"""DH-EX-010: real PostgreSQL qualification of provider-assigned lab routing's concurrency claims
-- same-lab concurrent provider assignments, different-lab concurrent provider assignments, and a
patient-vs-provider race for the same order. SQLite (the threaded HTTP/repository tests already
cover the mechanism) is not sufficient evidence for production PostgreSQL row-locking behavior --
matching the reasoning ``test_dhex001r_postgres_qualification.py`` and
``test_dhex009_postgres_qualification.py`` already established, applied here to
``LabSelectionRepository.assign_by_provider`` and its interaction with ``select()``.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX010_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX010_PG_HOST=127.0.0.1 \\
    DHEX010_PG_PORT=5432 \\
    DHEX010_PG_USER=dhex010 \\
    DHEX010_PG_PASSWORD=dhex010 \\
    DHEX010_PG_DATABASE=dhex010_qual \\
        pytest adapter/tests/integration/test_dhex010_postgres_qualification.py -v

The target database must already have migrations applied through ``0012_exchange_provider_assign``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import LabOrderStatus, LabSelection, PatientAccessGrant
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.selection import LabAlreadyAssignedByProvider, LabSelectionRepository
from daher_adapter.models import utc_now

PG_HOST = os.environ.get("DHEX010_PG_HOST")
PG_PORT = os.environ.get("DHEX010_PG_PORT")
PG_USER = os.environ.get("DHEX010_PG_USER")
PG_PASSWORD = os.environ.get("DHEX010_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX010_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX010_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_order(sessions: sessionmaker, key_prefix: str):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)

    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")
    entry, _ = catalog.create_entry(code=f"{key_prefix}-TEST", display_name="Qualification Test")
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
    return organizations, catalog, orders, provider, order, entry


def _make_lab(organizations, catalog, directory, *, key_prefix: str, entry_id, listed=False):
    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=listed,
        accepting_orders=True,
    )
    return lab


def test_ten_concurrent_identical_provider_assignments_yield_exactly_one_selection(
    sessions: sessionmaker,
) -> None:
    key_prefix = f"pa10same-{uuid.uuid4().hex[:8]}"
    organizations, catalog, orders, provider, order, entry = _seed_order(sessions, key_prefix)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    selections = LabSelectionRepository(sessions, organizations)
    lab = _make_lab(organizations, catalog, directory, key_prefix=key_prefix, entry_id=entry.id)

    outcomes: dict[int, bool] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            _record, created = selections.assign_by_provider(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                provider_organization_id=provider.id,
            )
            with lock:
                outcomes[i] = created
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected exceptions from concurrent assignments: {errors!r}"
    assert sorted(outcomes.values()) == [False] * 9 + [True]

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(LabSelection)
            .where(LabSelection.lab_order_id == order.id)
        )
    assert count == 1, "real PostgreSQL must permit exactly one LabSelection for this order"
    refreshed = orders.get(order.id)
    assert refreshed.status == LabOrderStatus.SELECTED.value
    assert refreshed.items[0].selected_price_amount is None


def test_concurrent_different_lab_provider_assignments_yield_one_winner(
    sessions: sessionmaker,
) -> None:
    key_prefix = f"paxlab-{uuid.uuid4().hex[:8]}"
    organizations, catalog, orders, provider, order, entry = _seed_order(sessions, key_prefix)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    selections = LabSelectionRepository(sessions, organizations)
    lab_a = _make_lab(
        organizations, catalog, directory, key_prefix=f"{key_prefix}-a", entry_id=entry.id
    )
    lab_b = _make_lab(
        organizations, catalog, directory, key_prefix=f"{key_prefix}-b", entry_id=entry.id
    )

    outcomes: dict[str, tuple[bool, uuid.UUID] | None] = {"a": None, "b": None}
    errors: dict[str, BaseException] = {}
    barrier = threading.Barrier(2)

    def attempt(label: str, lab_id: uuid.UUID) -> None:
        try:
            barrier.wait(timeout=15)
            record, created = selections.assign_by_provider(
                lab_order_id=order.id,
                lab_organization_id=lab_id,
                provider_organization_id=provider.id,
            )
            outcomes[label] = (created, record.lab_organization_id)
        except BaseException as error:  # noqa: BLE001
            errors[label] = error

    t_a = threading.Thread(target=attempt, args=("a", lab_a.id))
    t_b = threading.Thread(target=attempt, args=("b", lab_b.id))
    t_a.start()
    t_b.start()
    t_a.join(timeout=30)
    t_b.join(timeout=30)

    # Exactly one of the two must have won (created the selection); the other must have either
    # observed the winner's lab as an existing same-lab record (impossible here, different labs)
    # or raised a conflict -- never both silently "succeeding" for different labs.
    successes = {k: v for k, v in outcomes.items() if v is not None}
    assert len(successes) == 1, f"exactly one call may succeed for two different labs: {outcomes!r}"
    assert len(errors) == 1, (
        f"the loser must observe a conflict, not silently do nothing: {errors!r}"
    )

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(LabSelection)
            .where(LabSelection.lab_order_id == order.id)
        )
    assert count == 1


def test_concurrent_patient_and_provider_assignment_yields_one_coherent_selection(
    sessions: sessionmaker,
) -> None:
    """Real PostgreSQL row-locking must serialize a patient's `select()` call against a
    provider's `assign_by_provider()` call for the *same* order down to one winner, with the
    loser observing the committed truth rather than corrupting or duplicating it."""
    key_prefix = f"paxpat-{uuid.uuid4().hex[:8]}"
    organizations, catalog, orders, provider, order, entry = _seed_order(sessions, key_prefix)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    selections = LabSelectionRepository(sessions, organizations)
    lab = _make_lab(
        organizations, catalog, directory, key_prefix=key_prefix, entry_id=entry.id, listed=True
    )
    offering_id = catalog.list_offerings_for_lab(lab.id)[0].id
    price, _outcome = catalog.set_price(
        offering_id=offering_id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )

    grant_id = uuid.uuid4()
    with sessions() as session:
        session.add(
            PatientAccessGrant(
                id=grant_id,
                lab_order_id=order.id,
                # `token_hash` is globally unique -- derive it from `key_prefix` (already a fresh
                # uuid4-suffixed value per test run) rather than a fixed literal, which collided
                # across repeated runs against the same persistent scratch database.
                token_hash=uuid.uuid5(uuid.NAMESPACE_URL, key_prefix).hex + "0" * 32,
                expires_at=utc_now() + timedelta(hours=1),
            )
        )
        session.commit()

    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def provider_attempt() -> None:
        try:
            barrier.wait(timeout=15)
            record, created = selections.assign_by_provider(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                provider_organization_id=provider.id,
            )
            results["provider"] = ("ok", created, record.selected_via_patient_access_grant_id)
        except BaseException as error:  # noqa: BLE001
            results["provider"] = ("error", type(error).__name__, None)

    def patient_attempt() -> None:
        try:
            barrier.wait(timeout=15)
            record, created = selections.select(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                expected_price_ids=frozenset({price.id}),
                patient_access_grant_id=grant_id,
            )
            results["patient"] = ("ok", created, record.selected_via_patient_access_grant_id)
        except LabAlreadyAssignedByProvider:
            results["patient"] = ("provider_won", None, None)
        except BaseException as error:  # noqa: BLE001
            results["patient"] = ("error", type(error).__name__, None)

    t_provider = threading.Thread(target=provider_attempt)
    t_patient = threading.Thread(target=patient_attempt)
    t_provider.start()
    t_patient.start()
    t_provider.join(timeout=30)
    t_patient.join(timeout=30)

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(LabSelection)
            .where(LabSelection.lab_order_id == order.id)
        )
    assert count == 1, "exactly one LabSelection must exist regardless of who won the race"

    provider_outcome = results["provider"]
    patient_outcome = results["patient"]
    assert provider_outcome[0] in ("ok", "error"), provider_outcome
    assert patient_outcome[0] in ("ok", "provider_won", "error"), patient_outcome
    # Whichever won, the final committed row's provenance must be internally coherent: if the
    # patient's own call observed `created=True`, the row is patient-provenanced; if the provider
    # observed `created=True` (or the patient's call was refused as `provider_won`), the row must
    # never carry the patient's grant id.
    with sessions() as session:
        final = session.scalar(select(LabSelection).where(LabSelection.lab_order_id == order.id))
    if patient_outcome[0] == "ok" and patient_outcome[1] is True:
        assert final.selected_via_patient_access_grant_id == grant_id
    else:
        assert final.selected_via_patient_access_grant_id is None
