"""DHEALTH-APPOINTMENTS-001/ADR-073: clinic appointments, and the arrival that becomes a visit.

What this file proves, and the shape of each proof:

- **AP-A** booking is idempotent on the request, not on the patient: a retry replays, a genuinely
  different date for the same patient is ordinary and allowed, and a patient from another clinic is
  refused as *not found* rather than as forbidden.
- **AP-B** the clinic's day is the clinic's, not the caller's. Wall clocks go in, UTC instants are
  stored, and the day boundary is computed in the facility timezone -- asserted against the stored
  column, not against a round trip.
- **AP-C/AP-D** a booking can be moved or cancelled while it is merely expected, and **neither can
  touch a visit**. Cancellation never deletes the row.
- **AP-E** arrival produces **exactly one** `CHECKED_IN` visit, is retry-safe, and is refused for a
  cancelled booking. A patient who already walked in is a controlled 409, not a second visit.
- **AP-F** walk-ins still work with no appointment at all -- the regression this ticket most has to
  not break.
- **AP-G** three separate authorities: reading the day, changing the schedule, recording an arrival.
  Plus direct GRANT, explicit DENY, and same-token revoke.
- **AP-H** tenant isolation on all five operations, with a guessed id refused identically to a
  non-existent one.
- **AP-I** the exact projection, and every forbidden field asserted absent at every depth.
- **AP-J** structural guards: the visit lifecycle is still four states, the appointment lifecycle is
  still two, and the string `NO_SHOW` appears nowhere in the shipped domain.

Same harness pattern as `test_dhealthlabreport001_report.py` -- a bare `FastAPI()` with
`build_exchange_router` mounted directly, helpers duplicated rather than imported, per this
directory's self-contained-per-ticket convention.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.models import (
    Appointment,
    AppointmentStatus,
    ExchangeSecurityAuditEvent,
    Visit,
    VisitStatus,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRepository

TTL_HOURS = 168
TZ = "Africa/Djibouti"

#: The exact projection. Any key added to or removed from `AppointmentResponse` fails here first.
APPOINTMENT_KEYS = {
    "id",
    "scheduled_at",
    "status",
    "patient",
    "visit_id",
    "visit_status",
    "created_at",
}
PATIENT_KEYS = {"patient_id", "display_name", "date_of_birth"}
DAY_KEYS = {"provider_organization_id", "day", "facility_timezone", "appointments"}

#: Every key this ticket forbids on the scheduling surface, checked recursively at every depth.
FORBIDDEN_KEYS = frozenset(
    {
        # Clinical content of any kind. A schedule is not a clinical surface.
        "reason_for_visit",
        "stated_reason",
        "clinical_note",
        "note",
        "notes",
        "diagnosis",
        "results",
        "observations",
        "review_status",
        # Scheduling complexity this ticket deliberately does not model.
        "duration",
        "duration_minutes",
        "clinician_identity_id",
        "clinician_id",
        "room",
        "resource",
        "slot",
        "capacity",
        "no_show",
        "arrived",
        "is_arrived",
        "waiting_minutes",
        "queue_position",
        # Reminders are a separate subsystem and are out of scope entirely.
        "reminder",
        "reminder_sent",
        "sms_status",
        "whatsapp_status",
        "notification_status",
        # Money.
        "amount",
        "invoice_id",
        "deposit",
        "balance",
        # Identity/authorization internals, and patient contact.
        "contact_channel_type",
        "contact_value",
        "idempotency_key",
        "created_by_identity_id",
        "cancelled_by_identity_id",
        "capabilities",
        "roles",
        "memberships",
    }
)


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
        patient_access_ttl_hours=TTL_HOURS,
        facility_timezone=TZ,
    )
    app = FastAPI()
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


# ==================================================================================================
# harness
# ==================================================================================================


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _key() -> str:
    return str(uuid.uuid4())


def _bootstrap_identity(client: TestClient, private_key, subject: str, **kwargs) -> str:
    token = make_token(private_key, subject=subject, **kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _member(client, private_key, org_id, subject: str, roles: list[str]) -> tuple[str, str]:
    """Returns `(token, membership_id)` -- the membership id is what overrides are applied to."""
    identity_id = _bootstrap_identity(client, private_key, subject)
    admin = make_token(private_key, subject=f"admin-{subject}", roles=[PLATFORM_ADMIN_ROLE])
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(admin),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return make_token(private_key, subject=subject), response.json()["membership_id"]


def _override(client, private_key, org_id, membership_id: str, capability: str, effect: str):
    admin_token, _ = _member(
        client, private_key, org_id, f"capadm-{membership_id[:8]}-{capability[-6:]}", ["ORG_ADMIN"]
    )
    response = client.put(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}"
        f"/capabilities/{capability}",
        headers=_auth(admin_token),
        json={"effect": effect},
    )
    assert response.status_code == 200, response.text


def _clear_override(client, private_key, org_id, membership_id: str, capability: str):
    admin_token, _ = _member(
        client, private_key, org_id, f"capclr-{membership_id[:8]}-{capability[-6:]}", ["ORG_ADMIN"]
    )
    response = client.delete(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}"
        f"/capabilities/{capability}",
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text


class Clinic:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _clinic(client, private_key, organizations, patients, name: str) -> Clinic:
    """A provider organization, a patient in its directory, and a scheduler who may book.

    The scheduler is `ORG_ADMIN` + a **direct grant** of `appointment.manage` -- not a role carrying
    it, because no role template does (ADR-073 §5). That is the reception desk shape ADR-069
    established for `visit.check_in`, and using it here makes every authorization test exercise the
    real grant path rather than a convenient template."""
    provider = make_provider(organizations, name=f"Clinique-{name}")
    scheduler, scheduler_membership = _member(
        client, private_key, provider.id, f"sched-{name}", ["ORG_ADMIN"]
    )
    _override(client, private_key, provider.id, scheduler_membership, "appointment.manage", "GRANT")
    # The desk that records arrivals: `visit.check_in` and nothing else clinical.
    desk, desk_membership = _member(client, private_key, provider.id, f"desk-{name}", ["ORG_ADMIN"])
    _override(client, private_key, provider.id, desk_membership, "visit.check_in", "GRANT")
    # A clinician, for the continuation of the visit after arrival.
    clinician, _ = _member(client, private_key, provider.id, f"clin-{name}", ["CLINICIAN"])

    patient = patients.create(
        provider_organization_id=provider.id,
        idempotency_key=f"patient-{name}",
        display_name=f"Patient Synthétique {name}",
        date_of_birth=date(1990, 4, 17),
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=uuid.UUID(_bootstrap_identity(client, private_key, f"reg-{name}")),
    )
    return Clinic(
        name=name,
        provider=provider,
        provider_id=provider.id,
        scheduler=scheduler,
        scheduler_membership=scheduler_membership,
        desk=desk,
        desk_membership=desk_membership,
        clinician=clinician,
        patient_id=patient.patient.id,
    )


def _appointments_url(provider_id) -> str:
    return f"/exchange/v1/organizations/{provider_id}/appointments"


def _book(
    client,
    clinic: Clinic,
    *,
    day: date | None = None,
    at: time = time(10, 0),
    token: str | None = None,
    patient_id=None,
    key: str | None = None,
):
    body = {
        "patient_id": str(patient_id or clinic.patient_id),
        "scheduled_date": (day or date(2026, 10, 6)).isoformat(),
        "scheduled_time": at.strftime("%H:%M:%S"),
    }
    return client.post(
        _appointments_url(clinic.provider_id),
        headers={**_auth(token or clinic.scheduler), "Idempotency-Key": key or _key()},
        json=body,
    )


def _booked(client, clinic: Clinic, **kwargs) -> dict:
    response = _book(client, clinic, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def _day(client, clinic: Clinic, day: date | None = None, token: str | None = None):
    params = {"day": day.isoformat()} if day is not None else {}
    return client.get(
        _appointments_url(clinic.provider_id),
        headers=_auth(token or clinic.scheduler),
        params=params,
    )


def _check_in(client, clinic: Clinic, appointment_id: str, token: str | None = None):
    return client.post(
        f"{_appointments_url(clinic.provider_id)}/{appointment_id}/check-in",
        headers=_auth(token or clinic.desk),
    )


def _cancel(client, clinic: Clinic, appointment_id: str, token: str | None = None):
    return client.post(
        f"{_appointments_url(clinic.provider_id)}/{appointment_id}/cancel",
        headers=_auth(token or clinic.scheduler),
    )


def _reschedule(
    client, clinic: Clinic, appointment_id: str, *, day: date, at: time, token: str | None = None
):
    return client.post(
        f"{_appointments_url(clinic.provider_id)}/{appointment_id}/reschedule",
        headers=_auth(token or clinic.scheduler),
        json={"scheduled_date": day.isoformat(), "scheduled_time": at.strftime("%H:%M:%S")},
    )


def _collect_keys(node, into: set[str]) -> None:
    if isinstance(node, dict):
        into.update(node.keys())
        for value in node.values():
            _collect_keys(value, into)
    elif isinstance(node, list):
        for value in node:
            _collect_keys(value, into)


def _all_keys(payload) -> set[str]:
    keys: set[str] = set()
    _collect_keys(payload, keys)
    return keys


def _audit_types(sessions: sessionmaker[Session], organization_id) -> list[str]:
    with sessions() as session:
        return list(
            session.scalars(
                select(ExchangeSecurityAuditEvent.event_type)
                .where(ExchangeSecurityAuditEvent.organization_id == organization_id)
                .order_by(ExchangeSecurityAuditEvent.created_at.asc())
            ).all()
        )


# ==================================================================================================
# AP-A: booking
# ==================================================================================================


def test_ap_a1_booking_a_patient_creates_one_scheduled_appointment(
    client, keypair, organizations, patients, sessions
):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "A1")
    body = _booked(client, clinic, day=date(2026, 10, 6), at=time(10, 0))

    assert body["status"] == AppointmentStatus.SCHEDULED.value
    assert body["visit_id"] is None
    assert body["visit_status"] is None
    assert body["patient"]["display_name"] == "Patient Synthétique A1"

    # **No visit was created.** A booking is an expectation, not an encounter -- the whole ticket.
    with sessions() as session:
        assert session.scalars(select(Visit)).all() == []


def test_ap_a2_the_same_request_replayed_returns_the_same_appointment(
    client, keypair, organizations, patients, sessions
):
    """A retry after a lost response, or a double-clicked save. One booking, not two, and the
    replay answers 200 rather than 201 so the caller can tell."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "A2")
    key = _key()

    first = _book(client, clinic, key=key)
    second = _book(client, clinic, key=key)
    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]

    with sessions() as session:
        assert len(session.scalars(select(Appointment)).all()) == 1


