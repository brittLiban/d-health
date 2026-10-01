"""DHEALTH-LAB-OFFERINGS-001: real PostgreSQL qualification of laboratory service availability.

**Why this file has to exist.** The unit suite
(``tests/test_dhealthlabofferings001_offerings.py``, 25 tests) runs on SQLite, where the
three things this ticket's concurrency claims rest on are silently absent: SQLAlchemy's
SQLite dialect **omits ``FOR UPDATE`` entirely**, so the row lock this ticket *added* to
``set_offering_active`` is a no-op there and SQLite's whole-database writer serialization produces
the right answer for an unrelated
reason; ``PRAGMA foreign_keys`` is **off** in ``tests/conftest.py``; and SQLite does not behave like
an MVCC backend under contention. The lock fix is qualified **here**, or nowhere.

What is qualified here and nowhere else:

- **PG-OF-1a** The lock itself, deterministically. Two administrators enabling the same offering in
  overlapping transactions -> **one** ``"updated"`` outcome and one ``"unchanged"``, so exactly one
  ``lab_offering_status_changed`` audit row describes one state change. This is the test that
  reddens when ``.with_for_update()`` is removed, and it is written as two transactions the
  test owns rather than a thread barrier -- see its own docstring for why a barrier proved
  nothing here.
- **PG-OF-1b** The same thing under a real N-way barrier, as a smoke test, with its limits stated.
- **PG-OF-2** Enable racing disable -> a deterministic final value and never a lost update that
  leaves the row disagreeing with the audit trail.
- **PG-OF-3** A clinic's order assignment racing the laboratory disabling the analysis -> either the
  assignment committed first and stands, or it is refused with a controlled
  ``LabCannotFulfillOrder``. **Never** an order routed to a laboratory that had already stopped
  accepting the work, and never an ``IntegrityError`` surfacing as a 500.
- **PG-OF-4** The historical rule under the same contention: an order that *did* commit first is
  untouched by the disable that followed it.
- **PG-OF-5** ``uq_exchange_lab_offerings_lab_catalog`` enforced by the database, and
  ``create_offering``'s insert-first-wins recovery returning the existing row rather than
  leaking the ``IntegrityError``.
- **PG-OF-6** Tenant ownership: ``lab_organization_id`` is write-once in practice -- no code path
  mutates it -- and the foreign keys are enforced and ``RESTRICT``.
- **PG-OF-7** Migration state: exactly one head at ``0039_lab_specimens``, and the offering table's
  real constraint set. **This ticket adds no migration**, because the relationship already existed;
  this test is what makes that claim checkable rather than asserted in prose.

**The proof is the observable outcome, never an inspection of the lock.** Nothing here reads
``pg_locks``.

**Manual / opt-in only**, mirroring every other live-infra test in this directory: skipped by
default, including in CI, unless every ``DHOFF_PG_*`` variable is set.

Usage::

    DHOFF_PG_HOST=127.0.0.1 \\
    DHOFF_PG_PORT=55441 \\
    DHOFF_PG_USER=adapter \\
    DHOFF_PG_PASSWORD=... \\
    DHOFF_PG_DATABASE=dhoff_qual \\
        pytest adapter/tests/integration/test_dhealthlabofferings001_postgres_qualification.py -v

The target database must already be migrated to ``0039_lab_specimens``. **Never point it at a
facility database** -- nothing here checks that, so use a disposable instance and destroy it after.

All catalogue, patient and organization data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from decimal import Decimal

import pytest
from exchange_support import activate_result_template, make_lab, make_provider
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository, OfferingConflict
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import (
    LabOffering,
    LabOrder,
    LabOrderItem,
    LabOrderStatus,
    LabSelection,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.provider_lab_routes import ProviderLabRouteRepository
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHOFF_PG_HOST")
PG_PORT = os.environ.get("DHOFF_PG_PORT")
PG_USER = os.environ.get("DHOFF_PG_USER")
PG_PASSWORD = os.environ.get("DHOFF_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHOFF_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHOFF_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _prefix(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _seed(sessions: sessionmaker, *, key_prefix: str):
    """A laboratory offering one synthetic analysis, a clinic authorized to send to it, and an
    order.

    Every one of those four facts is separate and several tests below switch exactly one off."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)

    provider = make_provider(organizations, name=f"{key_prefix}-clinic")
    lab = make_lab(organizations, name=f"{key_prefix}-lab")
    entry, _ = catalog.create_entry(code=f"{key_prefix}-TEST", display_name="Analyse Qualification")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
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
    ProviderLabRouteRepository(sessions, organizations).upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
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
    selections = LabSelectionRepository(sessions, organizations)
    return provider, lab, entry, offering, order, catalog, selections


