"""DHEALTH-APPOINTMENTS-001: clinic appointments, and the arrival that turns one into a visit.

An appointment answers *"when is this patient expected?"*. A `Visit` answers *"the patient arrived
and clinical work is happening"*. This module owns the first question and deliberately owns no part
of the second: arrival goes through `VisitRepository.start_in_session`, so every rule about
attendance stays in `exchange/visits.py` and is enforced exactly once.

── The one invariant worth stating twice ──

**Check-in is one transaction.** It locks the booking, decides inside the lock, creates the visit
through the existing visit path, links the booking to it, and audits -- or it does none of those.
The state the ticket names as unacceptable, a visit that exists while its booking still reads
unarrived, cannot be produced by a successful call or by two concurrent ones. The database says the
same thing
independently: `ck_exchange_appointments_cancelled_has_no_visit` and
`uq_exchange_appointments_visit_id`.

── Time ──

A receptionist types a wall clock. This module converts it, **server-side**, in the deployment
timezone (`config.DEPLOYMENT_TIMEZONE`) and stores a UTC instant. The browser's own zone never
participates in deciding when a patient is expected, and "today" means the clinic's today -- which
is why `day_bounds` exists rather than a `date(now)` anywhere in the frontend.

── What this module refuses to invent ──

No capacity (two patients may share an instant -- without a duration or a resource model, "10:00 is
full" would be a lie). No no-show (a judgement, not a timestamp comparison -- ADR-073 Human Decision
4). No reminder, no slot, no recurring booking, no clinician assignment, no duration, no free-text
reason. Each is argued in ADR-073 §6.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.models import (
    Appointment,
    AppointmentStatus,
    OrganizationType,
    Patient,
    Visit,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import (
    ExchangeSecurityAuditRepository,
    SecurityEventType,
    SecurityOutcome,
)
from daher_adapter.exchange.visits import (
    ActiveVisitAlreadyExists,
    StartVisitResult,
    VisitRepository,
)

__all__ = [
    "AppointmentAlreadyArrived",
    "AppointmentCancelled",
    "AppointmentCheckInResult",
    "AppointmentNotFound",
    "AppointmentPatientNotFound",
    "AppointmentRecord",
    "AppointmentRepository",
    "ActiveVisitAlreadyExists",
    "day_bounds",
]


class AppointmentNotFound(RuntimeError):
    """No such booking **at this organization**.

    One exception for "does not exist" and "belongs to another clinic", deliberately, matching
    `VisitNotFound` and `LabOrderRepository.get_for_organization`: a guessed id must not be able to
    tell the difference, or the error itself becomes an enumeration oracle."""


class AppointmentPatientNotFound(RuntimeError):
    """The patient is not in this clinic's directory. Patient references do not cross tenants."""


class AppointmentCancelled(RuntimeError):
    """The booking is cancelled, and the requested act needs it not to be.

    A *domain conflict*, not an authorization failure: the caller may well be allowed to do this to
    a live booking. Surfaces as 409."""


class AppointmentAlreadyArrived(RuntimeError):
    """The booking already produced a visit, and the requested act would rewrite that.

    Rescheduling or cancelling an arrival that has happened is refused rather than silently
    mutating the clinical record -- the ticket's "do not allow cancelling the Appointment to erase
    or mutate the clinical Visit". Surfaces as 409."""


@dataclass(frozen=True, slots=True)
class AppointmentRecord:
    """One booking, as every caller sees it.

    `patient_display_name`/`patient_date_of_birth` are read through `patient_id` at query time and
    are **current directory truth, never a scheduling snapshot** -- a name corrected today shows
    corrected on every past booking. ADR-073 §7 says so explicitly, in contrast to a released
    laboratory report (ADR-072), which genuinely is frozen.

    `visit_id` non-`None` is the whole of "this booking produced an encounter"; `visit_status` is
    the linked visit's own current state, which is the authoritative record of what happened at it.
    Nothing here is a second copy of attendance.
    """

    id: uuid.UUID
    provider_organization_id: uuid.UUID
    patient_id: uuid.UUID
    patient_display_name: str | None
    patient_date_of_birth: date | None
    scheduled_at: datetime
    status: str
    visit_id: uuid.UUID | None
    visit_status: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AppointmentCheckInResult:
    """The booking and the visit it is now linked to.

    `visit_created` is `False` on a replay -- a double click, a retry, a second receptionist
    pressing the same button. The booking was already linked, so the *existing* visit is returned
    rather than a second one created, which is the ticket's "same logical check-in should return/
    reveal the existing Visit"."""

    appointment: AppointmentRecord
    visit: StartVisitResult
    visit_created: bool