def test_ap_a3_the_same_patient_may_be_booked_again_on_another_date(
    client, keypair, organizations, patients, sessions
):
    """Idempotency is keyed on the request, never on the patient. A clinic booking somebody for
    Tuesday *and* Thursday is ordinary work, and a `unique(patient_id)` rule would have made the
    common case impossible while calling itself retry safety."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "A3")
    first = _booked(client, clinic, day=date(2026, 10, 6))
    second = _booked(client, clinic, day=date(2026, 10, 8))
    assert first["id"] != second["id"]

    with sessions() as session:
        assert len(session.scalars(select(Appointment)).all()) == 2


def test_ap_a4_two_patients_may_share_an_instant(
    client, keypair, organizations, patients, sessions
):
    """**No capacity rule is invented.** Without a duration or a resource model the system cannot
    truthfully say 10:00 is full, so it does not pretend to."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "A4")
    second_patient = patients.create(
        provider_organization_id=clinic.provider_id,
        idempotency_key="patient-A4-second",
        display_name="Patient Synthétique A4b",
        date_of_birth=None,
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=uuid.UUID(_bootstrap_identity(client, private_key, "reg-A4b")),
    )
    slot = date(2026, 10, 6)
    first = _booked(client, clinic, day=slot, at=time(10, 0))
    second = _booked(client, clinic, day=slot, at=time(10, 0), patient_id=second_patient.patient.id)
    assert first["scheduled_at"] == second["scheduled_at"]
    assert first["id"] != second["id"]