def _active(sessions, offering_id) -> bool:
    with sessions() as session:
        return session.scalar(select(LabOffering.active).where(LabOffering.id == offering_id))


# ── PG-OF-1a: the lock, deterministically ────────────────────────────────────────────────────────


def test_pg_of_1a_the_row_lock_decides_which_administrator_made_the_change(sessions: sessionmaker):
    """**The deterministic proof that ``set_offering_active``'s new ``FOR UPDATE`` is
    load-bearing.**

    ``outcome`` is not cosmetic: ``api.py`` writes it into the ``lab_offering_status_changed`` audit
    row, so it is what an administrator reads back to answer *"when was this analysis turned
    off, and by whom?"*. Deciding it from an unlocked read made that answer wrong -- two
    administrators acting at the same instant both saw ``active=False``, both decided they were the
    one making the change, and **two** audit rows described **one** switch.

    Written as two transactions the test owns, not a thread barrier, for the reason
    ``PG-OF-1b`` records: a barrier does not reliably produce the overlap, because the GIL plus
    per-session setup staggers the threads enough that each commits before the next reads. Here the
    first transaction is held open deliberately while the second contends for the same row.

    With the lock: the second blocks, then reads committed truth (``active=True``) and correctly
    reports ``"unchanged"``. Without it: the second proceeds on its stale read and reports
    ``"updated"``, which is the assertion that reddens."""
    _provider, _lab, _entry, offering, _order, catalog, _sel = _seed(
        sessions, key_prefix=_prefix("lockproof")
    )
    # Start from inactive, so "enable" is a real change both would claim to have made.
    catalog.set_offering_active(offering.id, False)
    assert _active(sessions, offering.id) is False

    started = threading.Event()
    release_first = threading.Event()
    outcomes: dict[str, object] = {}
    lock = threading.Lock()

    def hold() -> None:
        try:
            with sessions.begin() as session:
                _record, outcome = catalog.set_offering_active(offering.id, True, session=session)
                with lock:
                    outcomes["first"] = outcome
                started.set()
                # Commit happens when this block exits -- after the waiter has really contended.
                release_first.wait(timeout=20)
        except BaseException as error:  # noqa: BLE001
            started.set()
            with lock:
                outcomes["first"] = ("error", error)

    def contend() -> None:
        try:
            assert started.wait(timeout=20), "the first transaction never opened"
            with sessions.begin() as session:
                # Fail loudly rather than hang if the lock is never released.
                session.execute(text("SET LOCAL statement_timeout = '15s'"))
                _record, outcome = catalog.set_offering_active(offering.id, True, session=session)
                with lock:
                    outcomes["second"] = outcome
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes["second"] = ("error", type(error).__name__, str(error)[:200])

    holder = threading.Thread(target=hold)
    waiter = threading.Thread(target=contend)
    holder.start()
    waiter.start()
    assert started.wait(timeout=20)
    time.sleep(2.0)
    release_first.set()
    holder.join(timeout=40)
    waiter.join(timeout=40)

    assert outcomes.get("first") == "updated", outcomes.get("first")
    assert outcomes.get("second") == "unchanged", (
        f"the second administrator must observe committed truth and report 'unchanged' -- "
        f"without the FOR UPDATE row lock it decides from a stale read and claims 'updated', so "
        f"two audit rows describe one state change: {outcomes.get('second')!r}"
    )
    assert _active(sessions, offering.id) is True


