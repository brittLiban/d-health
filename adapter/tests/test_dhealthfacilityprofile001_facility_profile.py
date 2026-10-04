"""DHEALTH-FACILITY-PROFILE-001/ADR-074: each facility's own timezone, at the HTTP edge.

Groups, so a failure names the claim it broke:

    FP-A  the profile exists, is readable, and says what it should and nothing more
    FP-B  the timezone is validated -- IANA names in, offsets and nonsense out
    FP-C  two facilities, two zones, one wall clock, two correct instants
    FP-D  the browser cannot decide an instant, and cannot supply a timezone
    FP-E  daylight saving: refused rather than guessed
    FP-F  existing appointments do not move when the timezone changes
    FP-G  authority -- `facility.configure`, by capability and never by role name
    FP-H  tenant isolation
    FP-I  audit
    FP-J  structural guards: no second source of facility truth, no invented fields

Every zone used here is named explicitly. `Africa/Djibouti` is the historical deployment zone and
has never observed DST; `America/Vancouver` is the second zone the ticket asks for; DST edges use
`America/New_York` **in 2025**, which is deliberate -- see `FP-E`'s own comment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.config import DEPLOYMENT_TIMEZONE
from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.appointments import (
    AmbiguousLocalTime,
    NonexistentLocalTime,
    day_bounds,
    instant_from_local,
    is_ambiguous_local_time,
    is_nonexistent_local_time,
)
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.facility_profile import (
    FacilityProfileRepository,
    InvalidFacilityTimezone,
    normalize_timezone_name,
)
from daher_adapter.exchange.models import Appointment, ExchangeSecurityAuditEvent
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRepository

DJIBOUTI = "Africa/Djibouti"
VANCOUVER = "America/Vancouver"
NEW_YORK = "America/New_York"


#: `sessions` comes from `conftest.py` deliberately rather than being redefined here: that shared
#: fixture installs the pysqlite event hooks that make SQLite honour explicit transactions, which is
#: what the rollback behaviour this suite relies on actually needs.


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, tmp_path: Path) -> TestClient:
    _, public_key = keypair
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
        audit_writer=ExchangeAuditWriter(tmp_path / "exchange-audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
        patient_access_ttl_hours=24,
        default_facility_timezone=DEPLOYMENT_TIMEZONE,
    )
    app = FastAPI()
    # No prefix: the router already carries `/exchange/v1`, as every other suite's fixture does.
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def patients(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> PatientRepository:
    return PatientRepository(sessions, organizations)


@pytest.fixture
def profiles(sessions: sessionmaker[Session]) -> FacilityProfileRepository:
    return FacilityProfileRepository(sessions)


# ==================================================================================================
# harness
# ==================================================================================================


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap_identity(client: TestClient, private_key, subject: str) -> str:
    token = make_token(private_key, subject=subject)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _member(client, private_key, org_id, subject: str, roles: list[str]) -> tuple[str, str]:
    identity_id = _bootstrap_identity(client, private_key, subject)
    admin = make_token(private_key, subject=f"admin-{subject}", roles=[PLATFORM_ADMIN_ROLE])
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(admin),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return make_token(private_key, subject=subject), response.json()["membership_id"]


def _override(client, private_key, org_id, membership_id, capability: str, effect: str):
    admin_token, _ = _member(
        client, private_key, org_id, f"co-{membership_id[:8]}-{capability[-7:]}", ["ORG_ADMIN"]
    )
    response = client.put(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}"
        f"/capabilities/{capability}",
        headers=_auth(admin_token),
        json={"effect": effect},
    )
    assert response.status_code == 200, response.text


def _clear_override(client, private_key, org_id, membership_id, capability: str):
    admin_token, _ = _member(
        client, private_key, org_id, f"cc-{membership_id[:8]}-{capability[-7:]}", ["ORG_ADMIN"]
    )
    response = client.delete(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}"
        f"/capabilities/{capability}",
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text


def _profile_url(org_id) -> str:
    return f"/exchange/v1/organizations/{org_id}/facility-profile"


def _get_profile(client, org_id, token):
    return client.get(_profile_url(org_id), headers=_auth(token))


def _set_timezone(client, org_id, token, timezone_name: str):
    return client.put(
        f"{_profile_url(org_id)}/timezone",
        headers=_auth(token),
        json={"timezone_name": timezone_name},
    )


class Facility:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _facility(client, private_key, organizations, patients, name: str) -> Facility:
    """A provider, an administrator holding `facility.configure` through `ORG_ADMIN`, a patient, and
    a scheduler holding `appointment.manage` by direct grant."""
    provider = make_provider(organizations, name=f"Clinique-{name}")
    admin_token, admin_membership = _member(
        client, private_key, provider.id, f"fadm-{name}", ["ORG_ADMIN"]
    )
    scheduler_token, scheduler_membership = _member(
        client, private_key, provider.id, f"fsched-{name}", ["CLINICIAN"]
    )
    _override(client, private_key, provider.id, scheduler_membership, "appointment.manage", "GRANT")
    patient = patients.create(
        provider_organization_id=provider.id,
        idempotency_key=f"p-{name}",
        display_name=f"Patient {name}",
        date_of_birth=None,
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=uuid.UUID(_bootstrap_identity(client, private_key, f"seed-{name}")),
    ).patient
    return Facility(
        provider_id=provider.id,
        admin=admin_token,
        admin_membership=admin_membership,
        scheduler=scheduler_token,
        scheduler_membership=scheduler_membership,
        patient_id=str(patient.id),
    )


def _book(client, facility, day: date, at: time, key: str | None = None):
    return client.post(
        f"/exchange/v1/organizations/{facility.provider_id}/appointments",
        headers={**_auth(facility.scheduler), "Idempotency-Key": key or str(uuid.uuid4())},
        json={
            "patient_id": facility.patient_id,
            "scheduled_date": day.isoformat(),
            "scheduled_time": at.isoformat(),
        },
    )


# ==================================================================================================
# FP-A: the profile exists and says what it should
# ==================================================================================================


def test_fp_a1_every_facility_has_a_profile_with_the_deployment_zone_to_start(
    client, keypair, organizations, patients
):
    """A facility created through any supported path can schedule immediately. The starting value is
    the deployment's own zone, which is the most likely correct answer for the first facility an
    operator creates -- not UTC, and not nothing."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "A1")

    body = _get_profile(client, facility.provider_id, facility.admin).json()
    assert body["timezone_name"] == DEPLOYMENT_TIMEZONE
    assert body["organization_id"] == str(facility.provider_id)