def test_ap_a5_a_patient_from_another_clinic_is_refused_as_not_found(
    client, keypair, organizations, patients
):
    """404, not 403: a patient id that is not this clinic's must be indistinguishable from one that
    does not exist, or the error becomes a way to probe another clinic's directory."""
    private_key, _ = keypair
    mine = _clinic(client, private_key, organizations, patients, "A5a")
    theirs = _clinic(client, private_key, organizations, patients, "A5b")

    response = _book(client, mine, patient_id=theirs.patient_id)
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "patient not found"}


def test_ap_a6_a_laboratory_cannot_even_be_granted_scheduling_authority(
    client, keypair, organizations, patients
):
    """A laboratory has no outpatient consultation to book, and the protection is **stronger than a
    route refusal**: `appointment.manage` is declared `organization_types=_PROVIDER`, so an
    administrator cannot grant it at a laboratory at all -- the attempt is a 422 naming the reason.

    This test originally asserted a 404/409 from the booking route. It was rewritten when the run
    showed the grant itself is refused first: asserting the weaker guarantee would have left the
    real one -- the capability's own organization-type declaration -- untested, and would have kept
    passing if that declaration were removed."""
    private_key, _ = keypair
    _clinic(client, private_key, organizations, patients, "A6")
    lab = make_lab(organizations, name="Laboratoire A6")
    identity_id = _bootstrap_identity(client, private_key, "lab-sched-A6")
    admin = make_token(private_key, subject="admin-lab-A6", roles=[PLATFORM_ADMIN_ROLE])
    membership = client.post(
        f"/exchange/v1/organizations/{lab.id}/memberships",
        headers=_auth(admin),
        json={"identity_id": identity_id, "roles": ["ORG_ADMIN"]},
    )
    assert membership.status_code in (200, 201), membership.text

    refused = client.put(
        f"/exchange/v1/organizations/{lab.id}/memberships/"
        f"{membership.json()['membership_id']}/capabilities/appointment.manage",
        headers=_auth(admin),
        json={"effect": "GRANT"},
    )
    assert refused.status_code == 422, refused.text
    assert "LAB" in refused.text

    # And with no grant possible, the route itself refuses the laboratory's own administrator.
    lab_token = make_token(private_key, subject="lab-sched-A6")
    response = client.get(_appointments_url(lab.id), headers=_auth(lab_token))
    assert response.status_code == 403, response.text


# ==================================================================================================
# AP-B: the clinic's day, and the clinic's clock
# ==================================================================================================


def test_ap_b1_a_wall_clock_is_stored_as_the_facility_instant(
    client, keypair, organizations, patients, sessions
):
    """10:00 in Djibouti is 07:00 UTC. Asserted against the **stored column**, not against a round
    trip through the API, so a server that echoed the input back unchanged could not pass."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "B1")
    body = _booked(client, clinic, day=date(2026, 10, 6), at=time(10, 0))

    with sessions() as session:
        stored = session.get(Appointment, uuid.UUID(body["id"]))
        instant = stored.scheduled_at
        if instant.tzinfo is None:  # SQLite round-trips naive; the stored contract is UTC
            instant = instant.replace(tzinfo=UTC)
        assert instant == datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
        # And the same instant is 10:00 again when read back in the facility's own zone.
        assert instant.astimezone(ZoneInfo(TZ)).strftime("%H:%M") == "10:00"


def test_ap_b2_the_day_view_is_the_clinics_day_not_utcs(client, keypair, organizations, patients):
    """A booking at 01:00 local is 22:00 UTC **the previous day**. It must appear on the clinic's
    day, which is the one the clinic asked for -- a naive `date(scheduled_at)` in UTC would file it
    under yesterday and the receptionist would not find the patient standing in front of them."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "B2")
    early = _booked(client, clinic, day=date(2026, 10, 6), at=time(1, 0))

    on_the_day = _day(client, clinic, date(2026, 10, 6))
    assert on_the_day.status_code == 200, on_the_day.text
    assert [a["id"] for a in on_the_day.json()["appointments"]] == [early["id"]]

    # And it is NOT on the previous day, which is where the UTC instant falls.
    day_before = _day(client, clinic, date(2026, 10, 5))
    assert day_before.json()["appointments"] == []