def test_pg_of_1b_an_n_way_barrier_agrees_but_proves_less(sessions: sessionmaker):
    """The same claim under a real eight-thread barrier, kept as a smoke test with its limits said.

    **This test is not proof of the lock** and the docstring says so rather than letting a green run
    imply it: control-tested by deleting ``.with_for_update()``, a barrier-based version of this
    passed, because the threads stagger. ``PG-OF-1a`` is the one that reddens. What this adds is
    that eight concurrent writers produce no exception and one final value -- a different and weaker
    claim, worth having because it is the shape real traffic takes."""
    _provider, _lab, _entry, offering, _order, catalog, _sel = _seed(
        sessions, key_prefix=_prefix("barrier")
    )
    catalog.set_offering_active(offering.id, False)

    thread_count = 8
    barrier = threading.Barrier(thread_count)
    lock = threading.Lock()
    results: list[str] = []
    errors: list[BaseException] = []

    def attempt() -> None:
        try:
            barrier.wait(timeout=30)
            _record, outcome = catalog.set_offering_active(offering.id, True)
            with lock:
                results.append(outcome)
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], f"a concurrent enable must never raise: {errors!r}"
    assert len(results) == thread_count
    assert results.count("updated") == 1, (
        f"exactly one of {thread_count} may have made the change, got {results.count('updated')}"
    )
    assert _active(sessions, offering.id) is True


# ── PG-OF-2: enable racing disable ───────────────────────────────────────────────────────────────


def test_pg_of_2_enable_racing_disable_leaves_a_deterministic_row(sessions: sessionmaker):
    """One administrator enables while another disables, in overlapping transactions.

    Either order is legitimate -- this is two people disagreeing, not a bug -- and what must hold is
    that the stored value and the reported outcomes agree with each other. The loser must not report
    a change it did not make, and the final row must be exactly what the last committed writer set.
    """
    _provider, _lab, _entry, offering, _order, catalog, _sel = _seed(
        sessions, key_prefix=_prefix("enabledisable")
    )
    catalog.set_offering_active(offering.id, False)

    started = threading.Event()
    release_first = threading.Event()
    outcomes: dict[str, object] = {}
    lock = threading.Lock()

    def enabler() -> None:
        try:
            with sessions.begin() as session:
                _r, outcome = catalog.set_offering_active(offering.id, True, session=session)
                with lock:
                    outcomes["enable"] = outcome
                started.set()
                release_first.wait(timeout=20)
        except BaseException as error:  # noqa: BLE001
            started.set()
            with lock:
                outcomes["enable"] = ("error", error)

    def disabler() -> None:
        try:
            assert started.wait(timeout=20)
            with sessions.begin() as session:
                session.execute(text("SET LOCAL statement_timeout = '15s'"))
                _r, outcome = catalog.set_offering_active(offering.id, False, session=session)
                with lock:
                    outcomes["disable"] = outcome
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes["disable"] = ("error", type(error).__name__, str(error)[:200])

    threads = [threading.Thread(target=enabler), threading.Thread(target=disabler)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=20)
    time.sleep(1.5)
    release_first.set()
    for thread in threads:
        thread.join(timeout=40)

    # The enabler committed first (it held the lock), so it genuinely changed False -> True.
    assert outcomes.get("enable") == "updated", outcomes.get("enable")
    # The disabler then blocked, read True, and genuinely changed True -> False. Both are real
    # changes and both are truthfully audited -- which is the correct answer for two people
    # disagreeing, unlike two rows for one change.
    assert outcomes.get("disable") == "updated", outcomes.get("disable")
    assert _active(sessions, offering.id) is False, "the last committed writer decides the row"


# ── PG-OF-3 / PG-OF-4: the order-versus-disable race ─────────────────────────────────────────────


