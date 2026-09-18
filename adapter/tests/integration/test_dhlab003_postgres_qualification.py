"""DH-LAB-003/ADR-053: real PostgreSQL qualification of ``LabResultTemplateRepository.
activate_template``'s optimistic-concurrency compare-and-swap -- SQLite's whole-database locking
is not sufficient evidence for real PostgreSQL row-locking behavior under genuine concurrency,
matching the reasoning ``test_dhex011_postgres_qualification.py`` already established for
``record_collection``/``start_processing``, applied here to this ticket's own new
``FOR UPDATE``-locked discover-and-swap.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHLAB003_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

**Not executed this session** -- no Docker/PostgreSQL was reachable in this session's sandbox
(pulling ``postgres:16-alpine`` failed with a DNS/TLS timeout reaching Docker Hub's CDN; see
``PROJECT_STATE.md``'s DH-LAB-002/DH-LAB-003 entries for the full disclosure). Written to the same
standard as every other PG qualification file in this directory so a future session (or CI, if
ever wired up) can run it directly.

Usage::

    DHLAB003_PG_HOST=127.0.0.1 \\
    DHLAB003_PG_PORT=5432 \\
    DHLAB003_PG_USER=dhlab003 \\
    DHLAB003_PG_PASSWORD=dhlab003 \\
    DHLAB003_PG_DATABASE=dhlab003_qual \\
        pytest adapter/tests/integration/test_dhlab003_postgres_qualification.py -v

The target database must already have migrations applied through ``0024_result_templates``
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
from daher_adapter.exchange.lab_result_templates import (
    LabResultTemplateRepository,
    TemplateActivationConflict,
    TemplateObservationDefinitionInput,
)
from daher_adapter.exchange.models import LabResultTemplate
from daher_adapter.exchange.organizations import OrganizationRepository

PG_HOST = os.environ.get("DHLAB003_PG_HOST")
PG_PORT = os.environ.get("DHLAB003_PG_PORT")
PG_USER = os.environ.get("DHLAB003_PG_USER")
PG_PASSWORD = os.environ.get("DHLAB003_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHLAB003_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHLAB003_PG_* env vars) -- never in CI",
)

_DEMO_OBSERVATION = TemplateObservationDefinitionInput(
    source_code="DEMO_A",
    display_label="Demo Measure A",
    result_type="NUMERIC",
    numeric_unit="demo-unit",
    required=True,
    sort_order=0,
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_offering_with_two_templates(sessions: sessionmaker, *, key_prefix: str):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    templates = LabResultTemplateRepository(sessions)

    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    entry, _ = catalog.create_entry(code=f"{key_prefix}-TEST", display_name="Qualification Test")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    v1, _ = templates.create_template(
        lab_offering_id=offering.id, version=1, observations=[_DEMO_OBSERVATION]
    )
    v2_obs = TemplateObservationDefinitionInput(
        source_code="DEMO_B",
        display_label="Demo Measure B",
        result_type="NUMERIC",
        numeric_unit=None,
        required=True,
        sort_order=0,
    )
    v2, _ = templates.create_template(lab_offering_id=offering.id, version=2, observations=[v2_obs])
    return offering, v1, v2, templates


def test_pg_a1_two_concurrent_activations_of_different_targets_yield_one_winner(
    sessions: sessionmaker,
) -> None:
    """PG-A1: two threads simultaneously attempt to activate two *different* templates from the
    same expected-current state (no active template yet). Exactly one must win; the other must
    receive `TemplateActivationConflict` (never both silently succeeding, never two active rows)."""
    offering, v1, v2, templates = _seed_offering_with_two_templates(
        sessions, key_prefix=f"pga1-{uuid.uuid4().hex[:8]}"
    )

    results: list[tuple[str, object]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def activate(template_id) -> None:
        try:
            barrier.wait(timeout=15)
            record, outcome = templates.activate_template(
                lab_offering_id=offering.id,
                template_id=template_id,
                expected_current_template_id=None,
            )
            with lock:
                results.append(("ok", (record.id, outcome)))
        except TemplateActivationConflict:
            with lock:
                results.append(("conflict", None))
        except BaseException as error:  # noqa: BLE001
            with lock:
                results.append(("error", type(error).__name__))

    t1 = threading.Thread(target=activate, args=(v1.id,))
    t2 = threading.Thread(target=activate, args=(v2.id,))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert len(results) == 2
    outcomes = [r[0] for r in results]
    assert outcomes.count("ok") == 1, results
    assert outcomes.count("conflict") == 1, results

    with sessions() as session:
        active_rows = session.scalars(
            select(LabResultTemplate).where(
                LabResultTemplate.lab_offering_id == offering.id,
                LabResultTemplate.active.is_(True),
            )
        ).all()
    assert len(active_rows) == 1


def test_pg_a2_concurrent_identical_activation_converges_to_one_active_template(
    sessions: sessionmaker,
) -> None:
    """PG-A2: many threads call activate on the *same* template concurrently (some as the first
    real activation, some as a replay once it wins). Exactly one active row must exist afterward,
    and no `TemplateActivationConflict` is a permitted outcome here -- reactivating the exact
    target you already expected is always safe, matching `set_price`'s own already-correct-value
    exception."""
    offering, v1, _v2, templates = _seed_offering_with_two_templates(
        sessions, key_prefix=f"pga2-{uuid.uuid4().hex[:8]}"
    )

    results: list[tuple[str, object]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def activate() -> None:
        try:
            barrier.wait(timeout=15)
            record, outcome = templates.activate_template(
                lab_offering_id=offering.id,
                template_id=v1.id,
                expected_current_template_id=None,
            )
            with lock:
                results.append(("ok", (record.id, outcome)))
        except BaseException as error:  # noqa: BLE001
            with lock:
                results.append(("error", type(error).__name__))

    threads = [threading.Thread(target=activate) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 10
    assert all(outcome == "ok" for outcome, _ in results), results
    ids = {payload[0] for _outcome, payload in results}
    assert ids == {v1.id}

    with sessions() as session:
        active_count = session.scalar(
            select(func.count())
            .select_from(LabResultTemplate)
            .where(
                LabResultTemplate.lab_offering_id == offering.id,
                LabResultTemplate.active.is_(True),
            )
        )
    assert active_count == 1