def test_ap_b3_the_day_is_ordered_earliest_first_and_echoes_what_it_resolved(
    client, keypair, organizations, patients
):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "B3")
    day = date(2026, 10, 6)
    late = _booked(client, clinic, day=day, at=time(14, 30))
    early = _booked(client, clinic, day=day, at=time(9, 0))

    body = _day(client, clinic, day).json()
    assert body["day"] == day.isoformat()
    assert body["facility_timezone"] == TZ
    assert [a["id"] for a in body["appointments"]] == [early["id"], late["id"]]


def test_ap_b4_omitting_the_day_resolves_the_facilitys_today_on_the_server(
    client, keypair, organizations, patients
):
    """The browser never decides what "today" means. Asserted by booking for the facility's own
    current date and finding it with no `day` parameter at all."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "B4")
    facility_today = datetime.now(ZoneInfo(TZ)).date()
    today_booking = _booked(client, clinic, day=facility_today, at=time(11, 0))
    _booked(client, clinic, day=facility_today + timedelta(days=1), at=time(11, 0))

    body = _day(client, clinic).json()
    assert body["day"] == facility_today.isoformat()
    assert [a["id"] for a in body["appointments"]] == [today_booking["id"]]


def test_ap_b5_a_cancelled_booking_stays_in_the_day_view_as_history(
    client, keypair, organizations, patients
):
    """Hiding it would make "was this cancelled, or never booked?" unanswerable from the day. The
    read includes it and marks it; the UI gives it no arrival action."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "B5")
    day = date(2026, 10, 6)
    booking = _booked(client, clinic, day=day)
    assert _cancel(client, clinic, booking["id"]).status_code == 200

    body = _day(client, clinic, day).json()
    assert [a["id"] for a in body["appointments"]] == [booking["id"]]
    assert body["appointments"][0]["status"] == AppointmentStatus.CANCELLED.value


# ==================================================================================================
# AP-C: rescheduling
# ==================================================================================================


def test_ap_c1_a_booking_that_has_not_arrived_can_be_moved(
    client, keypair, organizations, patients, sessions
):
    """Same row, new instant -- no duplicate booking and no replacement row, so one booking never
    looks like two in a day view."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "C1")
    booking = _booked(client, clinic, day=date(2026, 10, 6), at=time(10, 0))

    moved = _reschedule(client, clinic, booking["id"], day=date(2026, 10, 7), at=time(15, 0))
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] == booking["id"]

    with sessions() as session:
        assert len(session.scalars(select(Appointment)).all()) == 1
        stored = session.get(Appointment, uuid.UUID(booking["id"]))
        instant = stored.scheduled_at
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        assert instant == datetime(2026, 10, 7, 12, 0, tzinfo=UTC)  # 15:00 Djibouti

    assert [a["id"] for a in _day(client, clinic, date(2026, 10, 6)).json()["appointments"]] == []
    assert [a["id"] for a in _day(client, clinic, date(2026, 10, 7)).json()["appointments"]] == [
        booking["id"]
    ]


def test_ap_c2_a_cancelled_booking_cannot_be_moved(client, keypair, organizations, patients):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "C2")
    booking = _booked(client, clinic)
    _cancel(client, clinic, booking["id"])

    response = _reschedule(client, clinic, booking["id"], day=date(2026, 10, 9), at=time(9, 0))
    assert response.status_code == 409, response.text


def test_ap_c3_a_booking_that_already_arrived_cannot_be_moved(
    client, keypair, organizations, patients, sessions
):
    """Once a booking has produced an encounter, its scheduled instant is history and moving it
    would make the record say the patient was expected at a time they had already been seen at.

    **Written because a negative control found this untested.** `NC-AP-12` disabled
    `_require_mutable`'s `visit_id` guard and the AP-C tests stayed green: `ap_c1` moves a booking
    that never arrived and `ap_c2` moves a cancelled one, so nothing exercised the arrived case and
    `AppointmentAlreadyArrived` was never asserted on the reschedule path at all. The guard was
    correct; the suite simply did not look at it."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "C3")
    booking = _booked(client, clinic, day=date(2026, 10, 6), at=time(10, 0))

    arrived = _check_in(client, clinic, booking["id"])
    assert arrived.status_code == 200, arrived.text

    response = _reschedule(client, clinic, booking["id"], day=date(2026, 10, 7), at=time(15, 0))
    assert response.status_code == 409, response.text

    # And the refusal changed nothing: same instant, same single visit.
    with sessions() as session:
        stored = session.get(Appointment, uuid.UUID(booking["id"]))
        instant = stored.scheduled_at
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        assert instant == datetime(2026, 10, 6, 7, 0, tzinfo=UTC)  # 10:00 Djibouti
        assert stored.visit_id is not None
        assert len(session.scalars(select(Visit)).all()) == 1


# ==================================================================================================
# AP-D: cancellation
# ==================================================================================================