def test_pg_of_3_an_assignment_racing_a_disable_is_refused_or_stands_but_never_both(
    sessions: sessionmaker,
):
    """**The race the ticket singles out.** The clinic saw the laboratory as eligible at T1; the
    laboratory disabled the analysis; the clinic submits at T2.

    The disable is held open while the assignment contends, so the assignment really does arrive
    against an uncommitted change. Two outcomes are legitimate and both are checked for internal
    consistency:

    * the assignment got there first -> a ``LabSelection`` exists and the order is ``SELECTED``, and
      the disable that followed does not undo it (that is ``PG-OF-4``);
    * the disable committed first -> the assignment is refused with a **controlled**
      ``selection``-layer error, never an ``IntegrityError`` or an ``OperationalError``.

    What must never happen is an order routed to a laboratory whose offering was already inactive
    when the routing committed."""
    provider, lab, _entry, offering, order, catalog, selections = _seed(
        sessions, key_prefix=_prefix("orderrace")
    )

    started = threading.Event()
    release_disable = threading.Event()
    outcomes: dict[str, object] = {}
    lock = threading.Lock()

    def disable() -> None:
        try:
            with sessions.begin() as session:
                catalog.set_offering_active(offering.id, False, session=session)
                started.set()
                release_disable.wait(timeout=20)
            with lock:
                outcomes["disable"] = "committed"
        except BaseException as error:  # noqa: BLE001
            started.set()
            with lock:
                outcomes["disable"] = ("error", error)

    def assign() -> None:
        try:
            assert started.wait(timeout=20)
            with sessions.begin() as session:
                session.execute(text("SET LOCAL statement_timeout = '15s'"))
                selections.assign_by_provider(
                    lab_order_id=order.id,
                    lab_organization_id=lab.id,
                    provider_organization_id=provider.id,
                    session=session,
                )
            with lock:
                outcomes["assign"] = "assigned"
        except Exception as error:  # noqa: BLE001 -- the TYPE is the assertion below
            with lock:
                outcomes["assign"] = ("refused", type(error).__name__, str(error)[:200])

    threads = [threading.Thread(target=disable), threading.Thread(target=assign)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=20)
    time.sleep(1.5)
    release_disable.set()
    for thread in threads:
        thread.join(timeout=40)

    assert outcomes.get("disable") == "committed", outcomes.get("disable")
    assign_outcome = outcomes.get("assign")
    assert assign_outcome is not None, "the assignment never finished"

    with sessions() as session:
        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order.id)
        )
        status = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))

    if assign_outcome == "assigned":
        # It committed before the disable landed. Legitimate, and it must stand.
        assert selection is not None
        assert selection.lab_organization_id == lab.id
        assert status == LabOrderStatus.SELECTED.value
    else:
        kind, exception_name, detail = assign_outcome
        assert kind == "refused", assign_outcome
        # A controlled domain refusal, never a leaked database error.
        assert exception_name not in {
            "IntegrityError",
            "OperationalError",
            "ProgrammingError",
            "DataError",
        }, f"a legitimate race must not surface as a database error: {exception_name} {detail}"
        assert "LabCannotFulfillOrder" in exception_name or "Offering" in exception_name, (
            f"expected a selection-layer availability refusal, got {exception_name}: {detail}"
        )
        # And nothing was routed.
        assert selection is None
        assert status == LabOrderStatus.AWAITING_LAB_SELECTION.value

    # Either way the offering ended up disabled, and that is the laboratory's own truth.
    assert _active(sessions, offering.id) is False


def test_pg_of_3b_a_disable_that_commits_first_deterministically_refuses_the_assignment(
    sessions: sessionmaker,
):
    """The non-racing half, so the race above is not the only evidence. Disable, then assign: the
    refusal is deterministic and is a controlled domain error."""
    provider, lab, _entry, offering, order, catalog, selections = _seed(
        sessions, key_prefix=_prefix("ordersettled")
    )
    catalog.set_offering_active(offering.id, False)

    with pytest.raises(Exception) as caught:  # noqa: B017 -- the type is asserted below
        selections.assign_by_provider(
            lab_order_id=order.id,
            lab_organization_id=lab.id,
            provider_organization_id=provider.id,
        )
    name = type(caught.value).__name__
    assert name not in {"IntegrityError", "OperationalError", "DataError"}, name

    with sessions() as session:
        assert (
            session.scalar(select(LabSelection).where(LabSelection.lab_order_id == order.id))
            is None
        )