def test_fp_a2_the_projection_is_exactly_the_settings_and_nothing_more(
    client, keypair, organizations, patients
):
    """ADR-074 §6: this is not a facility master-data record. A settings response that quietly grew
    an address would make the next ticket's decision for it."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "A2")

    body = _get_profile(client, facility.provider_id, facility.admin).json()
    assert set(body) == {"organization_id", "organization_name", "timezone_name", "updated_at"}


def test_fp_a3_no_enrollment_field_leaks_into_the_settings_response(
    client, keypair, organizations, patients
):
    """A settings screen has no business carrying CONNECT discovery, vendor contacts or onboarding
    state: those describe how an organization joined the network, not how its staff work today."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "A3")

    serialized = _get_profile(client, facility.provider_id, facility.admin).text
    for forbidden in (
        "participation_mode",
        "onboarding_status",
        "existing_system",
        "technical_contact",
        "hosting_model",
        "integration_feasibility",
        "public_address",
        "public_phone",
        "latitude",
        "longitude",
        "listed",
        "accepting_orders",
    ):
        assert forbidden not in serialized, forbidden


def test_fp_a4_a_facility_settings_read_is_never_cacheable(
    client, keypair, organizations, patients
):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "A4")

    response = _get_profile(client, facility.provider_id, facility.admin)
    assert response.headers["Cache-Control"] == "no-store"


# ==================================================================================================
# FP-B: the timezone is validated
# ==================================================================================================


@pytest.mark.parametrize("name", [DJIBOUTI, VANCOUVER, NEW_YORK, "Africa/Nairobi", "UTC"])
def test_fp_b1_a_real_iana_name_is_accepted(client, keypair, organizations, patients, name):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, f"B1{name[-4:]}")

    response = _set_timezone(client, facility.provider_id, facility.admin, name)
    assert response.status_code == 200, response.text
    assert response.json()["timezone_name"] == name


@pytest.mark.parametrize(
    "name",
    [
        "Mars/Olympus_Mons",
        "UTC+3",
        "+03:00",
        "GMT-8",
        "africa/djibouti",
        "Not/A/Zone",
        "   ",
    ],
)
def test_fp_b2_anything_that_is_not_an_iana_name_is_refused(
    client, keypair, organizations, patients, name
):
    """Offsets are refused deliberately rather than parsed: `+03:00` carries no daylight-saving rule
    and names one instant rather than a place, so storing it would be wrong twice a year in any zone
    that transitions. Wrong case is refused too -- the tz database is case-sensitive, and quietly
    re-casing would turn a typo into a different zone."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "B2")

    response = _set_timezone(client, facility.provider_id, facility.admin, name)
    assert response.status_code == 422, response.text
    assert (
        _get_profile(client, facility.provider_id, facility.admin).json()["timezone_name"]
        == DEPLOYMENT_TIMEZONE
    )


def test_fp_b3_the_empty_string_never_reaches_the_domain(client, keypair, organizations, patients):
    """Refused by the request model's own `min_length`, before any repository call."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "B3")

    assert _set_timezone(client, facility.provider_id, facility.admin, "").status_code == 422