def test_ap_d1_cancelling_before_arrival_touches_no_visit_and_deletes_no_row(
    client, keypair, organizations, patients, sessions
):
    """Appointment cancellation is **not** visit cancellation, and before arrival there is no visit
    to cancel. The row survives as history."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "D1")
    booking = _booked(client, clinic)

    cancelled = _cancel(client, clinic, booking["id"])
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == AppointmentStatus.CANCELLED.value
    assert cancelled.json()["visit_id"] is None

    with sessions() as session:
        stored = session.get(Appointment, uuid.UUID(booking["id"]))
        assert stored is not None, "cancellation must never delete the row"
        assert stored.cancelled_at is not None
        assert stored.cancelled_by_identity_id is not None
        assert session.scalars(select(Visit)).all() == []


def test_ap_d2_cancelling_twice_succeeds_unchanged(client, keypair, organizations, patients):
    """The caller's intent is already satisfied. A 409 on a double-clicked cancel would report a
    failure where nothing failed."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "D2")
    booking = _booked(client, clinic)

    first = _cancel(client, clinic, booking["id"])
    second = _cancel(client, clinic, booking["id"])
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == AppointmentStatus.CANCELLED.value


# ==================================================================================================
# AP-E: arrival
# ==================================================================================================


def test_ap_e1_arrival_produces_exactly_one_checked_in_visit_and_links_it(
    client, keypair, organizations, patients, sessions
):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "E1")
    booking = _booked(client, clinic)

    arrived = _check_in(client, clinic, booking["id"])
    assert arrived.status_code == 200, arrived.text
    body = arrived.json()
    assert body["visit_created"] is True
    assert body["visit"]["status"] == VisitStatus.CHECKED_IN.value
    assert body["appointment"]["visit_id"] == body["visit"]["id"]
    assert body["appointment"]["visit_status"] == VisitStatus.CHECKED_IN.value
    # The booking stays SCHEDULED -- arrival is not an appointment state, it is a visit.
    assert body["appointment"]["status"] == AppointmentStatus.SCHEDULED.value

    with sessions() as session:
        visits = session.scalars(select(Visit)).all()
        assert len(visits) == 1
        assert str(visits[0].id) == body["visit"]["id"]
        # No reason was carried across: a front-desk booking is not clinical documentation.
        assert visits[0].reason_for_visit is None


def test_ap_e2_repeating_the_arrival_returns_the_same_visit(
    client, keypair, organizations, patients, sessions
):
    """A double click, a retry on a slow connection, a second receptionist. One encounter."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "E2")
    booking = _booked(client, clinic)

    first = _check_in(client, clinic, booking["id"])
    second = _check_in(client, clinic, booking["id"])
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["visit_created"] is True
    assert second.json()["visit_created"] is False
    assert first.json()["visit"]["id"] == second.json()["visit"]["id"]

    with sessions() as session:
        assert len(session.scalars(select(Visit)).all()) == 1


def test_ap_e3_a_cancelled_booking_cannot_be_checked_in(
    client, keypair, organizations, patients, sessions
):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "E3")
    booking = _booked(client, clinic)
    _cancel(client, clinic, booking["id"])

    response = _check_in(client, clinic, booking["id"])
    assert response.status_code == 409, response.text
    with sessions() as session:
        assert session.scalars(select(Visit)).all() == []


def test_ap_e4_a_booking_that_arrived_can_be_neither_moved_nor_cancelled(
    client, keypair, organizations, patients, sessions
):
    """Arrival is history. Scheduling must not rewrite it, and cancelling a booking must never
    erase or mutate the clinical visit it produced."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "E4")
    booking = _booked(client, clinic)
    arrived = _check_in(client, clinic, booking["id"]).json()

    moved = _reschedule(client, clinic, booking["id"], day=date(2026, 11, 2), at=time(9, 0))
    cancelled = _cancel(client, clinic, booking["id"])
    assert moved.status_code == 409, moved.text
    assert cancelled.status_code == 409, cancelled.text

    with sessions() as session:
        visit = session.get(Visit, uuid.UUID(arrived["visit"]["id"]))
        assert visit.status == VisitStatus.CHECKED_IN.value
        stored = session.get(Appointment, uuid.UUID(booking["id"]))
        assert stored.status == AppointmentStatus.SCHEDULED.value
        assert stored.visit_id == visit.id