def day_bounds(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    """The half-open UTC instant range covering one **clinic-local** calendar day.

    `[local midnight, next local midnight)`, converted to UTC. Half-open rather than inclusive so
    an appointment at exactly midnight belongs to one day and not two.

    This function is the whole reason a day view cannot drift: "today" is resolved from the
    facility's timezone on the server, so a browser in another zone asking for the same date gets
    the same bookings. Africa/Djibouti is UTC+3 with no DST observance, so no transition is
    exercised today -- the arithmetic goes through `ZoneInfo` rather than a fixed offset so that it
    stays correct if that ever changes, but **DST handling is untested rather than proven**.
    """
    zone = ZoneInfo(timezone_name)
    start_local = datetime.combine(day, time.min, tzinfo=zone)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def instant_from_local(day: date, at: time, timezone_name: str) -> datetime:
    """Turn a clinic-local wall clock into the stored UTC instant.

    The server does this and the browser never does. A `new Date(date + 'T' + time)` in the
    frontend would interpret the receptionist's "10:00" in *the viewer's* zone, so the same booking
    would land on a different instant depending on who made it -- and on a laptop set to UTC it
    would be three hours out in Djibouti. `NC-AP-14` breaks exactly this.
    """
    return datetime.combine(day, at, tzinfo=ZoneInfo(timezone_name)).astimezone(UTC)


class AppointmentRepository:
    """Four mutations and two reads. Every mutation is one transaction that locks the booking row,
    decides inside the lock, and writes its audit row in that same transaction -- the discipline
    `VisitRepository` states for itself and this module inherits deliberately."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        organizations: OrganizationRepository,
        visits: VisitRepository,
        security_audit: ExchangeSecurityAuditRepository,
        facility_timezone: str,
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations
        self._visits = visits
        self._audit = security_audit
        self._timezone = facility_timezone

    @property
    def facility_timezone(self) -> str:
        """The timezone every wall clock in this module is interpreted in.

        Exposed so the API can tell the frontend which zone to *format* in, rather than the
        frontend assuming the viewer's own."""
        return self._timezone

    # -- creation ---------------------------------------------------------------------------------

    def create(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        scheduled_date: date,
        scheduled_time: time,
        idempotency_key: str,
    ) -> tuple[AppointmentRecord, bool]:
        """Book a patient. Returns `(record, created)`.

        `created=False` is an idempotent replay of the *same* request -- the same
        `(organization, idempotency_key)` -- which is a retry after a lost response or a double
        click, not a second booking. Deliberately **not** keyed on the patient: the same patient
        booking another appointment on another date is ordinary clinic work, and a unique patient
        key would make the common case impossible while calling it idempotency.

        A past instant is **accepted**. No refusal is invented here: backfilling a booking somebody
        took by telephone yesterday is legitimate, and "may a clinic book in the past" is a policy
        question (ADR-073 Human Decision 10) rather than something to guess at in a validator.
        """
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        scheduled_at = instant_from_local(scheduled_date, scheduled_time, self._timezone)

        with self._sessions.begin() as session:
            # Appointments exist at providers. A laboratory has no outpatient consultation to book,
            # the same check `visits.start_in_session` makes for the same reason.
            self._organizations.require_type(provider_organization_id, OrganizationType.PROVIDER)

            patient = session.get(Patient, patient_id)
            if patient is None or patient.provider_organization_id != provider_organization_id:
                raise AppointmentPatientNotFound("patient not found in this provider organization")

            existing = session.scalar(
                select(Appointment).where(
                    Appointment.provider_organization_id == provider_organization_id,
                    Appointment.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return self._record_for(session, existing), False

            appointment = Appointment(
                idempotency_key=idempotency_key,
                provider_organization_id=provider_organization_id,
                patient_id=patient_id,
                scheduled_at=scheduled_at,
                status=AppointmentStatus.SCHEDULED.value,
                created_by_identity_id=principal.identity_id,
            )
            # SAVEPOINT for the same reason `visits.start_in_session` uses one: the pre-read above
            # is an optimisation, and two simultaneous retries of the same request both pass it.
            # Only `uq_exchange_appointments_provider_idempotency_key` can refuse the second write,
            # and unwinding to here turns that into a replay rather than a 500.
            try:
                with session.begin_nested():
                    session.add(appointment)
                    session.flush()
            except IntegrityError:
                if appointment in session:
                    session.expunge(appointment)
                committed = session.scalar(
                    select(Appointment).where(
                        Appointment.provider_organization_id == provider_organization_id,
                        Appointment.idempotency_key == idempotency_key,
                    )
                )
                if committed is not None:
                    return self._record_for(session, committed), False
                # Not the idempotency rule. A composite-foreign-key or CHECK violation must surface
                # rather than be reported as a conflict it is not.
                raise

            self._audit_event(
                session,
                event_type="appointment_created",
                principal=principal,
                appointment=appointment,
                outcome="created",
            )
            return self._record_for(session, appointment), True

    # -- scheduling changes -----------------------------------------------------------------------

    def reschedule(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
        scheduled_date: date,
        scheduled_time: time,
    ) -> AppointmentRecord:
        """Move a booking that has not arrived. Same row, new instant.

        **Not** a cancel-and-replace: a terminal `RESCHEDULED` state plus a replacement row would
        make one booking look like two in every day view. The audit records that it moved, by whom
        and when -- but, because this table has no value column, **not what it moved from**. That
        limitation is stated in `security_audit.py` and ADR-073 rather than implied away.

        Refused once the booking has produced a visit: arrival is history and scheduling must not
        rewrite it.
        """
        scheduled_at = instant_from_local(scheduled_date, scheduled_time, self._timezone)
        with self._sessions.begin() as session:
            appointment = self._lock(session, provider_organization_id, appointment_id)
            self._require_mutable(appointment)
            appointment.scheduled_at = scheduled_at
            session.flush()
            self._audit_event(
                session,
                event_type="appointment_rescheduled",
                principal=principal,
                appointment=appointment,
                outcome="updated",
            )
            return self._record_for(session, appointment)

    def cancel(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
    ) -> AppointmentRecord:
        """The patient is no longer expected. **The row is never deleted.**

        Cancelling a booking is not cancelling a visit, and before arrival there is no visit to
        cancel -- this creates none and touches none. Re-cancelling an already-cancelled booking
        returns it unchanged rather than erroring: the caller's intent is already satisfied, and a
        409 there would make a double-clicked cancel look like a failure.

        Refused once the booking has produced a visit. The ticket is explicit that cancellation
        must not erase or mutate the clinical visit, and
        `ck_exchange_appointments_cancelled_has_no_visit` says the same thing in the schema.
        """
        with self._sessions.begin() as session:
            appointment = self._lock(session, provider_organization_id, appointment_id)
            if appointment.visit_id is not None:
                raise AppointmentAlreadyArrived(
                    "this appointment has already produced a visit and cannot be cancelled"
                )
            if appointment.status == AppointmentStatus.CANCELLED.value:
                return self._record_for(session, appointment)
            appointment.status = AppointmentStatus.CANCELLED.value
            appointment.cancelled_at = datetime.now(UTC)
            appointment.cancelled_by_identity_id = principal.identity_id
            session.flush()
            self._audit_event(
                session,
                event_type="appointment_cancelled",
                principal=principal,
                appointment=appointment,
                outcome="updated",
            )
            return self._record_for(session, appointment)

    # -- arrival ----------------------------------------------------------------------------------

    def check_in(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
    ) -> AppointmentCheckInResult:
        """The expected patient arrived. **One transaction, or nothing.**

        Order matters and is the whole design:

        1. lock the booking (`FOR UPDATE`) -- so a concurrent cancel, reschedule or second check-in
           waits rather than interleaving;
        2. refuse a cancelled booking;
        3. if already linked, return the **existing** visit (`visit_created=False`) -- the retry
           case, which must not produce a second encounter;
        4. create the visit through `VisitRepository.start_in_session`, which enforces every
           arrival rule itself and writes its own `visit_created` audit row;
        5. link the booking to it;
        6. audit `appointment_checked_in`;
        7. commit.

        Steps 4-6 share the caller's transaction, so there is no window in which the visit exists
        and the booking still reads unarrived.

        `reason_for_visit` is **not** passed: a front-desk scheduling note must not silently become
        clinical documentation, and this booking carries no reason to pass anyway (ADR-073 §6).

        The visit's idempotency key is derived from the booking's own id, so a retry replays
        through *both* this row lock and `uq_exchange_visits_provider_idempotency_key` -- belt and
        braces for the one operation the ticket most wants to be retry-safe.

        A booked patient who already walked in and was checked in as a walk-in raises
        `ActiveVisitAlreadyExists` (409) from the visit layer, and the booking stays unlinked.
        Whether the product should instead link the booking to that existing active visit is
        ADR-073 Human Decision 11 -- guessing would mean claiming a booking produced an encounter
        it did not.
        """
        with self._sessions.begin() as session:
            appointment = self._lock(session, provider_organization_id, appointment_id)
            if appointment.status == AppointmentStatus.CANCELLED.value:
                raise AppointmentCancelled("this appointment is cancelled and cannot be checked in")

            if appointment.visit_id is not None:
                existing = session.get(Visit, appointment.visit_id)
                if existing is None:  # pragma: no cover - RESTRICT makes this unreachable
                    raise AppointmentNotFound("appointment not found")
                patient = session.get(Patient, appointment.patient_id)
                if patient is None:  # pragma: no cover - RESTRICT makes this unreachable
                    raise AppointmentNotFound("appointment not found")
                return AppointmentCheckInResult(
                    appointment=self._record_for(session, appointment),
                    visit=StartVisitResult(visit=_visit_record(existing, patient), created=False),
                    visit_created=False,
                )

            started = self._visits.start_in_session(
                session,
                principal=principal,
                provider_organization_id=provider_organization_id,
                patient_id=appointment.patient_id,
                reason_for_visit=None,
                idempotency_key=f"appointment:{appointment.id}",
            )
            appointment.visit_id = started.visit.id
            session.flush()
            self._audit_event(
                session,
                event_type="appointment_checked_in",
                principal=principal,
                appointment=appointment,
                outcome="updated",
            )
            return AppointmentCheckInResult(
                appointment=self._record_for(session, appointment),
                visit=started,
                visit_created=started.created,
            )

    # -- reads ------------------------------------------------------------------------------------

    def list_for_day(
        self, provider_organization_id: uuid.UUID, day: date
    ) -> list[AppointmentRecord]:
        """One clinic's bookings for one **clinic-local** day, earliest first.

        Cancelled bookings are included: they are history, the caller decides how to present them,
        and a read that hid them would make "was this cancelled or never booked?" unanswerable from
        the day view. The API's own projection marks them, and the UI does not offer them an arrival
        action."""
        start, end = day_bounds(day, self._timezone)
        with self._sessions() as session:
            rows = session.scalars(
                select(Appointment)
                .where(
                    Appointment.provider_organization_id == provider_organization_id,
                    Appointment.scheduled_at >= start,
                    Appointment.scheduled_at < end,
                )
                .order_by(Appointment.scheduled_at.asc(), Appointment.id.asc())
            ).all()
            return [self._record_for(session, row) for row in rows]

    def get_for_organization(
        self, provider_organization_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> AppointmentRecord | None:
        """One booking, tenant-scoped. `None` for both "no such booking" and "another clinic's" --
        the caller turns that into a 404 without distinguishing them."""
        with self._sessions() as session:
            appointment = session.get(Appointment, appointment_id)
            if (
                appointment is None
                or appointment.provider_organization_id != provider_organization_id
            ):
                return None
            return self._record_for(session, appointment)

    # -- internals --------------------------------------------------------------------------------

    def _lock(
        self, session: Session, provider_organization_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> Appointment:
        """Take the row lock, then resolve the tenant boundary -- in that order.

        `with_for_update` is a no-op on SQLite, so every concurrency property this gives is
        qualified against real PostgreSQL rather than asserted by the unit suite."""
        appointment = session.scalar(
            select(Appointment).where(Appointment.id == appointment_id).with_for_update()
        )
        if appointment is None or appointment.provider_organization_id != provider_organization_id:
            raise AppointmentNotFound("appointment not found")
        return appointment

    def _require_mutable(self, appointment: Appointment) -> None:
        """A booking may be moved only while it is still merely expected."""
        if appointment.visit_id is not None:
            raise AppointmentAlreadyArrived(
                "this appointment has already produced a visit and cannot be rescheduled"
            )
        if appointment.status == AppointmentStatus.CANCELLED.value:
            raise AppointmentCancelled("this appointment is cancelled and cannot be rescheduled")

    def _record_for(self, session: Session, appointment: Appointment) -> AppointmentRecord:
        patient = session.get(Patient, appointment.patient_id)
        visit = (
            session.get(Visit, appointment.visit_id) if appointment.visit_id is not None else None
        )
        return AppointmentRecord(
            id=appointment.id,
            provider_organization_id=appointment.provider_organization_id,
            patient_id=appointment.patient_id,
            patient_display_name=None if patient is None else patient.display_name,
            patient_date_of_birth=None if patient is None else patient.date_of_birth,
            scheduled_at=appointment.scheduled_at,
            status=appointment.status,
            visit_id=appointment.visit_id,
            visit_status=None if visit is None else visit.status,
            created_at=appointment.created_at,
        )

    def _audit_event(
        self,
        session: Session,
        *,
        event_type: SecurityEventType,
        principal: AuthenticatedPrincipal,
        appointment: Appointment,
        outcome: SecurityOutcome,
    ) -> None:
        self._audit.record(
            session,
            event_type=event_type,
            actor_identity_id=principal.identity_id,
            organization_id=appointment.provider_organization_id,
            resource_type="appointment",
            resource_id=appointment.id,
            outcome=outcome,
        )


def _visit_record(visit: Visit, patient: Patient):
    """Build the visit record for a replayed check-in without duplicating `visits.py`'s mapper."""
    from daher_adapter.exchange.visits import _to_record

    return _to_record(visit, patient)