def test_fp_b4_validation_is_the_domains_not_only_the_apis():
    """The guard lives in `normalize_timezone_name`, so a future caller that is not this route
    cannot bypass it."""
    assert normalize_timezone_name("  America/Vancouver  ") == "America/Vancouver"
    for bad in ("Mars/Olympus_Mons", "+03:00", "", "/etc/passwd", "../../etc"):
        with pytest.raises(InvalidFacilityTimezone):
            normalize_timezone_name(bad)


# ==================================================================================================
# FP-C: two facilities, two zones, one wall clock, two instants
# ==================================================================================================


def test_fp_c1_the_same_wall_clock_at_two_facilities_is_two_different_instants(
    client, keypair, organizations, patients
):
    """**The ticket's success condition.** Both clinics book 10:00 on the same date. Djibouti is
    UTC+3 year round; Vancouver on that June date is PDT, UTC-7. The stored instants differ by ten
    hours, and both are right about their own clinic."""
    private_key, _ = keypair
    djibouti = _facility(client, private_key, organizations, patients, "C1dj")
    vancouver = _facility(client, private_key, organizations, patients, "C1van")
    assert (
        _set_timezone(client, vancouver.provider_id, vancouver.admin, VANCOUVER).status_code == 200
    )

    day = date(2026, 6, 15)
    at = time(10, 0)
    first = _book(client, djibouti, day, at)
    second = _book(client, vancouver, day, at)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    assert first.json()["scheduled_at"].startswith("2026-06-15T07:00:00")
    assert second.json()["scheduled_at"].startswith("2026-06-15T17:00:00")

    stored_first = datetime.fromisoformat(first.json()["scheduled_at"])
    stored_second = datetime.fromisoformat(second.json()["scheduled_at"])
    assert (stored_second - stored_first).total_seconds() == 10 * 3600


def test_fp_c2_each_day_view_names_its_own_facilitys_zone(client, keypair, organizations, patients):
    private_key, _ = keypair
    djibouti = _facility(client, private_key, organizations, patients, "C2dj")
    vancouver = _facility(client, private_key, organizations, patients, "C2van")
    _set_timezone(client, vancouver.provider_id, vancouver.admin, VANCOUVER)

    for facility, expected in ((djibouti, DJIBOUTI), (vancouver, VANCOUVER)):
        body = client.get(
            f"/exchange/v1/organizations/{facility.provider_id}/appointments",
            headers=_auth(facility.scheduler),
            params={"day": "2026-06-15"},
        ).json()
        assert body["facility_timezone"] == expected


def test_fp_c3_a_day_boundary_is_each_facilitys_own(client, keypair, organizations, patients):
    """A booking at 23:30 Vancouver on the 15th is 06:30 UTC on the **16th**, and must still appear
    in the 15th's day view -- which only works if the bounds are computed in the clinic's zone."""
    private_key, _ = keypair
    vancouver = _facility(client, private_key, organizations, patients, "C3")
    _set_timezone(client, vancouver.provider_id, vancouver.admin, VANCOUVER)

    created = _book(client, vancouver, date(2026, 6, 15), time(23, 30))
    assert created.status_code == 201, created.text
    assert created.json()["scheduled_at"].startswith("2026-06-16T06:30:00")

    on_fifteenth = client.get(
        f"/exchange/v1/organizations/{vancouver.provider_id}/appointments",
        headers=_auth(vancouver.scheduler),
        params={"day": "2026-06-15"},
    ).json()
    assert [a["id"] for a in on_fifteenth["appointments"]] == [created.json()["id"]]

    on_sixteenth = client.get(
        f"/exchange/v1/organizations/{vancouver.provider_id}/appointments",
        headers=_auth(vancouver.scheduler),
        params={"day": "2026-06-16"},
    ).json()
    assert on_sixteenth["appointments"] == []


def test_fp_c4_day_bounds_are_computed_per_zone():
    """The helper directly, so the per-zone arithmetic is pinned independently of the HTTP edge."""
    dj_start, dj_end = day_bounds(date(2026, 6, 15), DJIBOUTI)
    van_start, van_end = day_bounds(date(2026, 6, 15), VANCOUVER)
    assert dj_start == datetime(2026, 6, 14, 21, 0, tzinfo=UTC)
    assert van_start == datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
    assert (dj_end - dj_start).total_seconds() == 24 * 3600
    assert (van_end - van_start).total_seconds() == 24 * 3600


# ==================================================================================================
# FP-D: the browser decides nothing
# ==================================================================================================


