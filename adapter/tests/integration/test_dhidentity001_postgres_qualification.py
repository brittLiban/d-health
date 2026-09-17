"""DH-IDENTITY-001's required concurrency proof: real PostgreSQL row-locking/UNIQUE-constraint
qualification of native Patient registration (`PatientRepository.create`), plus a real-database
proof of the composite tenant foreign key `PatientReference` now carries. SQLite (the threaded
unit tests already cover the general insert-first-wins mechanism, and does not enforce foreign
keys at all by default) is not sufficient evidence for either claim -- matching the reasoning
``test_dhex003c_postgres_qualification.py``/``test_dhex009_postgres_qualification.py`` already
established.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI's own fast job unless the disposable postgres service sets
these exact env vars -- see ``.github/workflows/ci.yml``'s "DH-IDENTITY-001 real PostgreSQL patient
directory qualification" step). Never point this at a facility database.

The target database must already have migrations applied through
``0022_exchange_patient_directory`` (``alembic upgrade head``) before running this file -- it does
not run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    Patient,
    PatientReference,
    PatientRegistrationRequest,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRegistrationConflict, PatientRepository

PG_HOST = os.environ.get("DHIDENTITY001_PG_HOST")
PG_PORT = os.environ.get("DHIDENTITY001_PG_PORT")
PG_USER = os.environ.get("DHIDENTITY001_PG_USER")
PG_PASSWORD = os.environ.get("DHIDENTITY001_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHIDENTITY001_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHIDENTITY001_PG_* env vars) -- never "
    "in CI's own test collection default, only the dedicated wired-in CI step",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _setup(sessions: sessionmaker, prefix: str):
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    patients = PatientRepository(sessions, organizations)
    provider = organizations.create(org_type="PROVIDER", name=f"{prefix}-clinic")
    identity, _ = identities.get_or_create(issuer="pg-qualification", subject=f"{prefix}-actor")
    return organizations, patients, provider, identity


def test_pg1_pg4_ten_concurrent_identical_registrations_yield_exactly_one_patient(
    sessions: sessionmaker,
) -> None:
    prefix = f"pgidentity-same-{uuid.uuid4().hex[:8]}"
    _organizations, patients, provider, identity = _setup(sessions, prefix)
    key = f"{prefix}-key"

    outcomes: dict[int, bool] = {}
    resolved_ids: dict[int, uuid.UUID] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            result = patients.create(
                provider_organization_id=provider.id,
                idempotency_key=key,
                display_name="Concurrent Patient",
                date_of_birth=None,
                contact_channel_type=None,
                contact_value=None,
                created_by_identity_id=identity.id,
            )
            with lock:
                outcomes[i] = result.created
                resolved_ids[i] = result.patient.id
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected exceptions from concurrent registrations: {errors!r}"
    assert len(outcomes) == 10
    assert sorted(outcomes.values()) == [False] * 9 + [True], (
        f"exactly one concurrent attempt must create the Patient, got {outcomes!r}"
    )
    assert len(set(resolved_ids.values())) == 1, "every caller must resolve to the same Patient"

    with sessions() as session:
        # PG1: exactly one Patient.
        patient_count = session.scalar(
            select(func.count())
            .select_from(Patient)
            .where(Patient.provider_organization_id == provider.id)
        )
        assert patient_count == 1
        # PG2: exactly one native PatientReference.
        reference_count = session.scalar(
            select(func.count())
            .select_from(PatientReference)
            .where(
                PatientReference.source_organization_id == provider.id,
                PatientReference.source_system == "dhealth",
            )
        )
        assert reference_count == 1
        # PG3: exactly one registration request.
        registration_count = session.scalar(
            select(func.count())
            .select_from(PatientRegistrationRequest)
            .where(
                PatientRegistrationRequest.provider_organization_id == provider.id,
                PatientRegistrationRequest.idempotency_key == key,
            )
        )
        assert registration_count == 1


def test_pg5_pg6_concurrent_conflicting_payload_never_creates_two_patients(
    sessions: sessionmaker,
) -> None:
    prefix = f"pgidentity-conflict-{uuid.uuid4().hex[:8]}"
    _organizations, patients, provider, identity = _setup(sessions, prefix)
    key = f"{prefix}-key"

    outcomes: dict[int, str] = {}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def attempt(i: int, name: str) -> None:
        try:
            barrier.wait(timeout=15)
            result = patients.create(
                provider_organization_id=provider.id,
                idempotency_key=key,
                display_name=name,
                date_of_birth=None,
                contact_channel_type=None,
                contact_value=None,
                created_by_identity_id=identity.id,
            )
            with lock:
                outcomes[i] = "created" if result.created else "replayed"
        except PatientRegistrationConflict:
            with lock:
                outcomes[i] = "conflict"
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes[i] = f"error:{error!r}"

    threads = [
        threading.Thread(target=attempt, args=(0, "Name A")),
        threading.Thread(target=attempt, args=(1, "Name B")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert set(outcomes.values()) <= {"created", "conflict"}
    assert list(outcomes.values()).count("created") == 1, outcomes
    assert list(outcomes.values()).count("conflict") == 1, outcomes

    with sessions() as session:
        patient_count = session.scalar(
            select(func.count())
            .select_from(Patient)
            .where(Patient.provider_organization_id == provider.id)
        )
        # PG6: no orphan Patient -- exactly one, from the winning attempt only.
        assert patient_count == 1
        reference_count = session.scalar(
            select(func.count())
            .select_from(PatientReference)
            .where(PatientReference.source_organization_id == provider.id)
        )
        # PG7: no orphan PatientReference.
        assert reference_count == 1
        registration_count = session.scalar(
            select(func.count())
            .select_from(PatientRegistrationRequest)
            .where(
                PatientRegistrationRequest.provider_organization_id == provider.id,
                PatientRegistrationRequest.idempotency_key == key,
            )
        )
        # PG8: no orphan registration record.
        assert registration_count == 1


def test_pg_composite_fk_rejects_cross_provider_reference_to_patient_link(
    sessions: sessionmaker,
) -> None:
    """M6/§8 real-database proof: SQLite does not enforce foreign keys by default, so this
    invariant is only provable against real PostgreSQL. Directly constructing a PatientReference
    whose (patient_id, source_organization_id) pair does not match any (Patient.id,
    Patient.provider_organization_id) row must be rejected by the database itself."""
    prefix = f"pgidentity-fk-{uuid.uuid4().hex[:8]}"
    organizations, patients, provider_a, identity = _setup(sessions, prefix)
    provider_b = organizations.create(org_type="PROVIDER", name=f"{prefix}-clinic-b")

    result = patients.create(
        provider_organization_id=provider_a.id,
        idempotency_key=f"{prefix}-key",
        display_name="Provider A Patient",
        date_of_birth=None,
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=identity.id,
    )

    with pytest.raises(IntegrityError):
        with sessions() as session, session.begin():
            session.add(
                PatientReference(
                    # Cross-provider on purpose: this Patient belongs to provider_a, but the
                    # reference claims provider_b as its source organization.
                    source_organization_id=provider_b.id,
                    patient_id=result.patient.id,
                    source_system="attempted-cross-tenant-link",
                    source_identity_system=f"urn:{prefix}:attack",
                    source_identity_value="X",
                )
            )