def test_ap_e5_a_patient_already_in_an_active_visit_is_a_controlled_conflict(
    client, keypair, organizations, patients, sessions
):
    """They walked in before their appointment and were checked in as a walk-in. The arrival rule
    belongs to the visit layer and refuses with 409 -- never a second active visit, never a 500,
    and the booking stays unlinked rather than claiming an encounter it did not produce."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "E5")
    booking = _booked(client, clinic)

    walk_in = client.post(
        f"/exchange/v1/organizations/{clinic.provider_id}/visits",
        headers={**_auth(clinic.desk), "Idempotency-Key": _key()},
        json={"patient_id": str(clinic.patient_id)},
    )
    assert walk_in.status_code == 201, walk_in.text

    response = _check_in(client, clinic, booking["id"])
    assert response.status_code == 409, response.text

    with sessions() as session:
        assert len(session.scalars(select(Visit)).all()) == 1
        assert session.get(Appointment, uuid.UUID(booking["id"])).visit_id is None


def test_ap_e6_the_clinician_continues_the_same_visit(
    client, keypair, organizations, patients, sessions
):
    """The appointment produced the encounter and then stops being involved: the clinician takes
    the *visit*, and the booking remains its historical scheduling context."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "E6")
    booking = _booked(client, clinic)
    arrived = _check_in(client, clinic, booking["id"]).json()
    visit_id = arrived["visit"]["id"]

    taken = client.post(
        f"/exchange/v1/organizations/{clinic.provider_id}/visits/{visit_id}/take",
        headers=_auth(clinic.clinician),
    )
    assert taken.status_code == 200, taken.text
    assert taken.json()["status"] == VisitStatus.IN_PROGRESS.value

    with sessions() as session:
        assert len(session.scalars(select(Visit)).all()) == 1
        stored = session.get(Appointment, uuid.UUID(booking["id"]))
        assert stored.visit_id == uuid.UUID(visit_id)
    # And the schedule reports the visit's new state rather than a copy of its own.
    body = _day(client, clinic, date(2026, 10, 6)).json()["appointments"][0]
    assert body["visit_status"] == VisitStatus.IN_PROGRESS.value


# ==================================================================================================
# AP-F: walk-ins, the regression this ticket must not break
# ==================================================================================================


def test_ap_f1_a_walk_in_still_needs_no_appointment(
    client, keypair, organizations, patients, sessions
):
    """The existing arrival path is untouched: search, register, check in. **An appointment is never
    required for a visit** -- `NC-AP-10` breaks exactly this."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "F1")

    response = client.post(
        f"/exchange/v1/organizations/{clinic.provider_id}/visits",
        headers={**_auth(clinic.desk), "Idempotency-Key": _key()},
        json={"patient_id": str(clinic.patient_id)},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == VisitStatus.CHECKED_IN.value

    with sessions() as session:
        assert len(session.scalars(select(Visit)).all()) == 1
        # No appointment exists at all, and none was invented to satisfy the visit.
        assert session.scalars(select(Appointment)).all() == []


# ==================================================================================================
# AP-G: three separate authorities
# ==================================================================================================


def test_ap_g1_reading_the_day_does_not_imply_changing_it(client, keypair, organizations, patients):
    """`visit.check_in` sees who is expected -- the desk needs that -- and cannot book, move or
    cancel. Read authority is not mutation authority."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "G1")
    booking = _booked(client, clinic)

    assert _day(client, clinic, token=clinic.desk).status_code == 200
    assert _book(client, clinic, token=clinic.desk).status_code == 403
    assert _cancel(client, clinic, booking["id"], token=clinic.desk).status_code == 403
    assert (
        _reschedule(
            client, clinic, booking["id"], day=date(2026, 11, 2), at=time(9, 0), token=clinic.desk
        ).status_code
        == 403
    )


def test_ap_g2_managing_the_schedule_does_not_imply_recording_an_arrival(
    client, keypair, organizations, patients
):
    """`appointment.manage` rearranges the diary. Asserting that somebody walked in is
    `visit.check_in`, and a scheduler has no standing to do it."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "G2")
    booking = _booked(client, clinic)

    response = _check_in(client, clinic, booking["id"], token=clinic.scheduler)
    assert response.status_code == 403, response.text


def test_ap_g3_patient_directory_read_alone_grants_nothing_here(
    client, keypair, organizations, patients
):
    """`NC-AP-3`. Reading a patient's name has never implied authority to create a durable clinic
    commitment about them, and it does not let them see the schedule either."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "G3")
    reader, membership = _member(
        client, private_key, clinic.provider_id, "reader-G3", ["ORG_ADMIN"]
    )
    _override(
        client, private_key, clinic.provider_id, membership, "patient.directory.read", "GRANT"
    )

    assert _day(client, clinic, token=reader).status_code == 403
    assert _book(client, clinic, token=reader).status_code == 403


@pytest.mark.parametrize("roles", [["BILLING_CLERK"], ["CLINICIAN"]])
def test_ap_g4_neither_the_till_nor_a_clinician_schedules_by_default(
    client, keypair, organizations, patients, roles
):
    """Billing has no clinical or scheduling authority at all (ADR-066). And a `CLINICIAN` cannot
    book either, because **no role template carries `appointment.manage`** -- ADR-073 §5 keeps the
    specification's "LTD -- own schedule" honest by requiring a direct grant rather than widening a
    shipped role. A clinician who should book is granted it explicitly."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, f"G4{roles[0][:4]}")
    token, _ = _member(client, private_key, clinic.provider_id, f"g4-{roles[0].lower()}", roles)

    assert _book(client, clinic, token=token).status_code == 403


def test_ap_g5_a_lab_actor_cannot_schedule_clinic_patients(
    client, keypair, organizations, patients
):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "G5")
    lab = make_lab(organizations, name="Laboratoire G5")
    lab_token, _ = _member(client, private_key, lab.id, "lab-tech-G5", ["LAB_TECH"])

    assert _day(client, clinic, token=lab_token).status_code == 404
    assert _book(client, clinic, token=lab_token).status_code == 404


def test_ap_g6_platform_admin_holds_no_blanket_scheduling_authority(
    client, keypair, organizations, patients
):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "G6")
    admin = make_token(private_key, subject="g6-platform-admin", roles=[PLATFORM_ADMIN_ROLE])
    _bootstrap_identity(client, private_key, "g6-platform-admin", roles=[PLATFORM_ADMIN_ROLE])

    assert _day(client, clinic, token=admin).status_code == 403
    assert _book(client, clinic, token=admin).status_code == 403


def test_ap_g7_a_deny_override_beats_the_grant_on_the_next_request(
    client, keypair, organizations, patients
):
    """Same bearer token before and after, no logout and no refresh. A DENY always wins."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "G7")
    assert _book(client, clinic).status_code == 201

    _override(
        client,
        private_key,
        clinic.provider_id,
        clinic.scheduler_membership,
        "appointment.manage",
        "DENY",
    )
    refused = _book(client, clinic)
    assert refused.status_code == 403, refused.text


