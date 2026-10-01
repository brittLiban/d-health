"""DH-DEMO-001 real PostgreSQL qualification for `demo_seed`'s destructive reset path.

`reset_workflow_state` issues a real `TRUNCATE ... CASCADE` -- SQLite does not support `TRUNCATE`
and does not enforce foreign keys by default, so this behavior can only be qualified against real
PostgreSQL (matching every other integration test in this directory's own stated reasoning).

**Manual / opt-in only**, skipped by default (including in CI) unless every ``DHDEMO001_PG_*`` env
var below is set. Never point this at a real facility database -- nothing here checks that; use a
disposable instance, and note that `reset_workflow_state` itself is exactly what refuses to run
against anything that doesn't carry the three-fact demo marker (see PG-RS1 below).

Usage::

    DHDEMO001_PG_HOST=127.0.0.1 \\
    DHDEMO001_PG_PORT=5432 \\
    DHDEMO001_PG_USER=dhdemo001 \\
    DHDEMO001_PG_PASSWORD=dhdemo001 \\
    DHDEMO001_PG_DATABASE=dhealth_native_demo \\
        pytest adapter/tests/integration/test_demo_seed_postgres.py -v

The target database must already have migrations applied through ``0025_native_result_binding``
and must already carry the `dhealth_demo_environment` marker table (see
`demo/native/db-init/init-tenant-databases.sh`) -- this file does not run migrations or create
the marker itself.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_support import lab_staff
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.config import Settings
from daher_adapter.exchange import demo_seed
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import PractitionerReferenceRepository
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabOffering,
    LabOrderBlockReason,
    LabResultTemplate,
    Organization,
    Patient,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRepository
from daher_adapter.exchange.results import (
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
    NativeValueInput,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHDEMO001_PG_HOST")
PG_PORT = os.environ.get("DHDEMO001_PG_PORT")
PG_USER = os.environ.get("DHDEMO001_PG_USER")
PG_PASSWORD = os.environ.get("DHDEMO001_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHDEMO001_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHDEMO001_PG_* vars) -- never in CI",
)


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> sessionmaker[Session]:
    monkeypatch.setenv("ADAPTER_DEMO_ENVIRONMENT", "1")
    monkeypatch.delenv("DEMO_KEYCLOAK_ADMIN_URL", raising=False)
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _settings() -> Settings:
    return Settings(
        timezone="Africa/Djibouti",
        db_host=PG_HOST or "",
        db_port=int(PG_PORT or 5432),
        db_name=PG_DATABASE or "",
        db_user=PG_USER or "",
        db_password=PG_PASSWORD or "",
        audit_path=Path("/tmp/unused.jsonl"),
        retry_max_attempts=1,
        retry_multiplier_seconds=1.0,
        retry_max_seconds=1.0,
    )


def _table_count(sessions: sessionmaker[Session], table_name: str) -> int:
    with sessions() as session:
        return session.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one()  # noqa: S608


def _create_full_workflow_object(sessions: sessionmaker[Session]) -> None:
    """Drives one order all the way through provider-assignment, Native entry, completion,
    release, an amendment, and its own release -- exercising every table
    `demo_seed._WORKFLOW_TABLES` names, not merely `exchange_lab_orders` itself."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientRepository(sessions, organizations)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    workflow = LabWorkflowRepository(sessions, organizations)
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    amendments = LabResultAmendmentRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)

    with sessions() as session:
        provider = session.scalar(
            select(Organization).where(Organization.name == demo_seed.PROVIDER_ORG_NAME)
        )
        lab = session.scalar(
            select(Organization).where(Organization.name == demo_seed.LAB_ORG_NAME)
        )
        offering = session.scalar(select(LabOffering))
        template = session.scalar(
            select(LabResultTemplate).where(LabResultTemplate.active.is_(True))
        )
        definitions_by_type = {obs.result_type: obs.id for obs in template.observations}

    patient_result = patients.create(
        provider_organization_id=provider.id,
        idempotency_key=demo_seed.PATIENT_IDEMPOTENCY_KEY,
        display_name=demo_seed.PATIENT_DISPLAY_NAME,
        date_of_birth=None,
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=_seed_identity(identities),
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhdemo001-qualification",
        source_identity_system="urn:dhdemo001:practitioner",
        source_identity_value="D-1",
    )
    order = orders.create(
        idempotency_key=f"dhdemo001-qual-{uuid.uuid4().hex[:8]}",
        provider_organization_id=provider.id,
        patient_reference_id=patient_result.patient.primary_patient_reference_id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=offering.catalog_entry_id)],
    ).order
    selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)

    # DHEALTH-LAB-BLOCKED-001: one block episode, raised and resolved, so that
    # `exchange_lab_order_blocks` genuinely has a row for the reset assertion to clear. Without
    # this the loop in `test_pgd2_pgd3_...` would pass **vacuously** for the new table -- zero rows
    # before the reset and zero after proves nothing about whether reset covers it, and this
    # helper's whole docstring promise is that it exercises every table `_WORKFLOW_TABLES` names.
    #
    # Resolved rather than left active, because the order has to reach COMPLETED below and a
    # blocked order (correctly) cannot advance. The episode survives its own resolution, which is
    # exactly the row the reset must then remove.
    _record, episode, _created = workflow.block(
        order_id=order.id,
        lab_organization_id=lab.id,
        reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
        blocked_by_identity_id=_labtech_identity(identities, "tech"),
        note="note de qualification synthétique",
    )
    workflow.resolve_block(
        order_id=order.id,
        block_id=episode.id,
        lab_organization_id=lab.id,
        resolved_by_identity_id=_labtech_identity(identities, "tech"),
    )

    workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=lab_staff(organizations, lab.id),
    )
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)
    order = orders.get(order.id)
    item_id = order.items[0].id

    tech_identity = _labtech_identity(identities, "tech")
    results.record_native_values(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        template_id=template.id,
        values=[
            NativeValueInput(
                definition_id=definitions_by_type["NUMERIC"],
                numeric_value=Decimal("1"),
                text_value=None,
            ),
            NativeValueInput(
                definition_id=definitions_by_type["TEXT"],
                numeric_value=None,
                text_value="valeur de démonstration",
            ),
        ],
        recorded_by_identity_id=tech_identity,
    )
    results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=tech_identity,
    )
    inspection = results.get_for_lab(order.id, lab.id)
    releases.release(
        order_id=order.id,
        lab_organization_id=lab.id,
        released_by_identity_id=_labtech_identity(identities, "releaser"),
        expected_result_fingerprint=inspection.result_fingerprint,
    )
    amendments.start_amendment(
        order_id=order.id, lab_organization_id=lab.id, started_by_identity_id=tech_identity
    )