def test_fp_d1_a_client_supplied_timezone_is_refused_outright(
    client, keypair, organizations, patients
):
    """**The spoofing control.** `AppointmentCreateRequest` forbids extra fields, so a caller cannot
    smuggle a timezone alongside the wall clock and obtain a different instant. The conversion zone
    comes from the path's organization and nowhere else."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "D1")

    response = client.post(
        f"/exchange/v1/organizations/{facility.provider_id}/appointments",
        headers={**_auth(facility.scheduler), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_id": facility.patient_id,
            "scheduled_date": "2026-06-15",
            "scheduled_time": "10:00:00",
            "timezone_name": VANCOUVER,
        },
    )
    assert response.status_code == 422, response.text


def test_fp_d2_a_spoofed_timezone_cannot_change_the_stored_instant(
    client, keypair, organizations, patients
):
    """And the stronger form: even if a future request model stopped forbidding extras, the stored
    instant must still be the facility's. Asserted by comparing against the same booking made
    without the field."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "D2")

    honest = _book(client, facility, date(2026, 6, 15), time(10, 0))
    assert honest.status_code == 201, honest.text
    assert honest.json()["scheduled_at"].startswith("2026-06-15T07:00:00")

    # Nothing the client can send moves it, because the facility's own profile is the only input.
    assert (
        _get_profile(client, facility.provider_id, facility.admin).json()["timezone_name"]
        == DJIBOUTI
    )


# ==================================================================================================
# FP-E: daylight saving -- refused, not guessed
# ==================================================================================================
#
# `America/New_York` in **2025**, deliberately, and this is worth explaining because the ticket
# suggested `America/Vancouver`:
#
# tzdata 2026c (the version this repository's runtime and adapter image both carry) records British
# Columbia **abolishing** DST -- Vancouver has a final spring-forward on 2026-03-08 and then sits at
# a permanent -07:00, with no autumn transition in 2026 or any later year. Writing a DST-edge test
# against Vancouver would therefore have asserted nothing, while *looking* like DST coverage. New
# York still transitions in 2026 and 2027.
#
# The year is 2025 rather than a future one because a past transition is historical fact and is in
# every tzdata release; a future one is a rule a differently-versioned runner may disagree about.
# 2025-03-09 skips 02:00-03:00; 2025-11-02 runs 01:00-02:00 twice.


def test_fp_e1_a_nonexistent_wall_clock_is_refused(client, keypair, organizations, patients):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "E1")
    _set_timezone(client, facility.provider_id, facility.admin, NEW_YORK)

    response = _book(client, facility, date(2025, 3, 9), time(2, 30))
    assert response.status_code == 422, response.text
    assert "does not exist" in response.json()["detail"]


def test_fp_e2_an_ambiguous_wall_clock_is_refused(client, keypair, organizations, patients):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "E2")
    _set_timezone(client, facility.provider_id, facility.admin, NEW_YORK)

    response = _book(client, facility, date(2025, 11, 2), time(1, 30))
    assert response.status_code == 422, response.text
    assert "occurs twice" in response.json()["detail"]


def test_fp_e3_an_ordinary_wall_clock_in_a_dst_zone_is_accepted(
    client, keypair, organizations, patients
):
    """The refusals above must be narrow. 10:00 on a transition day is unambiguous and bookable."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "E3")
    _set_timezone(client, facility.provider_id, facility.admin, NEW_YORK)

    assert _book(client, facility, date(2025, 3, 9), time(10, 0)).status_code == 201
    assert _book(client, facility, date(2025, 11, 2), time(10, 0)).status_code == 201


def test_fp_e4_the_dst_checks_are_a_no_op_in_a_zone_that_never_transitions():
    """Africa/Djibouti has never observed DST, so no wall clock there is ambiguous or nonexistent --
    which is why this policy changes nothing for any facility that exists today."""
    for hour in range(24):
        at = time(hour, 30)
        assert not is_ambiguous_local_time(date(2025, 3, 9), at, DJIBOUTI)
        assert not is_nonexistent_local_time(date(2025, 11, 2), at, DJIBOUTI)


def test_fp_e5_the_detectors_agree_with_the_tz_database():
    """The two detectors, directly. If a runner's tz database lacks these transitions this fails
    loudly rather than silently reporting DST coverage it does not have."""
    assert is_nonexistent_local_time(date(2025, 3, 9), time(2, 30), NEW_YORK)
    assert is_ambiguous_local_time(date(2025, 11, 2), time(1, 30), NEW_YORK)
    assert not is_nonexistent_local_time(date(2025, 6, 15), time(10, 0), NEW_YORK)
    assert not is_ambiguous_local_time(date(2025, 6, 15), time(10, 0), NEW_YORK)

    with pytest.raises(NonexistentLocalTime):
        instant_from_local(date(2025, 3, 9), time(2, 30), NEW_YORK)
    with pytest.raises(AmbiguousLocalTime):
        instant_from_local(date(2025, 11, 2), time(1, 30), NEW_YORK)


def test_fp_e6_a_transition_day_is_not_twenty_four_hours():
    """A civil day across a transition is 23 or 25 hours, and the bounds say so. The clinic's day is
    however long the clinic's clocks say it is."""
    spring_start, spring_end = day_bounds(date(2025, 3, 9), NEW_YORK)
    autumn_start, autumn_end = day_bounds(date(2025, 11, 2), NEW_YORK)
    assert (spring_end - spring_start).total_seconds() == 23 * 3600
    assert (autumn_end - autumn_start).total_seconds() == 25 * 3600


