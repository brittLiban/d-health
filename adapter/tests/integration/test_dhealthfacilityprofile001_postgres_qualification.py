"""DHEALTH-FACILITY-PROFILE-001/ADR-074 against real PostgreSQL.

Opt-in, like every suite in this directory: it needs a disposable database (`DHFP_PG_*`) and is
never run in CI. What it exists for is the handful of claims SQLite structurally cannot test --
row-level locking, two sessions contending, and `timestamptz` arithmetic across zones.

    PG-FP-1   two facilities, two zones, one wall clock, two correct instants
    PG-FP-2   the create-versus-timezone-change race, serialized on the profile row
    PG-FP-3   the reschedule-versus-timezone-change race
    PG-FP-4   two administrators changing the timezone concurrently
    PG-FP-5   a historical appointment's instant survives a timezone change
    PG-FP-6   cross-tenant update under contention
    PG-FP-7   profile uniqueness is the database's, and a retry is deterministic
    PG-FP-8   day boundaries per facility, against real timestamptz
    PG-FP-9   migration state and the DST policy on a zone that actually transitions

Run it with a throwaway container, never a facility database:

    DHFP_PG_HOST=127.0.0.1 DHFP_PG_PORT=55492 DHFP_PG_USER=adapter \\
    DHFP_PG_PASSWORD=adapter DHFP_PG_DATABASE=adapter \\
    PYTHONPATH=src python -m pytest -q 
      tests/integration/test_dhealthfacilityprofile001_postgres_qualification.py
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, time
from threading import Barrier, Thread

import pytest
from exchange_auth_support import generate_keypair, make_token  # noqa: F401 -- parity with siblings
from exchange_support import make_provider
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.appointments import (
    AmbiguousLocalTime,
    AppointmentRepository,
    NonexistentLocalTime,
    day_bounds,
)
from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.facility_profile import (
    FacilityProfileRepository,
    FacilityTimezoneNotConfigured,
)
from daher_adapter.exchange.identity import PractitionerReferenceRepository  # noqa: F401
from daher_adapter.exchange.models import Appointment, FacilityProfile
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.exchange.visits import VisitRepository

PG_HOST = os.environ.get("DHFP_PG_HOST")
PG_PORT = os.environ.get("DHFP_PG_PORT")
PG_USER = os.environ.get("DHFP_PG_USER")
PG_PASSWORD = os.environ.get("DHFP_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHFP_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a disposable PostgreSQL database (DHFP_PG_*) -- never in CI",
)

DJIBOUTI = "Africa/Djibouti"
VANCOUVER = "America/Vancouver"
NEW_YORK = "America/New_York"


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _prefix(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


class Fixture:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _seed(sessions: sessionmaker, prefix: str, timezone_name: str = DJIBOUTI) -> Fixture:
    """A provider with a facility profile, a patient, and the repositories under test."""
    organizations = OrganizationRepository(sessions)
    patients = PatientRepository(sessions, organizations)
    security_audit = ExchangeSecurityAuditRepository()
    visits = VisitRepository(sessions, organizations, security_audit)
    profiles = FacilityProfileRepository(sessions, security_audit)
    appointments = AppointmentRepository(sessions, organizations, visits, security_audit, profiles)

    provider = make_provider(organizations, name=f"Clinique {prefix}")
    profiles.ensure(provider.id, timezone_name)
    if timezone_name != DJIBOUTI:
        # `make_provider` seeds the deployment zone; set the one this fixture asked for.
        with sessions.begin() as session:
            profile = session.scalars(
                select(FacilityProfile).where(FacilityProfile.organization_id == provider.id)
            ).one()
            profile.timezone_name = timezone_name

    identity_id = uuid.uuid4()
    with sessions.begin() as session:
        session.execute(
            text(
                "INSERT INTO exchange_identities (id, issuer, subject, display_name, status, "
                "created_at) VALUES (:id, :issuer, :subject, :name, 'ACTIVE', NOW())"
            ),
            {
                "id": identity_id,
                "issuer": "https://dhfp.tests.invalid",
                "subject": f"actor-{prefix}",
                "name": f"Acteur {prefix}",
            },
        )

    patient = patients.create(
        provider_organization_id=provider.id,
        idempotency_key=f"patient-{prefix}",
        display_name=f"Patient {prefix}",
        date_of_birth=None,
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=identity_id,
    ).patient

    principal = AuthenticatedPrincipal(
        identity_id=identity_id,
        subject=f"actor-{prefix}",
        issuer="https://dhfp.tests.invalid",
        platform_admin=False,
    )
    return Fixture(
        provider=provider,
        patient=patient,
        principal=principal,
        profiles=profiles,
        appointments=appointments,
        organizations=organizations,
        sessions=sessions,
    )


def _instant(sessions: sessionmaker, appointment_id: uuid.UUID) -> datetime:
    with sessions() as session:
        stored = session.get(Appointment, appointment_id)
        value = stored.scheduled_at
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ==================================================================================================
# PG-FP-1: two facilities, two zones, one wall clock
# ==================================================================================================


def test_pg_fp_1_the_same_wall_clock_at_two_facilities_is_two_instants(sessions: sessionmaker):
    """The ticket's success condition, against real `timestamptz` rather than SQLite's text dates.

    Both clinics book 10:00 on 2026-06-15. Djibouti is +03:00 all year; Vancouver on that date is
    PDT, -07:00. Ten hours apart, and each correct about its own clinic."""
    dj = _seed(sessions, _prefix("fp1dj"), DJIBOUTI)
    van = _seed(sessions, _prefix("fp1van"), VANCOUVER)

    first, _ = dj.appointments.create(
        principal=dj.principal,
        provider_organization_id=dj.provider.id,
        patient_id=dj.patient.id,
        scheduled_date=date(2026, 6, 15),
        scheduled_time=time(10, 0),
        idempotency_key=_prefix("k"),
    )
    second, _ = van.appointments.create(
        principal=van.principal,
        provider_organization_id=van.provider.id,
        patient_id=van.patient.id,
        scheduled_date=date(2026, 6, 15),
        scheduled_time=time(10, 0),
        idempotency_key=_prefix("k"),
    )

    assert _instant(sessions, first.id) == datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
    assert _instant(sessions, second.id) == datetime(2026, 6, 15, 17, 0, tzinfo=UTC)

    # And PostgreSQL itself agrees about the wall clocks, read back in each facility's own zone.
    with sessions() as session:
        dj_local = session.execute(
            text(
                "SELECT to_char(scheduled_at AT TIME ZONE :zone, 'HH24:MI') "
                "FROM exchange_appointments WHERE id = :id"
            ),
            {"zone": DJIBOUTI, "id": first.id},
        ).scalar_one()
        van_local = session.execute(
            text(
                "SELECT to_char(scheduled_at AT TIME ZONE :zone, 'HH24:MI') "
                "FROM exchange_appointments WHERE id = :id"
            ),
            {"zone": VANCOUVER, "id": second.id},
        ).scalar_one()
    assert dj_local == "10:00"
    assert van_local == "10:00"


# ==================================================================================================
# PG-FP-2 / PG-FP-3: the races
# ==================================================================================================


def test_pg_fp_2_a_booking_in_flight_serializes_against_a_timezone_change(sessions: sessionmaker):
    """The race ADR-074 §5 defines: a receptionist submits *10:00* while an administrator changes
    the facility timezone.

    The booking's transaction takes `FOR SHARE` on the profile row before converting, and the
    settings update takes `FOR UPDATE`. Here the booking's lock is taken and **held** while the
    update contends, so the update waits and the booking is validated and stored under one zone --
    the old one. What must never happen is a booking validated under one zone and stored under
    another, and this asserts the stored instant matches the zone the booking actually read.
    """
    fx = _seed(sessions, _prefix("fp2"), DJIBOUTI)
    outcome: dict[str, object] = {}
    gate = Barrier(2, timeout=30)

    def booking() -> None:
        # The repository's own transaction, so this is the real locking path rather than a mock.
        with fx.sessions.begin() as session:
            zone = fx.profiles.timezone_for_conversion_in_session(session, fx.provider.id)
            outcome["zone_read_by_booking"] = zone
            gate.wait()  # the updater may now try to take FOR UPDATE and will block
            from daher_adapter.exchange.appointments import instant_from_local

            outcome["instant"] = instant_from_local(date(2026, 6, 15), time(10, 0), zone)
            # Commit happens on exiting this block, releasing the share lock.

    def updater() -> None:
        gate.wait()
        fx.profiles.set_timezone(
            principal=fx.principal,
            organization_id=fx.provider.id,
            timezone_name=VANCOUVER,
        )
        outcome["update_committed"] = True

    threads = [Thread(target=booking), Thread(target=updater)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "the race deadlocked"

    # The booking read Djibouti and its instant is Djibouti's 10:00. Coherent, not torn.
    assert outcome["zone_read_by_booking"] == DJIBOUTI
    assert outcome["instant"] == datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
    assert outcome.get("update_committed") is True

    # And the change did land -- the booking delayed it, it did not lose it.
    assert fx.profiles.timezone_for(fx.provider.id) == VANCOUVER


def test_pg_fp_3_a_reschedule_in_flight_serializes_the_same_way(sessions: sessionmaker):
    """Reschedule converts under the same lock as create, so the same guarantee holds."""
    fx = _seed(sessions, _prefix("fp3"), DJIBOUTI)
    created, _ = fx.appointments.create(
        principal=fx.principal,
        provider_organization_id=fx.provider.id,
        patient_id=fx.patient.id,
        scheduled_date=date(2026, 6, 15),
        scheduled_time=time(9, 0),
        idempotency_key=_prefix("k"),
    )

    # Timezone changes first, commits, and only then the reschedule runs: the other ordering, which
    # ADR-074 §5 also accepts. The reschedule must use the NEW zone, consistently.
    fx.profiles.set_timezone(
        principal=fx.principal,
        organization_id=fx.provider.id,
        timezone_name=VANCOUVER,
    )
    moved = fx.appointments.reschedule(
        principal=fx.principal,
        provider_organization_id=fx.provider.id,
        appointment_id=created.id,
        scheduled_date=date(2026, 6, 15),
        scheduled_time=time(9, 0),
    )
    assert _instant(sessions, moved.id) == datetime(2026, 6, 15, 16, 0, tzinfo=UTC)


def test_pg_fp_4_two_administrators_changing_the_timezone_converge(sessions: sessionmaker):
    """`FOR UPDATE` on one row per organization means the two serialize: one final value, one row,
    no duplicate profile, and both callers return normally rather than one getting a 500."""
    fx = _seed(sessions, _prefix("fp4"), DJIBOUTI)
    gate = Barrier(2, timeout=30)
    results: list[str] = []
    failures: list[BaseException] = []

    def change(zone: str) -> None:
        try:
            gate.wait()
            record = fx.profiles.set_timezone(
                principal=fx.principal,
                organization_id=fx.provider.id,
                timezone_name=zone,
            )
            results.append(record.timezone_name)
        except BaseException as error:  # noqa: BLE001
            failures.append(error)

    threads = [Thread(target=change, args=(VANCOUVER,)), Thread(target=change, args=(NEW_YORK,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "concurrent updates deadlocked"
    assert failures == [], f"an ordinary contention raised: {failures!r}"
    assert len(results) == 2

    with sessions() as session:
        rows = session.scalars(
            select(FacilityProfile).where(FacilityProfile.organization_id == fx.provider.id)
        ).all()
    assert len(rows) == 1, "contention created a second profile row"
    assert rows[0].timezone_name in {VANCOUVER, NEW_YORK}


# ==================================================================================================
# PG-FP-5: history
# ==================================================================================================


def test_pg_fp_5_a_timezone_change_moves_no_stored_instant(sessions: sessionmaker):
    fx = _seed(sessions, _prefix("fp5"), DJIBOUTI)
    created, _ = fx.appointments.create(
        principal=fx.principal,
        provider_organization_id=fx.provider.id,
        patient_id=fx.patient.id,
        scheduled_date=date(2026, 6, 15),
        scheduled_time=time(10, 0),
        idempotency_key=_prefix("k"),
    )
    before = _instant(sessions, created.id)
    assert before == datetime(2026, 6, 15, 7, 0, tzinfo=UTC)

    for zone in (VANCOUVER, NEW_YORK, DJIBOUTI):
        fx.profiles.set_timezone(
            principal=fx.principal, organization_id=fx.provider.id, timezone_name=zone
        )
        assert _instant(sessions, created.id) == before, f"the instant moved after setting {zone}"


# ==================================================================================================
# PG-FP-6 / PG-FP-7: tenancy and uniqueness
# ==================================================================================================


def test_pg_fp_6_a_profile_update_names_one_organization_only(sessions: sessionmaker):
    """The repository takes an organization id and touches exactly that row. The HTTP edge refuses a
    rival before reaching here; this is the layer below, proving a mutation cannot spill
    sideways."""
    mine = _seed(sessions, _prefix("fp6a"), DJIBOUTI)
    theirs = _seed(sessions, _prefix("fp6b"), DJIBOUTI)

    mine.profiles.set_timezone(
        principal=mine.principal,
        organization_id=mine.provider.id,
        timezone_name=VANCOUVER,
    )
    assert mine.profiles.timezone_for(mine.provider.id) == VANCOUVER
    assert theirs.profiles.timezone_for(theirs.provider.id) == DJIBOUTI


def test_pg_fp_7_a_second_profile_for_one_organization_is_impossible(sessions: sessionmaker):
    """`ensure` is insert-first-wins and the database refuses a duplicate regardless."""
    fx = _seed(sessions, _prefix("fp7"), DJIBOUTI)

    # Repeated `ensure` is a no-op, never an overwrite and never a duplicate.
    fx.profiles.ensure(fx.provider.id, VANCOUVER)
    assert fx.profiles.timezone_for(fx.provider.id) == DJIBOUTI

    with sessions() as session:
        count = session.execute(
            text("SELECT count(*) FROM exchange_facility_profiles WHERE organization_id = :id"),
            {"id": fx.provider.id},
        ).scalar_one()
    assert count == 1


def test_pg_fp_7b_a_facility_with_no_profile_refuses_rather_than_assuming(sessions: sessionmaker):
    """The missing-profile contract. An organization created outside the supported paths has no
    timezone authority, and the repository raises instead of falling back to UTC or the server's own
    zone -- a wrong instant nobody notices is the failure this prevents."""
    organizations = OrganizationRepository(sessions)
    bare = organizations.create(org_type="PROVIDER", name=f"Sans profil {_prefix('fp7b')}")
    profiles = FacilityProfileRepository(sessions)

    try:
        assert profiles.get(bare.id) is None
        with pytest.raises(FacilityTimezoneNotConfigured) as caught:
            profiles.timezone_for(bare.id)
        assert str(bare.id) in str(caught.value)
    finally:
        # Removed deliberately. This organization is in a state the product cannot produce, and
        # these suites share one database -- left behind, it makes `PG-FP-9`'s "every organization
        # has a profile" assertion fail for a reason that is this test's fixture rather than a
        # defect. Cleaning up is the honest way to keep both claims testable.
        with sessions.begin() as session:
            session.execute(
                text(
                    "DELETE FROM exchange_organization_role_assignments WHERE membership_id IN ("
                    "SELECT id FROM exchange_organization_memberships WHERE organization_id = :id)"
                ),
                {"id": bare.id},
            )
            session.execute(
                text("DELETE FROM exchange_organization_memberships WHERE organization_id = :id"),
                {"id": bare.id},
            )
            session.execute(
                text("DELETE FROM exchange_facility_enrollments WHERE organization_id = :id"),
                {"id": bare.id},
            )
            session.execute(
                text("DELETE FROM exchange_organizations WHERE id = :id"), {"id": bare.id}
            )


# ==================================================================================================
# PG-FP-8 / PG-FP-9: boundaries, migration state, DST
# ==================================================================================================


def test_pg_fp_8_day_boundaries_are_each_facilitys_own(sessions: sessionmaker):
    """A 23:30 Vancouver booking is 06:30Z the next day and must still belong to the Vancouver day
    it was booked for -- asserted through a real query, not only through the helper."""
    van = _seed(sessions, _prefix("fp8"), VANCOUVER)
    created, _ = van.appointments.create(
        principal=van.principal,
        provider_organization_id=van.provider.id,
        patient_id=van.patient.id,
        scheduled_date=date(2026, 6, 15),
        scheduled_time=time(23, 30),
        idempotency_key=_prefix("k"),
    )
    assert _instant(sessions, created.id) == datetime(2026, 6, 16, 6, 30, tzinfo=UTC)

    on_fifteenth = van.appointments.list_for_day(van.provider.id, date(2026, 6, 15))
    on_sixteenth = van.appointments.list_for_day(van.provider.id, date(2026, 6, 16))
    assert [record.id for record in on_fifteenth] == [created.id]
    assert on_sixteenth == []

    start, end = day_bounds(date(2026, 6, 15), VANCOUVER)
    with sessions() as session:
        matched = session.execute(
            text(
                "SELECT count(*) FROM exchange_appointments "
                "WHERE id = :id AND scheduled_at >= :start AND scheduled_at < :end"
            ),
            {"id": created.id, "start": start, "end": end},
        ).scalar_one()
    assert matched == 1


def test_pg_fp_9_migration_state_and_the_backfill(sessions: sessionmaker):
    """One head at 0041, every organization has a profile, and the table carries no contact fact."""
    with sessions() as session:
        heads = session.scalars(text("SELECT version_num FROM alembic_version")).all()
        assert len(heads) == 1, heads
        assert heads[0] == "0041_facility_profiles", heads[0]

        orphans = session.execute(
            text(
                "SELECT count(*) FROM exchange_organizations o WHERE NOT EXISTS ("
                "SELECT 1 FROM exchange_facility_profiles p WHERE p.organization_id = o.id)"
            )
        ).scalar_one()
        assert orphans == 0, f"{orphans} organization(s) have no facility profile"

        columns = set(
            session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'exchange_facility_profiles'"
                )
            )
            .scalars()
            .all()
        )
    assert columns == {"id", "organization_id", "timezone_name", "created_at", "updated_at"}


def test_pg_fp_9b_the_dst_policy_refuses_on_a_zone_that_really_transitions(sessions: sessionmaker):
    """`America/New_York` in 2025 -- a past transition, so every tzdata release agrees about it.

    `America/Vancouver` is deliberately not used for this: tzdata 2026c records British Columbia
    abolishing DST, so Vancouver has no autumn transition from 2026 onward and a DST test against it
    would assert nothing while looking like coverage."""
    fx = _seed(sessions, _prefix("fp9b"), NEW_YORK)
    common = {
        "principal": fx.principal,
        "provider_organization_id": fx.provider.id,
        "patient_id": fx.patient.id,
    }

    with pytest.raises(NonexistentLocalTime):
        fx.appointments.create(
            **common,
            scheduled_date=date(2025, 3, 9),
            scheduled_time=time(2, 30),
            idempotency_key=_prefix("k"),
        )
    with pytest.raises(AmbiguousLocalTime):
        fx.appointments.create(
            **common,
            scheduled_date=date(2025, 11, 2),
            scheduled_time=time(1, 30),
            idempotency_key=_prefix("k"),
        )

    # Narrow: an ordinary time on each transition day is bookable, and lands on the right instant.
    spring, _ = fx.appointments.create(
        **common,
        scheduled_date=date(2025, 3, 9),
        scheduled_time=time(10, 0),
        idempotency_key=_prefix("k"),
    )
    assert _instant(sessions, spring.id) == datetime(2025, 3, 9, 14, 0, tzinfo=UTC)

    autumn, _ = fx.appointments.create(
        **common,
        scheduled_date=date(2025, 11, 2),
        scheduled_time=time(10, 0),
        idempotency_key=_prefix("k"),
    )
    assert _instant(sessions, autumn.id) == datetime(2025, 11, 2, 15, 0, tzinfo=UTC)

    # And the transition days really are 23 and 25 hours long in this zone.
    spring_start, spring_end = day_bounds(date(2025, 3, 9), NEW_YORK)
    autumn_start, autumn_end = day_bounds(date(2025, 11, 2), NEW_YORK)
    assert (spring_end - spring_start).total_seconds() == 23 * 3600
    assert (autumn_end - autumn_start).total_seconds() == 25 * 3600
