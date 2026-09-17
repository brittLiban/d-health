"""DH-CLINIC-006 §20/§55: real-PostgreSQL concurrent provider-lab-assignment qualification.

Opt-in only, against a disposable database migrated through
``0023_exchange_provider_lab_routes``. SQLite does not enforce ``SELECT ... FOR UPDATE`` row
locking the same way real PostgreSQL does, which is exactly what these tests need to prove:

- PG1/PG2: two clinicians concurrently assigning the *same* order to the *same* lab converge to
  exactly one ``LabSelection`` row, one ``SELECTED`` order, and both callers resolve consistently
  (one ``created=True``, the other ``created=False`` for the identical record) -- never two rows,
  never a lost update.
- PG3/PG4: two clinicians concurrently assigning the *same* order to two *different* labs produce
  exactly one winner and one ``DifferentLabAlreadySelected`` loser -- never two ``LabSelection``
  rows, never last-write-wins, never a silent reassignment.
- PG5: a route deactivation genuinely races a concurrent assignment attempt (the ``LabOrder`` row
  lock ``assign_by_provider`` takes does not serialize against ``ProviderLabRouteRepository.
  upsert``'s own separate lock on the route row) -- but PostgreSQL's READ COMMITTED isolation still
  hands the route read a consistent, genuinely-committed view, so the outcome is always one of
  exactly two truthful possibilities, never a torn state: either the assignment reads the route as
  active and succeeds cleanly (and a route deactivated afterward never disturbs the resulting
  ``LabSelection``, per §11), or it reads the route as already inactive and fails closed with
  ``ProviderLabRouteNotActive`` -- in neither case does more than one ``LabSelection`` row exist.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import LabOrder, LabSelection
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.provider_lab_routes import ProviderLabRouteRepository
from daher_adapter.exchange.selection import (
    DifferentLabAlreadySelected,
    LabSelectionRepository,
    ProviderLabRouteNotActive,
)

PG_HOST = os.environ.get("DHCLINIC006_PG_HOST")
PG_PORT = os.environ.get("DHCLINIC006_PG_PORT")
PG_USER = os.environ.get("DHCLINIC006_PG_USER")
PG_PASSWORD = os.environ.get("DHCLINIC006_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHCLINIC006_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a disposable PostgreSQL database (DHCLINIC006_PG_*) -- never in CI-less runs",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _build(sessions: sessionmaker):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    provider_lab_routes = ProviderLabRouteRepository(sessions, organizations)
    return (
        organizations,
        catalog,
        directory,
        patients,
        practitioners,
        orders,
        selections,
        provider_lab_routes,
    )


def _accepting(directory: LabDirectoryProfileRepository, lab_id) -> None:
    directory.upsert(
        lab_organization_id=lab_id,
        public_address="PG Qualification Lab",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )


def _seed_order(prefix: str, organizations, catalog, directory, patients, practitioners, orders):
    provider = organizations.create(org_type="PROVIDER", name=f"{prefix}-provider")
    entry, _ = catalog.create_entry(code=f"{prefix}-TEST", display_name=f"{prefix} test")
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhclinic006-qualification",
        source_identity_system=f"urn:{prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhclinic006-qualification",
        source_identity_system=f"urn:{prefix}:practitioner",
        source_identity_value="D-1",
    )
    order = orders.create(
        idempotency_key=f"{prefix}-key",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order
    return provider, order, entry


def test_pg1_concurrent_same_lab_assignment_converges_to_one_selection(
    sessions: sessionmaker,
) -> None:
    prefix = f"pg006-1-{uuid.uuid4().hex[:8]}"
    organizations, catalog, directory, patients, practitioners, orders, selections, routes = _build(
        sessions
    )
    provider, order, entry = _seed_order(
        prefix, organizations, catalog, directory, patients, practitioners, orders
    )
    lab = organizations.create(org_type="LAB", name=f"{prefix}-lab")
    _accepting(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    routes.upsert(provider_organization_id=provider.id, lab_organization_id=lab.id, active=True)

    results: list = []
    exceptions: list = []
    barrier = threading.Barrier(5)

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            with sessions() as session:
                record, created = selections.assign_by_provider(
                    lab_order_id=order.id,
                    lab_organization_id=lab.id,
                    provider_organization_id=provider.id,
                    session=session,
                )
                session.commit()
                results.append((record.id, created))
        except BaseException as error:  # noqa: BLE001
            exceptions.append(error)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "PG1 deadlocked"
    assert exceptions == [], f"PG1 worker exceptions: {exceptions!r}"
    assert len(results) == 5

    created_count = sum(1 for _id, created in results if created)
    assert created_count == 1, "exactly one caller must create the selection"
    selection_ids = {selection_id for selection_id, _created in results}
    assert len(selection_ids) == 1, "every caller must resolve to the same LabSelection"

    with sessions() as session:
        rows = session.scalars(
            select(LabSelection).where(LabSelection.lab_order_id == order.id)
        ).all()
        assert len(rows) == 1
        order_row = session.get(LabOrder, order.id)
        assert order_row.status == "SELECTED"


def test_pg2_concurrent_different_lab_assignment_has_exactly_one_winner(
    sessions: sessionmaker,
) -> None:
    prefix = f"pg006-2-{uuid.uuid4().hex[:8]}"
    organizations, catalog, directory, patients, practitioners, orders, selections, routes = _build(
        sessions
    )
    provider, order, entry = _seed_order(
        prefix, organizations, catalog, directory, patients, practitioners, orders
    )
    lab_a = organizations.create(org_type="LAB", name=f"{prefix}-lab-a")
    lab_b = organizations.create(org_type="LAB", name=f"{prefix}-lab-b")
    for lab in (lab_a, lab_b):
        _accepting(directory, lab.id)
        catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        routes.upsert(provider_organization_id=provider.id, lab_organization_id=lab.id, active=True)

    results: list = []
    exceptions: list = []
    barrier = threading.Barrier(2)

    def worker(lab_id) -> None:
        try:
            barrier.wait(timeout=10)
            with sessions() as session:
                record, created = selections.assign_by_provider(
                    lab_order_id=order.id,
                    lab_organization_id=lab_id,
                    provider_organization_id=provider.id,
                    session=session,
                )
                session.commit()
                results.append(("ok", record.lab_organization_id, created))
        except DifferentLabAlreadySelected:
            results.append(("conflict", None, None))
        except BaseException as error:  # noqa: BLE001
            exceptions.append(error)

    threads = [
        threading.Thread(target=worker, args=(lab_a.id,)),
        threading.Thread(target=worker, args=(lab_b.id,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "PG2 deadlocked"
    assert exceptions == [], f"PG2 worker exceptions: {exceptions!r}"
    assert len(results) == 2

    winners = [r for r in results if r[0] == "ok"]
    losers = [r for r in results if r[0] == "conflict"]
    assert len(winners) == 1, "exactly one caller must win"
    assert len(losers) == 1, "the other caller must get a clean conflict, never a silent overwrite"

    with sessions() as session:
        rows = session.scalars(
            select(LabSelection).where(LabSelection.lab_order_id == order.id)
        ).all()
        assert len(rows) == 1, "never two LabSelection rows for one order"
        assert rows[0].lab_organization_id == winners[0][1]


def test_pg5_route_deactivation_racing_assignment_resolves_truthfully(
    sessions: sessionmaker,
) -> None:
    """Either the assignment wins the race (succeeds, creating exactly one LabSelection, and the
    later-committing deactivation never touches it -- §11), or the deactivation wins (the
    assignment fails closed with ``ProviderLabRouteNotActive`` and zero LabSelection rows exist).
    No other outcome -- no exception of any other type, no torn state, never both "succeeded" and
    "refused" simultaneously true."""
    prefix = f"pg006-5-{uuid.uuid4().hex[:8]}"
    organizations, catalog, directory, patients, practitioners, orders, selections, routes = _build(
        sessions
    )
    provider, order, entry = _seed_order(
        prefix, organizations, catalog, directory, patients, practitioners, orders
    )
    lab = organizations.create(org_type="LAB", name=f"{prefix}-lab")
    _accepting(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    routes.upsert(provider_organization_id=provider.id, lab_organization_id=lab.id, active=True)

    outcome: dict[str, object] = {}
    exceptions: list = []
    barrier = threading.Barrier(2)

    def assign_worker() -> None:
        try:
            barrier.wait(timeout=10)
            with sessions() as session:
                record, created = selections.assign_by_provider(
                    lab_order_id=order.id,
                    lab_organization_id=lab.id,
                    provider_organization_id=provider.id,
                    session=session,
                )
                session.commit()
                outcome["assign"] = ("ok", record.id, created)
        except ProviderLabRouteNotActive:
            outcome["assign"] = ("refused", None, None)
        except BaseException as error:  # noqa: BLE001
            exceptions.append(error)

    def deactivate_worker() -> None:
        try:
            barrier.wait(timeout=10)
            routes.upsert(
                provider_organization_id=provider.id, lab_organization_id=lab.id, active=False
            )
            outcome["deactivate"] = "ok"
        except BaseException as error:  # noqa: BLE001
            exceptions.append(error)

    threads = [threading.Thread(target=assign_worker), threading.Thread(target=deactivate_worker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "PG5 deadlocked"
    assert exceptions == [], f"PG5 worker exceptions: {exceptions!r}"
    assert outcome["deactivate"] == "ok"
    assign_result = outcome["assign"]
    assert assign_result[0] in ("ok", "refused")

    with sessions() as session:
        rows = session.scalars(
            select(LabSelection).where(LabSelection.lab_order_id == order.id)
        ).all()
        if assign_result[0] == "ok":
            assert len(rows) == 1, "a winning assignment must leave exactly one LabSelection"
            assert rows[0].id == assign_result[1]
            order_row = session.get(LabOrder, order.id)
            assert order_row.status == "SELECTED", (
                "the historical assignment must survive the later route deactivation (§11)"
            )
        else:
            assert len(rows) == 0, "a refused assignment must leave zero LabSelection rows"
            order_row = session.get(LabOrder, order.id)
            assert order_row.status == "AWAITING_LAB_SELECTION"