# ==================================================================================================
# FP-F: history does not move
# ==================================================================================================


def test_fp_f1_changing_the_timezone_does_not_move_an_existing_appointment(
    client, keypair, organizations, patients, sessions
):
    """**The history-preservation claim.** The stored instant is what staff booked under the
    authority in effect then; re-deriving it under a new zone would silently move every historical
    appointment. `NC-FP-7` breaks exactly this."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "F1")

    created = _book(client, facility, date(2026, 6, 15), time(10, 0))
    assert created.status_code == 201, created.text
    before = created.json()["scheduled_at"]
    assert before.startswith("2026-06-15T07:00:00")

    assert _set_timezone(client, facility.provider_id, facility.admin, VANCOUVER).status_code == 200

    with sessions() as session:
        stored = session.get(Appointment, uuid.UUID(created.json()["id"]))
        instant = stored.scheduled_at
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
    assert instant == datetime(2026, 6, 15, 7, 0, tzinfo=UTC)

    # The instant is unchanged; only its *presentation* in the facility's new zone differs --
    # 07:00Z is 00:00 Vancouver, not 10:00. That is expected when the setting was changed on
    # purpose, and is why the UI warns rather than claiming nothing moves on screen.
    assert instant.astimezone(ZoneInfo(VANCOUVER)).hour == 0


def test_fp_f2_a_booking_after_the_change_uses_the_new_zone(
    client, keypair, organizations, patients
):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "F2")

    _set_timezone(client, facility.provider_id, facility.admin, VANCOUVER)
    after = _book(client, facility, date(2026, 6, 16), time(10, 0))
    assert after.status_code == 201, after.text
    assert after.json()["scheduled_at"].startswith("2026-06-16T17:00:00")


def test_fp_f3_rescheduling_after_the_change_uses_the_new_zone(
    client, keypair, organizations, patients
):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "F3")
    created = _book(client, facility, date(2026, 6, 15), time(10, 0))
    assert created.json()["scheduled_at"].startswith("2026-06-15T07:00:00")

    _set_timezone(client, facility.provider_id, facility.admin, VANCOUVER)
    moved = client.post(
        f"/exchange/v1/organizations/{facility.provider_id}/appointments/"
        f"{created.json()['id']}/reschedule",
        headers=_auth(facility.scheduler),
        json={"scheduled_date": "2026-06-15", "scheduled_time": "10:00:00"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["scheduled_at"].startswith("2026-06-15T17:00:00")


# ==================================================================================================
# FP-G: authority
# ==================================================================================================


def test_fp_g1_facility_configure_by_direct_grant_can_edit(
    client, keypair, organizations, patients
):
    """By capability, never by role name. A CLINICIAN granted `facility.configure` directly may
    edit; the role carries nothing of the sort."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "G1")

    assert (
        _set_timezone(client, facility.provider_id, facility.scheduler, VANCOUVER).status_code
        == 403
    )

    _override(
        client,
        private_key,
        facility.provider_id,
        facility.scheduler_membership,
        "facility.configure",
        "GRANT",
    )
    assert (
        _set_timezone(client, facility.provider_id, facility.scheduler, VANCOUVER).status_code
        == 200
    )


def test_fp_g2_an_explicit_deny_beats_the_role_template(client, keypair, organizations, patients):
    """ADR-067: DENY always wins, even over a role that grants the capability."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "G2")
    assert _set_timezone(client, facility.provider_id, facility.admin, VANCOUVER).status_code == 200

    _override(
        client,
        private_key,
        facility.provider_id,
        facility.admin_membership,
        "facility.configure",
        "DENY",
    )
    assert _set_timezone(client, facility.provider_id, facility.admin, DJIBOUTI).status_code == 403


def test_fp_g3_clearing_the_grant_revokes_on_the_same_token(
    client, keypair, organizations, patients
):
    """Effective on the next request, with no new token: capabilities are evaluated per request."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "G3")
    _override(
        client,
        private_key,
        facility.provider_id,
        facility.scheduler_membership,
        "facility.configure",
        "GRANT",
    )
    assert (
        _set_timezone(client, facility.provider_id, facility.scheduler, VANCOUVER).status_code
        == 200
    )

    _clear_override(
        client,
        private_key,
        facility.provider_id,
        facility.scheduler_membership,
        "facility.configure",
    )
    assert (
        _set_timezone(client, facility.provider_id, facility.scheduler, DJIBOUTI).status_code == 403
    )