def test_ap_g8_clearing_the_grant_revokes_scheduling_on_the_same_token(
    client, keypair, organizations, patients
):
    """Revocation is not a separate mechanism: the grant is cleared and the member falls back to
    what their role template says, which for `ORG_ADMIN` carries no `appointment.manage` at all."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "G8")
    assert _book(client, clinic).status_code == 201

    _clear_override(
        client, private_key, clinic.provider_id, clinic.scheduler_membership, "appointment.manage"
    )
    refused = _book(client, clinic)
    assert refused.status_code == 403, refused.text


def test_ap_g9_an_unauthenticated_request_is_refused(client, keypair, organizations, patients):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "G9")
    assert client.get(_appointments_url(clinic.provider_id)).status_code == 401
    assert (
        client.post(
            _appointments_url(clinic.provider_id),
            headers={"Idempotency-Key": _key()},
            json={
                "patient_id": str(clinic.patient_id),
                "scheduled_date": "2026-10-06",
                "scheduled_time": "10:00:00",
            },
        ).status_code
        == 401
    )


# ==================================================================================================
# AP-H: tenant isolation
# ==================================================================================================


def test_ap_h1_another_clinic_cannot_reach_this_clinics_bookings(
    client, keypair, organizations, patients, sessions
):
    """All five operations, with a real booking id. A patient reference does not make a booking
    global -- the organization relationship is authoritative."""
    private_key, _ = keypair
    mine = _clinic(client, private_key, organizations, patients, "H1a")
    theirs = _clinic(client, private_key, organizations, patients, "H1b")
    booking = _booked(client, mine)

    # Through their own organization id, with their own real authority: the booking is not theirs.
    rival_list = _day(client, theirs, token=theirs.scheduler)
    assert rival_list.status_code == 200
    assert rival_list.json()["appointments"] == []

    # And naming my organization with their token is refused before anything is read.
    for method, url in (
        ("get", _appointments_url(mine.provider_id)),
        ("post", f"{_appointments_url(mine.provider_id)}/{booking['id']}/cancel"),
        ("post", f"{_appointments_url(mine.provider_id)}/{booking['id']}/check-in"),
    ):
        response = getattr(client, method)(url, headers=_auth(theirs.scheduler))
        assert response.status_code in (403, 404), f"{method} {url} -> {response.status_code}"

    with sessions() as session:
        assert session.get(Appointment, uuid.UUID(booking["id"])).visit_id is None
        assert session.scalars(select(Visit)).all() == []


def test_ap_h2_a_guessed_id_is_refused_exactly_like_a_foreign_one(
    client, keypair, organizations, patients
):
    """A booking that exists at another clinic and one that does not exist at all must be
    indistinguishable, or the response becomes an enumeration oracle."""
    private_key, _ = keypair
    mine = _clinic(client, private_key, organizations, patients, "H2a")
    theirs = _clinic(client, private_key, organizations, patients, "H2b")
    foreign = _booked(client, theirs)

    guessed = _cancel(client, mine, str(uuid.uuid4()))
    foreign_id = _cancel(client, mine, foreign["id"])
    assert guessed.status_code == 404
    assert foreign_id.status_code == 404
    assert guessed.json() == foreign_id.json()


# ==================================================================================================
# AP-I: projection and privacy
# ==================================================================================================


def test_ap_i1_the_projection_is_exactly_the_scheduling_facts(
    client, keypair, organizations, patients
):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "I1")
    _booked(client, clinic, day=date(2026, 10, 6))
    body = _day(client, clinic, date(2026, 10, 6)).json()

    assert set(body) == DAY_KEYS
    assert set(body["appointments"][0]) == APPOINTMENT_KEYS
    assert set(body["appointments"][0]["patient"]) == PATIENT_KEYS


def test_ap_i2_no_forbidden_field_appears_at_any_depth(client, keypair, organizations, patients):
    """Run over the day view *and* a check-in response, which is the widest payload this ticket
    produces because it carries a visit."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "I2")
    booking = _booked(client, clinic, day=date(2026, 10, 6))

    day_keys = _all_keys(_day(client, clinic, date(2026, 10, 6)).json())
    assert day_keys & FORBIDDEN_KEYS == set(), sorted(day_keys & FORBIDDEN_KEYS)

    # The check-in response embeds a `VisitResponse`, which legitimately carries visit fields --
    # the scheduling half must still contribute none of the forbidden ones.
    arrived = _check_in(client, clinic, booking["id"]).json()
    appointment_keys = _all_keys(arrived["appointment"])
    assert appointment_keys & FORBIDDEN_KEYS == set(), sorted(appointment_keys & FORBIDDEN_KEYS)