def test_pg_of_4_an_order_that_committed_first_survives_the_disable_untouched(
    sessions: sessionmaker,
):
    """The deactivation rule against a real backend: past work is not current configuration.

    The order is routed, then the analysis is disabled. The selection stays, the status stays, and
    the item's frozen code and display-name snapshots are byte-for-byte unchanged -- nothing
    reinterprets a historical order from today's configuration."""
    provider, lab, entry, offering, order, catalog, selections = _seed(
        sessions, key_prefix=_prefix("historical")
    )
    selections.assign_by_provider(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        provider_organization_id=provider.id,
    )

    with sessions() as session:
        before_items = session.scalars(
            select(LabOrderItem).where(LabOrderItem.lab_order_id == order.id)
        ).all()
        before = [
            (i.catalog_entry_id, i.catalog_code_snapshot, i.catalog_display_name_snapshot)
            for i in before_items
        ]

    catalog.set_offering_active(offering.id, False)

    with sessions() as session:
        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order.id)
        )
        status = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))
        after_items = session.scalars(
            select(LabOrderItem).where(LabOrderItem.lab_order_id == order.id)
        ).all()
        after = [
            (i.catalog_entry_id, i.catalog_code_snapshot, i.catalog_display_name_snapshot)
            for i in after_items
        ]

    assert selection is not None, "disabling an offering must not detach a routed order"
    assert selection.lab_organization_id == lab.id
    assert status == LabOrderStatus.SELECTED.value
    assert after == before
    assert after[0][0] == entry.id
    assert _active(sessions, offering.id) is False


# ── PG-OF-5: uniqueness, enforced by the database ────────────────────────────────────────────────


def test_pg_of_5_a_duplicate_offering_is_refused_by_the_database_not_only_the_service(
    sessions: sessionmaker,
):
    """``uq_exchange_lab_offerings_lab_catalog`` is the real guard, and ``create_offering``'s
    insert-first-wins recovery is what turns the ``IntegrityError`` into a usable answer.

    A direct second insert raises. Through the repository, the same collision returns the existing
    row with ``created=False`` -- and a *different* ``local_test_code`` for the same pair is an
    explicit ``OfferingConflict`` rather than a silently ignored difference."""
    from sqlalchemy.exc import IntegrityError

    _provider, lab, entry, offering, _order, catalog, _sel = _seed(
        sessions, key_prefix=_prefix("dupe")
    )

    with pytest.raises(IntegrityError), sessions() as session:
        session.add(LabOffering(lab_organization_id=lab.id, catalog_entry_id=entry.id))
        session.commit()

    replay, created = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    assert created is False
    assert replay.id == offering.id

    with pytest.raises(OfferingConflict):
        catalog.create_offering(
            lab_organization_id=lab.id, catalog_entry_id=entry.id, local_test_code="LOCAL-X"
        )

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(LabOffering)
            .where(
                LabOffering.lab_organization_id == lab.id,
                LabOffering.catalog_entry_id == entry.id,
            )
        )
    assert count == 1


def test_pg_of_5b_two_laboratories_may_offer_the_same_analysis(sessions: sessionmaker):
    """The uniqueness is per laboratory, which is the whole point of the model: the same canonical
    analysis is offered by many laboratories and each one's availability is its own fact."""
    _p1, lab_a, entry, _o1, _ord, catalog, _s = _seed(sessions, key_prefix=_prefix("shared"))
    organizations = OrganizationRepository(sessions)
    lab_b = make_lab(organizations, name=_prefix("otherlab"))

    other, created = catalog.create_offering(
        lab_organization_id=lab_b.id, catalog_entry_id=entry.id
    )
    assert created is True
    assert other.lab_organization_id == lab_b.id

    # And disabling one laboratory's offering leaves the other's alone.
    catalog.set_offering_active(other.id, False)
    with sessions() as session:
        rows = {
            row.lab_organization_id: row.active
            for row in session.scalars(
                select(LabOffering).where(LabOffering.catalog_entry_id == entry.id)
            ).all()
        }
    assert rows[lab_a.id] is True
    assert rows[lab_b.id] is False


# ── PG-OF-6: ownership and referential integrity ─────────────────────────────────────────────────