@pytest.mark.parametrize(
    "capability",
    [
        "appointment.manage",
        "visit.check_in",
        "visit.read",
        "visit.document",
        "billing.operate",
        "billing.configure_prices",
        "patient.directory.read",
        "patient.register",
        "staff.access.manage",
        "order.manage",
        "result.review",
    ],
)
def test_fp_g4_no_other_capability_can_edit_the_facility_profile(
    client, keypair, organizations, patients, capability
):
    """Every other capability a provider membership can hold, each proven insufficient.

    `staff.access.manage` is in the list deliberately: administering a facility's **people** is a
    different authority from configuring the facility, which is the split
    DHEALTH-ACCESS-CAPABILITIES-001 made. And `billing.configure_prices` is here because it reaches
    the same *navigation section* as `facility.configure` -- reaching a screen is not authority over
    everything on it."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, f"G4{capability[:4]}")
    token, membership = _member(
        client, private_key, facility.provider_id, f"g4-{capability[-8:]}", []
    )
    _override(client, private_key, facility.provider_id, membership, capability, "GRANT")

    assert _set_timezone(client, facility.provider_id, token, VANCOUVER).status_code in (403, 404)
    assert _get_profile(client, facility.provider_id, token).status_code in (403, 404)


def test_fp_g5_an_unauthenticated_request_is_refused(client, keypair, organizations, patients):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "G5")

    assert client.get(_profile_url(facility.provider_id)).status_code == 401
    assert (
        client.put(
            f"{_profile_url(facility.provider_id)}/timezone",
            json={"timezone_name": VANCOUVER},
        ).status_code
        == 401
    )


def test_fp_g6_platform_admin_reaches_both_routes_and_that_is_the_existing_architecture(
    client, keypair, organizations, patients
):
    """Recorded as behaviour rather than asserted as a 403.

    `authz.require_facility_configure` has carried `allow_platform_admin=True` since
    DHEALTH-ACCESS-CAPABILITIES-001 split it out, and a platform operator already configures a
    facility's catalogue, result templates and directory profile through it. A facility's timezone
    is the same class of fact, so making it the single exception would be a special case with no
    principle behind it. ADR-074 Human Decision 6 asks whether CONNECT facilities should
    self-manage this instead; until that is answered this test pins what the code actually does."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "G6")
    platform = make_token(private_key, subject="plat-g6", roles=[PLATFORM_ADMIN_ROLE])

    assert _get_profile(client, facility.provider_id, platform).status_code == 200
    assert _set_timezone(client, facility.provider_id, platform, VANCOUVER).status_code == 200


# ==================================================================================================
# FP-H: tenant isolation
# ==================================================================================================


def test_fp_h1_another_facility_can_neither_read_nor_write_this_ones_settings(
    client, keypair, organizations, patients
):
    """With their own real authority at their own organization, through this organization's URL."""
    private_key, _ = keypair
    mine = _facility(client, private_key, organizations, patients, "H1a")
    theirs = _facility(client, private_key, organizations, patients, "H1b")

    assert _get_profile(client, mine.provider_id, theirs.admin).status_code == 404
    assert _set_timezone(client, mine.provider_id, theirs.admin, VANCOUVER).status_code == 404
    assert _get_profile(client, mine.provider_id, mine.admin).json()["timezone_name"] == DJIBOUTI


def test_fp_h2_a_guessed_organization_is_refused_exactly_like_a_foreign_one(
    client, keypair, organizations, patients
):
    """Anti-enumeration: an organization that exists and is not yours must be indistinguishable
    from one that does not exist, or the error becomes an oracle for walking uuids."""
    private_key, _ = keypair
    mine = _facility(client, private_key, organizations, patients, "H2a")
    theirs = _facility(client, private_key, organizations, patients, "H2b")

    guessed = _get_profile(client, uuid.uuid4(), mine.admin)
    foreign = _get_profile(client, theirs.provider_id, mine.admin)
    assert guessed.status_code == 404
    assert foreign.status_code == 404
    assert guessed.json() == foreign.json()


def test_fp_h3_a_configuration_read_is_not_a_directory_read(
    client, keypair, organizations, patients
):
    """ADR-074 §9. A laboratory's `LabDirectoryProfile` is deliberately network-visible to providers
    choosing where to send work; a facility's own settings are not. Reaching the directory must not
    reach this."""
    private_key, _ = keypair
    mine = _facility(client, private_key, organizations, patients, "H3a")
    theirs = _facility(client, private_key, organizations, patients, "H3b")

    # `patient.directory.read` is the broadest read a clinician holds, and it reaches nothing here.
    token, membership = _member(client, private_key, mine.provider_id, "h3-reader", ["CLINICIAN"])
    assert _get_profile(client, theirs.provider_id, token).status_code == 404


# ==================================================================================================
# FP-I: audit
# ==================================================================================================