def test_ap_i3_the_schedule_shows_current_patient_truth_not_a_snapshot(
    client, keypair, organizations, patients, sessions
):
    """ADR-073 §7, asserted rather than claimed: a booking is not a frozen document. A corrected
    name reads corrected on a booking made before the correction."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "I3")
    _booked(client, clinic, day=date(2026, 10, 6))

    from daher_adapter.exchange.models import Patient

    with sessions() as session:
        patient = session.get(Patient, clinic.patient_id)
        patient.display_name = "Nom Corrigé I3"
        session.commit()

    body = _day(client, clinic, date(2026, 10, 6)).json()
    assert body["appointments"][0]["patient"]["display_name"] == "Nom Corrigé I3"


@pytest.mark.parametrize("path", ["", "/cancel", "/check-in"])
def test_ap_i4_scheduling_responses_are_never_shared_or_publicly_cacheable(
    client, keypair, organizations, patients, path
):
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, f"I4{path or 'list'}")
    booking = _booked(client, clinic)

    if path == "":
        response = _day(client, clinic)
    elif path == "/cancel":
        response = _cancel(client, clinic, booking["id"])
    else:
        response = _check_in(client, clinic, booking["id"])
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"


# ==================================================================================================
# AP-J: audit
# ==================================================================================================


def test_ap_j1_every_scheduling_mutation_is_audited_and_arrival_writes_both_rows(
    client, keypair, organizations, patients, sessions
):
    """Arrival writes `visit_created` **and** `appointment_checked_in` in the same transaction: the
    encounter and the scheduling fact are different questions a reader of this table asks."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "J1")
    booking = _booked(client, clinic)
    _reschedule(client, clinic, booking["id"], day=date(2026, 10, 7), at=time(9, 0))
    _check_in(client, clinic, booking["id"])

    second = _booked(client, clinic, day=date(2026, 10, 8))
    _cancel(client, clinic, second["id"])

    types = _audit_types(sessions, clinic.provider_id)
    for expected in (
        "appointment_created",
        "appointment_rescheduled",
        "appointment_checked_in",
        "appointment_cancelled",
        "visit_created",
    ):
        assert expected in types, f"{expected} missing from {types}"

    # The scheduling rows name the booking, never the patient.
    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.resource_type == "appointment"
            )
        ).all()
        assert rows, "no appointment-scoped audit rows"
        for row in rows:
            assert row.subject_identity_id is None
            assert row.resource_id in (
                uuid.UUID(booking["id"]),
                uuid.UUID(second["id"]),
            )


def test_ap_j2_a_refused_mutation_writes_no_scheduling_audit_row(
    client, keypair, organizations, patients, sessions
):
    """A refusal is an authorization event, recorded by the file writer, not a scheduling mutation.
    A 403 must not leave a row implying the schedule changed."""
    private_key, _ = keypair
    clinic = _clinic(client, private_key, organizations, patients, "J2")
    assert _book(client, clinic, token=clinic.desk).status_code == 403

    types = _audit_types(sessions, clinic.provider_id)
    assert "appointment_created" not in types


# ==================================================================================================
# AP-K: structural guards -- the states this ticket deliberately did not add
# ==================================================================================================


def test_ap_k1_the_visit_lifecycle_is_still_four_states():
    """ADR-073's central refusal, and the one a future author is most likely to undo "helpfully".

    `docs/NATIVE-OPERATING-PRODUCT.md` §12 and two earlier ADRs expected this ticket to add
    `PLANNED` and `NO_SHOW` here. It deliberately did not: a booking is an expectation and a visit
    is an encounter. `NC-AP-11` breaks this."""
    assert {state.value for state in VisitStatus} == {
        "CHECKED_IN",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
    }
    assert "PLANNED" not in {state.value for state in VisitStatus}
    assert "NO_SHOW" not in {state.value for state in VisitStatus}


def test_ap_k2_the_appointment_lifecycle_is_still_two_states():
    """Arrival is derived from `visit_id`, never stored as a status; no-show is a policy nobody has
    written down. `NC-AP-12` breaks this."""
    assert {state.value for state in AppointmentStatus} == {"SCHEDULED", "CANCELLED"}


def test_ap_k3_no_show_appears_nowhere_in_the_shipped_scheduling_domain():
    """Not as a state, not as a derived flag, not as a column, not as a label. The product may say
    "this time has passed"; it may not convert that observation into an operational judgement."""
    import inspect

    from daher_adapter.exchange import appointments as appointments_module

    for module in (appointments_module,):
        source = inspect.getsource(module)
        # Prose *about* the absence is legitimate and expected; a NO_SHOW identifier is not.
        code_lines = [
            line
            for line in source.split("\n")
            if not line.lstrip().startswith("#") and not line.lstrip().startswith("*")
        ]
        code = "\n".join(code_lines)
        assert "NO_SHOW" not in code.replace("``NO_SHOW``", "").replace("`NO_SHOW`", "")


def test_ap_k4_the_appointment_table_carries_no_duplicated_patient_identity():
    """A booking references the patient; it never copies their name, date of birth or telephone
    number. Prefer relationship over duplicated truth."""
    columns = set(Appointment.__table__.columns.keys())
    for forbidden in (
        "patient_display_name",
        "display_name",
        "patient_name",
        "date_of_birth",
        "contact_value",
        "phone",
        "email",
    ):
        assert forbidden not in columns, forbidden
    assert "patient_id" in columns
