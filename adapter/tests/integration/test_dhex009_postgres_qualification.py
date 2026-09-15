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


def test_postgres_patient_search_strictly_isolated_to_organization(
    sessions: sessionmaker,
) -> None:
    """DH-CLINIC-002A real-PostgreSQL qualification: patient search is strictly provider-scoped."""
    key_prefix = f"pgiso-{uuid.uuid4().hex[:8]}"
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    provider_a = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic-a")
    provider_b = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic-b")

    patient_a, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider_a.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-A1",
        display_name="Hodan Ali",
        contact_channel_type="PHONE",
        contact_value="+25377000001",
    )
    patient_b, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider_b.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-B1",
        display_name="Hodan Farah",
        contact_channel_type="PHONE",
        contact_value="+25377000002",
    )

    results_a = patients.list_for_organization(provider_a.id, query="Hodan")
    assert len(results_a) == 1
    assert results_a[0].id == patient_a.id
    assert results_a[0].display_name == "Hodan Ali"

    results_b = patients.list_for_organization(provider_b.id, query="Hodan")
    assert len(results_b) == 1
    assert results_b[0].id == patient_b.id
    assert results_b[0].display_name == "Hodan Farah"

    # Listing without query also respects tenant boundary under real PostgreSQL
    list_all_a = patients.list_for_organization(provider_a.id)
    assert any(p.id == patient_a.id for p in list_all_a)
    assert not any(p.id == patient_b.id for p in list_all_a)


def test_postgres_patient_search_case_insensitive_substring(
    sessions: sessionmaker,
) -> None:
    """DH-CLINIC-002A real-PostgreSQL qualification: search matches substrings case-insensitively
    across display_name, contact_value, and source_identity_value."""
    key_prefix = f"pgci-{uuid.uuid4().hex[:8]}"
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")

    patient, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="PAT-X99",
        display_name="Fatouma Mohamed",
        contact_channel_type="PHONE",
        contact_value="+25321350000",
    )

    # Lowercase substring in display_name
    match_lower = patients.list_for_organization(provider.id, query="fatouma")
    assert any(p.id == patient.id for p in match_lower)

    # Uppercase substring in display_name
    match_upper = patients.list_for_organization(provider.id, query="MOHAMED")
    assert any(p.id == patient.id for p in match_upper)

    # Substring in contact_value
    match_contact = patients.list_for_organization(provider.id, query="2135")
    assert any(p.id == patient.id for p in match_contact)

    # Substring in source_identity_value
    match_ident = patients.list_for_organization(provider.id, query="pat-x")
    assert any(p.id == patient.id for p in match_ident)

    # Non-matching string
    no_match = patients.list_for_organization(provider.id, query="Amina")
    assert not any(p.id == patient.id for p in no_match)


def test_postgres_patient_search_literal_wildcards_under_real_postgres(
    sessions: sessionmaker,
) -> None:
    """DH-CLINIC-002A real-PostgreSQL qualification: %, _, and \\ are treated as literal characters,
    not SQL wildcards."""
    key_prefix = f"pgwild-{uuid.uuid4().hex[:8]}"
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")

    p_pct, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="ID-PCT",
        display_name="Test%Percent",
    )
    p_xpct, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="ID-XPCT",
        display_name="TestXPercent",
    )
    p_und, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="ID-UND",
        display_name="Test_Underscore",
    )
    p_xund, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="ID-XUND",
        display_name="TestXUnderscore",
    )
    p_bs, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="ID-BS",
        display_name="Test\\Backslash",
    )
    p_xbs, _ = patients.get_or_create_with_outcome(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="ID-XBS",
        display_name="TestBackslash",
    )

    # % must match literally
    pct_results = patients.list_for_organization(provider.id, query="%Percent")
    pct_ids = {p.id for p in pct_results}
    assert p_pct.id in pct_ids
    assert p_xpct.id not in pct_ids, "literal % must not act as a wildcard matching TestXPercent"

    # _ must match literally
    und_results = patients.list_for_organization(provider.id, query="_Underscore")
    und_ids = {p.id for p in und_results}
    assert p_und.id in und_ids
    assert p_xund.id not in und_ids, "literal _ must not act as a wildcard matching TestXUnderscore"

    # \ must match literally
    bs_results = patients.list_for_organization(provider.id, query="\\Backslash")
    bs_ids = {p.id for p in bs_results}
    assert p_bs.id in bs_ids
    assert p_xbs.id not in bs_ids, "literal \\ must not match TestBackslash"