def test_pg_of_6_the_offerings_foreign_keys_are_enforced_and_restrict(sessions: sessionmaker):
    """Both foreign keys, against the backend that actually enforces them -- the unit suite runs
    with ``PRAGMA foreign_keys`` off and would accept either of these.

    ``RESTRICT`` is then checked by deleting the catalogue entry, which must be refused while a
    laboratory still offers it: a dangling offering would be a service nobody can identify."""
    from sqlalchemy.exc import IntegrityError

    _provider, lab, entry, _offering, _order, _catalog, _sel = _seed(
        sessions, key_prefix=_prefix("fks")
    )
    from daher_adapter.exchange.models import TestCatalogEntry

    with pytest.raises(IntegrityError), sessions() as session:
        session.add(LabOffering(lab_organization_id=uuid.uuid4(), catalog_entry_id=entry.id))
        session.commit()

    with pytest.raises(IntegrityError), sessions() as session:
        session.add(LabOffering(lab_organization_id=lab.id, catalog_entry_id=uuid.uuid4()))
        session.commit()

    with pytest.raises(IntegrityError), sessions() as session:
        session.execute(TestCatalogEntry.__table__.delete().where(TestCatalogEntry.id == entry.id))
        session.commit()


def test_pg_of_6b_no_code_path_moves_an_offering_to_another_laboratory(sessions: sessionmaker):
    """Ownership is write-once in practice. ``CatalogRepository`` exposes create, read and
    ``set_offering_active`` -- and nothing that reassigns ``lab_organization_id``.

    Asserted over the repository's own surface rather than by trying to call something that does not
    exist: an offering that could be moved between laboratories would carry its price history, its
    result templates and its availability to an organization that never configured any of them."""
    mutators = [
        name
        for name in dir(CatalogRepository)
        if not name.startswith("_") and callable(getattr(CatalogRepository, name))
    ]
    assert "set_offering_active" in mutators
    for forbidden in (
        "move_offering",
        "set_offering_lab",
        "reassign_offering",
        "set_offering_organization",
        "update_offering",
        "delete_offering",
    ):
        assert forbidden not in mutators, (
            f"CatalogRepository.{forbidden} would let an offering change hands or be rewritten"
        )


# ── PG-OF-7: migration state ─────────────────────────────────────────────────────────────────────


def test_pg_of_7_this_ticket_adds_no_migration_and_the_schema_already_supports_it(
    sessions: sessionmaker,
):
    """**This ticket adds no migration**, because the facility-offering relationship already
    existed. This test is what makes that claim checkable instead of prose.

    The head is read from the live database, and the offering table's real constraint set is read
    from PostgreSQL's own catalogue rather than from the SQLAlchemy model -- a model and a database
    that disagree is exactly the thing a migration test exists to catch."""
    with sessions() as session:
        head = session.scalar(text("SELECT version_num FROM alembic_version"))
        heads = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    assert head == "0039_lab_specimens", f"expected the specimen head, found {head!r}"
    assert len(heads) == 1, f"exactly one Alembic head, found {heads}"

    with sessions() as session:
        constraints = {
            (row[0], row[1])
            for row in session.execute(
                text(
                    "SELECT conname, contype FROM pg_constraint "
                    "WHERE conrelid = 'exchange_lab_offerings'::regclass"
                )
            ).all()
        }
        columns = {
            row[0]: row[1]
            for row in session.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'exchange_lab_offerings'"
                )
            ).all()
        }

    names = {name for name, _ in constraints}
    assert "uq_exchange_lab_offerings_lab_catalog" in names
    # Two foreign keys and no more: the organization and the catalogue entry.
    assert len([c for c in constraints if c[1] == "f"]) == 2

    # The column set is the model's, and carries nothing this ticket refused to add.
    assert set(columns) == {
        "id",
        "lab_organization_id",
        "catalog_entry_id",
        "local_test_code",
        "active",
        "created_at",
    }
    assert columns["active"] == "NO", "availability must not be nullable"
    assert columns["local_test_code"] == "YES", "the laboratory's own alias is optional"
    for absent in ("updated_at", "changed_by", "changed_by_identity_id", "specimen_type", "amount"):
        assert absent not in columns