def _seed_identity(identities: ExchangeIdentityRepository) -> uuid.UUID:
    record, _ = identities.get_or_create(issuer="dhdemo001-qual", subject="seed")
    return record.id


def _labtech_identity(identities: ExchangeIdentityRepository, subject: str) -> uuid.UUID:
    record, _ = identities.get_or_create(issuer="dhdemo001-qual", subject=subject)
    return record.id


# -- PG-RS1: the safety boundary genuinely refuses a non-demo target -----------------------------


def test_pgrs1_refuses_a_database_missing_the_marker_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Points `run_reset` at a real, migrated PostgreSQL database that was never initialized by
    `demo/native/db-init/init-tenant-databases.sh` (no marker table at all) -- must refuse before
    touching anything, proving the safety boundary against a genuine database, not merely a unit
    test's own mock."""
    monkeypatch.setenv("ADAPTER_DEMO_ENVIRONMENT", "1")
    settings = Settings(
        timezone="Africa/Djibouti",
        db_host=PG_HOST or "",
        db_port=int(PG_PORT or 5432),
        db_name="not_the_demo_database",
        db_user=PG_USER or "",
        db_password=PG_PASSWORD or "",
        audit_path=Path("/tmp/unused.jsonl"),
        retry_max_attempts=1,
        retry_multiplier_seconds=1.0,
        retry_max_seconds=1.0,
    )
    engine = create_engine(
        f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}",
        pool_pre_ping=True,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with pytest.raises(demo_seed.DemoEnvironmentRefused, match="not_the_demo_database"):
        demo_seed.run_reset(settings, factory)
    engine.dispose()


# -- PG-D2/D3: repeat reset is idempotent; a real workflow object is fully wiped, baseline persists


def test_pgd2_pgd3_reset_wipes_workflow_state_and_preserves_baseline(
    sessions: sessionmaker[Session],
) -> None:
    settings = _settings()
    demo_seed.run_reset(settings, sessions)

    _create_full_workflow_object(sessions)
    assert _table_count(sessions, "exchange_lab_orders") == 1
    assert _table_count(sessions, "exchange_amendments") == 1

    demo_seed.run_reset(settings, sessions)

    for table in demo_seed._WORKFLOW_TABLES:
        assert _table_count(sessions, table) == 0, f"{table} was not cleared by reset"

    with sessions() as session:
        organizations = session.scalars(select(Organization)).all()
        patients = session.scalars(select(Patient)).all()
    assert len(organizations) == 2, "baseline organizations must survive reset unchanged"
    assert len(patients) == 1, "the baseline patient must survive reset unchanged"

    # D2: run it again -- must remain a no-op, no duplicate baseline rows.
    demo_seed.run_reset(settings, sessions)
    with sessions() as session:
        organizations = session.scalars(select(Organization)).all()
    assert len(organizations) == 2, "repeat reset must never duplicate baseline organizations"