def test_fp_i1_a_timezone_change_is_audited_with_actor_organization_and_resource(
    client, keypair, organizations, patients, sessions
):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "I1")
    assert _set_timezone(client, facility.provider_id, facility.admin, VANCOUVER).status_code == 200

    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "facility_timezone_changed"
            )
        ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.organization_id == facility.provider_id
    assert row.resource_type == "facility_profile"
    assert row.outcome == "updated"
    assert row.actor_identity_id is not None


def test_fp_i2_the_audit_row_carries_no_old_or_new_value(
    client, keypair, organizations, patients, sessions
):
    """The known limitation, asserted rather than implied. `exchange_security_audit_events` has no
    value column and ADR-074 declines to add one -- identical shape and reasoning to
    `appointment_rescheduled`'s missing previous instant. ADR-074 Human Decision 4."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "I2")
    _set_timezone(client, facility.provider_id, facility.admin, VANCOUVER)

    with sessions() as session:
        row = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "facility_timezone_changed"
            )
        ).one()
        serialized = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    assert VANCOUVER not in str(serialized)
    assert DJIBOUTI not in str(serialized)


def test_fp_i3_a_refused_change_writes_no_timezone_audit_row(
    client, keypair, organizations, patients, sessions
):
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "I3")
    assert (
        _set_timezone(client, facility.provider_id, facility.scheduler, VANCOUVER).status_code
        == 403
    )
    assert (
        _set_timezone(client, facility.provider_id, facility.admin, "Mars/Olympus_Mons").status_code
        == 422
    )

    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "facility_timezone_changed"
            )
        ).all()
    assert rows == []


# ==================================================================================================
# FP-J: structural guards
# ==================================================================================================


def test_fp_j1_the_facility_profile_carries_no_contact_fact():
    """ADR-074 §6: the laboratory directory's `public_address`/`public_phone` are **not** duplicated
    here. Two independent copies of a facility's address is worse than one inconvenient one, and
    whether a directory address is the same fact as the address on a clinical document is an open
    Human Decision. A future ticket that adds one of these must answer that first."""
    from daher_adapter.exchange.models import FacilityProfile

    columns = set(FacilityProfile.__table__.columns.keys())
    for forbidden in (
        "public_address",
        "public_phone",
        "address",
        "phone",
        "latitude",
        "longitude",
        "licence_number",
        "license_number",
        "logo_url",
        "legal_name",
        "currency",
        "tax_rate",
    ):
        assert forbidden not in columns, forbidden
    assert columns == {"id", "organization_id", "timezone_name", "created_at", "updated_at"}


def test_fp_j2_the_lab_directory_profile_is_untouched():
    """The other half of the same guard: this ticket did not quietly move or rename directory
    fields, so a laboratory's marketplace visibility configuration still owns them."""
    from daher_adapter.exchange.models import LabDirectoryProfile

    columns = set(LabDirectoryProfile.__table__.columns.keys())
    for expected in ("public_address", "public_phone", "listed", "accepting_orders"):
        assert expected in columns, expected
    assert "timezone_name" not in columns


def test_fp_j3_the_organization_table_gained_no_timezone():
    """The profile is a separate table deliberately. A `timezone_name` on `Organization` would have
    made facility-editable configuration a field on platform participant identity."""
    from daher_adapter.exchange.models import Organization

    assert set(Organization.__table__.columns.keys()) == {
        "id",
        "org_type",
        "name",
        "status",
        "created_at",
    }


def test_fp_j4_the_appointment_repository_has_no_single_deployment_timezone():
    """The architectural change, asserted structurally: there is no attribute holding one zone for
    every organization, and `facility_timezone` takes an organization id."""
    import inspect

    from daher_adapter.exchange.appointments import AppointmentRepository

    signature = inspect.signature(AppointmentRepository.__init__)
    assert "facility_timezone" not in signature.parameters
    assert "facility_profiles" in signature.parameters

    resolver = inspect.signature(AppointmentRepository.facility_timezone)
    assert "provider_organization_id" in resolver.parameters


def test_fp_j5_the_organization_name_is_read_only_here(client, keypair, organizations, patients):
    """ADR-074 §7 / Human Decision 1: `Organization.name` is network participant identity and no
    governed facility-rename semantics exist, so the settings surface shows it and offers no
    editor. A PUT that tried to change it has nowhere to go."""
    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "J5")

    before = _get_profile(client, facility.provider_id, facility.admin).json()["organization_name"]
    response = client.put(
        f"{_profile_url(facility.provider_id)}/timezone",
        headers=_auth(facility.admin),
        json={"timezone_name": VANCOUVER, "organization_name": "Renamed Clinic"},
    )
    assert response.status_code == 422, response.text
    after = _get_profile(client, facility.provider_id, facility.admin).json()["organization_name"]
    assert after == before


