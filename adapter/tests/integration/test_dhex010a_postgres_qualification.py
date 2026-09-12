"""DH-EX-010A: real PostgreSQL qualification of the genuinely new race this ticket introduces --
``LabSelectionRepository.assign_by_provider`` racing ``PatientAccessGrantRepository.issue()`` for
the same order, both locking the same ``LabOrder`` row. SQLite (the threaded repository test in
``test_exchange_access_mode.py`` already covers the mechanism) is not sufficient evidence for
production PostgreSQL row-locking behavior -- matching the reasoning
``test_dhex010_postgres_qualification.py`` already established, applied here to this ticket's own
new interaction between the two repositories.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX010A_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX010A_PG_HOST=127.0.0.1 \\
    DHEX010A_PG_PORT=5432 \\
    DHEX010A_PG_USER=dhex010a \\
    DHEX010A_PG_PASSWORD=dhex010a \\
    DHEX010A_PG_DATABASE=dhex010a_qual \\
        pytest adapter/tests/integration/test_dhex010a_postgres_qualification.py -v

The target database must already have migrations applied through ``0013_exchange_access_mode``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import LabSelection, PatientAccessGrant
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patient_access import PatientAccessGrantRepository
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX010A_PG_HOST")
PG_PORT = os.environ.get("DHEX010A_PG_PORT")
PG_USER = os.environ.get("DHEX010A_PG_USER")
PG_PASSWORD = os.environ.get("DHEX010A_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX010A_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX010A_PG_* env vars) -- never in CI",
)

TTL_HOURS = 168


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_order_and_lab(sessions: sessionmaker, *, key_prefix: str):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)

    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    entry, _ = catalog.create_entry(code=f"{key_prefix}-TEST", display_name="Qualification Test")
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
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
    return provider, lab, order


def _run_one_race(
    sessions: sessionmaker,
    selections: LabSelectionRepository,
    access: PatientAccessGrantRepository,
    *,
    provider,
    lab,
    order,
) -> dict[str, object]:
    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def assign_attempt() -> None:
        try:
            barrier.wait(timeout=15)
            _record, created = selections.assign_by_provider(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                provider_organization_id=provider.id,
            )
            results["assign"] = ("ok", created)
        except BaseException as error:  # noqa: BLE001
            results["assign"] = ("error", type(error).__name__)

    def issue_attempt() -> None:
        try:
            barrier.wait(timeout=15)
            issued = access.issue(lab_order_id=order.id)
            results["issue"] = ("ok", issued.access_mode.value)
        except BaseException as error:  # noqa: BLE001
            results["issue"] = ("error", type(error).__name__)

    t_assign = threading.Thread(target=assign_attempt)
    t_issue = threading.Thread(target=issue_attempt)
    t_assign.start()
    t_issue.start()
    t_assign.join(timeout=30)
    t_issue.join(timeout=30)

    with sessions() as session:
        selection_count = session.scalar(
            select(func.count())
            .select_from(LabSelection)
            .where(LabSelection.lab_order_id == order.id)
        )
        grant = session.scalar(
            select(PatientAccessGrant).where(PatientAccessGrant.lab_order_id == order.id)
        )
    results["selection_count"] = selection_count
    results["grant_access_mode"] = grant.access_mode
    return results


def test_concurrent_assignment_and_issuance_race_yields_one_coherent_mode(
    sessions: sessionmaker,
) -> None:
    """Real PostgreSQL row-locking must serialize `assign_by_provider()` against `issue()` for the
    same order down to one coherent outcome: whichever commits first is what the other observes,
    proven here 5 consecutive times (matching this ticket's own PROJECT_STATE evidence
    discipline) -- never a torn state where a grant's persisted mode disagrees with whether a
    LabSelection actually existed at the moment issuance committed."""
    selections = LabSelectionRepository(sessions, OrganizationRepository(sessions))
    access = PatientAccessGrantRepository(sessions, ttl_hours=TTL_HOURS)

    for attempt in range(5):
        key_prefix = f"amrace-{uuid.uuid4().hex[:8]}"
        provider, lab, order = _seed_order_and_lab(sessions, key_prefix=key_prefix)

        results = _run_one_race(
            sessions, selections, access, provider=provider, lab=lab, order=order
        )

        assert results["assign"][0] == "ok", (attempt, results)
        assert results["issue"][0] == "ok", (attempt, results)
        assert results["selection_count"] == 1, (
            attempt,
            "exactly one LabSelection regardless of who won",
        )

        if results["issue"][1] == "LAB_SELECTION":
            assert results["assign"] == ("ok", True), (attempt, results)
        else:
            assert results["issue"][1] == "ORDER_READ", (attempt, results)
        assert results["grant_access_mode"] == results["issue"][1], (attempt, results)
