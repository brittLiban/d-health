"""DH-EX-009: real PostgreSQL qualification of provider-scoped patient-reference binding's
concurrency and organization-scoping claims. SQLite (the threaded unit test already covers the
insert-first-wins mechanism) is not sufficient evidence for production PostgreSQL row-locking
behavior -- matching the reasoning ``test_dhex001r_postgres_qualification.py`` and
``test_dhex007_postgres_qualification.py`` already established, applied here to
``PatientReferenceRepository.get_or_create_with_outcome``.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX009_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX009_PG_HOST=127.0.0.1 \\
    DHEX009_PG_PORT=5432 \\
    DHEX009_PG_USER=dhex009 \\
    DHEX009_PG_PASSWORD=dhex009 \\
    DHEX009_PG_DATABASE=dhex009_qual \\
        pytest adapter/tests/integration/test_dhex009_postgres_qualification.py -v

The target database must already have migrations applied through ``0011_exchange_patient_ref_bind``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import PatientReference
from daher_adapter.exchange.organizations import OrganizationRepository

PG_HOST = os.environ.get("DHEX009_PG_HOST")
PG_PORT = os.environ.get("DHEX009_PG_PORT")
PG_USER = os.environ.get("DHEX009_PG_USER")
PG_PASSWORD = os.environ.get("DHEX009_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX009_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX009_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_ten_concurrent_identical_binds_yield_exactly_one_reference(
    sessions: sessionmaker,
) -> None:
    key_prefix = f"pg9same-{uuid.uuid4().hex[:8]}"
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")

    outcomes: dict[int, bool] = {}
    resolved_ids: dict[int, uuid.UUID] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            record, created = patients.get_or_create_with_outcome(
                source_organization_id=provider.id,
                source_system="pg-qualification",
                source_identity_system=f"urn:{key_prefix}:patient",
                source_identity_value="P-1",
            )
            with lock:
                outcomes[i] = created
                resolved_ids[i] = record.id
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected exceptions from concurrent binds: {errors!r}"
    assert len(outcomes) == 10
    assert sorted(outcomes.values()) == [False] * 9 + [True], (
        "exactly one concurrent attempt must have created the reference and the other nine must "
        f"have resolved to the existing one, got {outcomes!r}"
    )
    assert len(set(resolved_ids.values())) == 1, (
        "every successful caller -- winner and losers alike -- must resolve to the same UUID"
    )

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(PatientReference)
            .where(
                PatientReference.source_organization_id == provider.id,
                PatientReference.source_identity_system == f"urn:{key_prefix}:patient",
                PatientReference.source_identity_value == "P-1",
            )
        )
    assert count == 1, "real PostgreSQL's UNIQUE constraint must permit exactly one row"


def test_cross_organization_same_raw_identity_yields_distinct_references_under_real_postgres(
    sessions: sessionmaker,
) -> None:
    key_prefix = f"pgxorg-{uuid.uuid4().hex[:8]}"
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    provider_a = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic-a")
    provider_b = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic-b")

    from_a, created_a = patients.get_or_create_with_outcome(
        source_organization_id=provider_a.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="123",
    )
    from_b, created_b = patients.get_or_create_with_outcome(
        source_organization_id=provider_b.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="123",
    )

    assert created_a is True
    assert created_b is True
    assert from_a.id != from_b.id, (
        "two different provider organizations reusing the same raw identity string under real "
        "PostgreSQL must never be silently merged into one PatientReference"
    )