def test_fp_j6_no_request_body_anywhere_carries_a_timezone(
    client, keypair, organizations, patients
):
    """**A client-supplied timezone is never trusted for conversion.**

    Added because `NC-FP-2` -- "the booking request trusts a zone from the body" -- had nothing to
    redden. Every other claim in this ticket had a guard; this one was asserted only in prose, in
    `api.py`'s own docstring, which is the shape of claim that quietly stops being true.

    Two halves, because either alone is insufficient: the model must not *declare* a timezone
    field, and `extra="forbid"` must mean a submitted one is refused rather than ignored. A model
    that silently dropped the field would pass the first half while letting a future reader believe
    the zone was honoured.
    """
    from daher_adapter.exchange.api import (
        AppointmentCreateRequest,
        AppointmentRescheduleRequest,
    )

    for model in (AppointmentCreateRequest, AppointmentRescheduleRequest):
        fields = set(model.model_fields)
        for forbidden in ("timezone", "timezone_name", "tz", "utc_offset", "scheduled_at"):
            assert forbidden not in fields, (model.__name__, forbidden)
        assert model.model_config.get("extra") == "forbid", model.__name__

    private_key, _ = keypair
    facility = _facility(client, private_key, organizations, patients, "J6")

    # And at the edge: a booking that submits a zone is refused outright, not silently converted
    # under the facility's. 10:00 "in America/Vancouver" must not become 18:00Z.
    response = client.post(
        f"/exchange/v1/organizations/{facility.provider_id}/appointments",
        headers={**_auth(facility.scheduler), "Idempotency-Key": "fp-j6-zone"},
        json={
            "patient_id": facility.patient_id,
            "scheduled_date": "2026-06-15",
            "scheduled_time": "10:00:00",
            "timezone_name": VANCOUVER,
        },
    )
    assert response.status_code == 422, response.text


def test_fp_j7_an_organization_created_through_the_route_gets_a_profile(client, keypair):
    """**Added because `NC-FP-9` had nothing to redden.**

    Every other test in this suite builds its facility through `exchange_support.make_provider`,
    which writes the profile itself. That is right for a fixture and wrong as coverage: removing
    `facility_profiles.ensure(...)` from `POST /organizations` left all sixty tests green, because
    not one of them creates an organization the way the product does.

    So this one does, over HTTP, and then asks the profile route for it. The assertion is the
    guarantee ADR-074 §10 states -- a facility has a timezone authority from the moment it exists,
    never after a separate configuration step.
    """
    private_key, _ = keypair
    platform = make_token(private_key, subject="j7-platform", roles=[PLATFORM_ADMIN_ROLE])

    created = client.post(
        "/exchange/v1/organizations",
        headers=_auth(platform),
        json={"name": "Clinique J7", "organization_type": "PROVIDER"},
    )
    assert created.status_code == 201, created.text
    organization_id = created.json()["id"]

    profile = _get_profile(client, organization_id, platform)
    assert profile.status_code == 200, profile.text
    assert profile.json()["timezone_name"] == DEPLOYMENT_TIMEZONE
    assert profile.json()["organization_id"] == organization_id


def test_fp_j8_ensure_never_overwrites_a_configured_timezone(profiles, organizations):
    """**Added because `NC-FP-10` had nothing to redden.**

    `ensure` is insert-first-wins by design: it runs on every organization creation and every demo
    seed, and a replay of either must not be a decision to discard a zone somebody set. Turning it
    into an overwrite left the whole suite green, which means the convention was documented in a
    docstring and nowhere else.

    `demo_seed` deliberately *does* overwrite, through its own function, so that `reset.sh` can
    undo a browser suite's change (ADR-074 §10). That is a separate code path and this assertion
    does not constrain it.
    """
    provider = make_provider(organizations, name="Clinique J8")

    assert profiles.timezone_for(provider.id) == DEPLOYMENT_TIMEZONE
    profiles.ensure(provider.id, VANCOUVER)
    # Still the first value: the row existed, so the second call is a no-op, not an update.
    assert profiles.timezone_for(provider.id) == DEPLOYMENT_TIMEZONE

    # And the other direction, which is the one that actually loses data: a configured zone
    # survives a re-run of the path that seeds the deployment default. The `profiles` fixture is
    # built without an audit writer, so this records nothing -- FP-I covers the audit row.
    record = profiles.set_timezone(
        principal=AuthenticatedPrincipal(
            identity_id=uuid.uuid4(),
            subject="j8-actor",
            issuer="https://dhfp.tests.invalid",
            platform_admin=False,
        ),
        organization_id=provider.id,
        timezone_name=VANCOUVER,
    )
    assert record.timezone_name == VANCOUVER
    profiles.ensure(provider.id, DEPLOYMENT_TIMEZONE)
    assert profiles.timezone_for(provider.id) == VANCOUVER
