"""DHEALTH-FACILITY-PROFILE-001/ADR-074: each facility's own operational profile.

Today that profile carries exactly one fact -- the facility's IANA timezone -- and that is the
point. Before this module, `config.DEPLOYMENT_TIMEZONE` was the authority for every clinic in a
deployment, which meant one Hawva deployment could not truthfully schedule a clinic in Djibouti and
a clinic in Vancouver. See `models.py`'s `FacilityProfile` docstring for why this is a fourth
facility-shaped table rather than a column on `Organization`, `FacilityEnrollment` or
`LabDirectoryProfile`.

── Validation lives here, not in the database ──

`ZoneInfo(name)` is the validator. The IANA database is amended several times a year, so a database
CHECK enumerating today's zone names would be wrong by the next release and would need a migration
to accept a newly created zone; the column only refuses the empty string. Validating in the
application means the answer tracks whatever tz database the runtime actually has -- which is also
the honest limitation, because that database is a property of the deployed image rather than of
this code.

── Two reads, and only one of them locks ──

`timezone_for_conversion_in_session` takes a `FOR SHARE` lock on the profile row and is what the
appointment write path uses. `timezone_for` does not lock and is what a day view or a settings read
uses. The difference is deliberate and is explained at each method.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.models import FacilityProfile, Organization, utc_now
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository

__all__ = [
    "FacilityProfileRecord",
    "FacilityProfileRepository",
    "FacilityTimezoneNotConfigured",
    "InvalidFacilityTimezone",
    "normalize_timezone_name",
]


class InvalidFacilityTimezone(ValueError):
    """The submitted string is not a timezone this runtime's tz database knows.

    Raised for `Mars/Olympus_Mons`, for an offset like `UTC+3` or `+03:00`, and for the empty
    string. An offset is refused deliberately rather than parsed: it carries no daylight-saving rule
    and describes one instant rather than a place, so storing it would make the facility's clock
    wrong twice a year in any zone that transitions.
    """


class FacilityTimezoneNotConfigured(RuntimeError):
    """The organization has no facility profile, so no timezone authority exists for it.

    **Deliberately not a fallback.** An appointment converted under a silently assumed zone is the
    failure this whole ticket exists to end, and it is the kind that surfaces months later as
    appointments at the wrong time rather than immediately as an error somebody can act on.

    In production this should be unreachable: 0041 backfills a row for every organization that
    existed, and `OrganizationRepository.create` writes one for every organization created since.
    It is raised rather than assumed so that if those two guarantees are ever broken, the result is
    a refusal naming the organization instead of a plausible wrong answer.
    """


def normalize_timezone_name(raw: str) -> str:
    """Validate an IANA timezone identifier and return it stripped.

    The only normalization is whitespace: the identifier's own case is **not** altered, because the
    tz database is case-sensitive about the names it ships (`Africa/Djibouti`, not
    `africa/djibouti`) and quietly re-casing a name would turn a typo into a different zone or into
    a lookup failure further away from the user who made it.
    """
    candidate = raw.strip()
    if not candidate:
        raise InvalidFacilityTimezone("a facility timezone is required")
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError) as error:
        # `ValueError` as well as `ZoneInfoNotFoundError`: ZoneInfo rejects an absolute path or a
        # name containing `..` with ValueError rather than NotFound, and both are "not a zone".
        raise InvalidFacilityTimezone(
            f"{candidate!r} is not an IANA timezone identifier this server recognises. Use a name "
            f"from the tz database such as 'Africa/Djibouti' or 'America/Vancouver', never an "
            f"offset like '+03:00' -- an offset carries no daylight-saving rule."
        ) from error
    return candidate


@dataclass(frozen=True, slots=True)
class FacilityProfileRecord:
    """One facility's operational profile, plus the organization name for display.

    `organization_name` is carried here as a read-only convenience for the settings screen so it
    does not need a second request to render a heading. It is **not** part of the profile: the name
    lives on `Organization`, is network participant identity, and this ticket deliberately does not
    make it facility-editable (ADR-074 §7, Human Decision 1).
    """

    organization_id: uuid.UUID
    organization_name: str
    timezone_name: str
    created_at: datetime
    updated_at: datetime


class FacilityProfileRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        security_audit: ExchangeSecurityAuditRepository | None = None,
    ) -> None:
        self._sessions = session_factory
        self._security_audit = security_audit

    # ── reads ────────────────────────────────────────────────────────────────────────────────────

    def get(self, organization_id: uuid.UUID) -> FacilityProfileRecord | None:
        """The profile with its organization's name, or `None` if the organization has none."""
        with self._sessions() as session:
            row = session.execute(
                select(FacilityProfile, Organization)
                .join(Organization, Organization.id == FacilityProfile.organization_id)
                .where(FacilityProfile.organization_id == organization_id)
            ).first()
            if row is None:
                return None
            profile, organization = row
            return FacilityProfileRecord(
                organization_id=profile.organization_id,
                organization_name=organization.name,
                timezone_name=profile.timezone_name,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )

    def timezone_for(self, organization_id: uuid.UUID) -> str:
        """This facility's timezone, for a read-only use such as a day view or resolving "today".

        **No lock, deliberately.** A read does not need serializing against a concurrent settings
        change: the worst outcome is that a day view renders under the zone that was authoritative a
        moment ago, and the next fetch is correct. Only the write path, which converts a wall clock
        into a stored instant, needs the facility's zone to be stable for the length of its
        transaction -- see `timezone_for_conversion_in_session`.
        """
        with self._sessions() as session:
            return self._timezone_in_session(session, organization_id, lock=False)

    def timezone_for_conversion_in_session(
        self, session: Session, organization_id: uuid.UUID
    ) -> str:
        """This facility's timezone, locked `FOR SHARE`, for a caller that is about to convert a
        local wall clock into a stored UTC instant inside this same transaction.

        This is the serialization point for the race ADR-074 §5 describes: a receptionist submitting
        *Tuesday 10:00* while an administrator changes the facility timezone. The settings update
        takes `FOR UPDATE` on this row, so PostgreSQL orders the two:

        * booking reads first -> the settings update waits for the booking to commit, and the
          booking is validated and stored under one timezone, the old one;
        * settings update commits first -> the booking's read returns the new zone, and the booking
          is validated and stored under one timezone, the new one.

        Both outcomes are coherent. The outcome this prevents is the torn one -- a request validated
        under one zone and stored under another -- which is what would happen if the zone were read
        outside the transaction that writes the instant.
        """
        return self._timezone_in_session(session, organization_id, lock=True)

    def _timezone_in_session(
        self, session: Session, organization_id: uuid.UUID, *, lock: bool
    ) -> str:
        statement = select(FacilityProfile).where(
            FacilityProfile.organization_id == organization_id
        )
        if lock:
            # `read=True` is `FOR SHARE`, not `FOR UPDATE`: two concurrent bookings at the same
            # clinic must not block each other, and they do not need to -- neither is changing the
            # profile. Only the settings writer needs exclusivity, and `FOR UPDATE` there conflicts
            # with these shared locks, which is exactly the ordering we want.
            statement = statement.with_for_update(read=True)
        profile = session.scalars(statement).one_or_none()
        if profile is None:
            raise FacilityTimezoneNotConfigured(
                f"organization {organization_id} has no facility profile, so no timezone authority "
                f"exists for it. Hawva refuses to guess: converting a local wall clock under an "
                f"assumed zone would store the wrong instant and nothing would report it. A "
                f"profile is created with every organization and 0041 backfilled every "
                f"pre-existing one, so this indicates a facility created outside those paths."
            )
        return profile.timezone_name

    # ── writes ───────────────────────────────────────────────────────────────────────────────────

    def ensure(
        self,
        organization_id: uuid.UUID,
        timezone_name: str,
        *,
        session: Session | None = None,
    ) -> FacilityProfileRecord | None:
        """Create this organization's profile if it has none; leave an existing one untouched.

        Insert-first-wins, the convention this repository's seeding and membership paths already
        use: calling it twice is a safe no-op rather than a duplicate or an overwrite. It must never
        overwrite, because the existing row may carry a timezone somebody configured deliberately
        and a re-run of organization creation or a seed is not a decision to discard it.

        Takes an optional `session` so organization creation can write the profile in the **same
        transaction** as the organization -- an organization that exists without a profile is a
        facility that cannot schedule, and committing them separately would leave a window where
        that is true. Same `session`/`_in_session` composition as `selection.py` and `visits.py`.
        """
        validated = normalize_timezone_name(timezone_name)
        if session is not None:
            self._ensure_in_session(session, organization_id, validated)
            return None
        with self._sessions.begin() as owned:
            self._ensure_in_session(owned, organization_id, validated)
        return self.get(organization_id)

    def _ensure_in_session(
        self, session: Session, organization_id: uuid.UUID, timezone_name: str
    ) -> None:
        existing = session.scalars(
            select(FacilityProfile).where(FacilityProfile.organization_id == organization_id)
        ).one_or_none()
        if existing is not None:
            return
        session.add(FacilityProfile(organization_id=organization_id, timezone_name=timezone_name))
        session.flush()

    def set_timezone(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        timezone_name: str,
    ) -> FacilityProfileRecord:
        """Change this facility's timezone. Validated, locked, audited.

        **No appointment is rewritten.** `exchange_appointments.scheduled_at` holds the instant
        staff booked under the authority in effect when they booked it; re-deriving those instants
        from a new zone would silently move every historical appointment. What changes is how future
        wall clocks are converted and how stored instants are presented. ADR-074 §4, and `NC-FP-7`
        exists to catch a future change that starts rewriting them.
        """
        validated = normalize_timezone_name(timezone_name)
        with self._sessions.begin() as session:
            # `FOR UPDATE`, which conflicts with the `FOR SHARE` an in-flight booking holds. That is
            # the serialization point described on `timezone_for_conversion_in_session`: this
            # statement waits for a booking that already read the zone, so that booking cannot be
            # validated under one zone and stored under another.
            profile = session.scalars(
                select(FacilityProfile)
                .where(FacilityProfile.organization_id == organization_id)
                .with_for_update()
            ).one_or_none()
            if profile is None:
                raise FacilityTimezoneNotConfigured(
                    f"organization {organization_id} has no facility profile to update"
                )

            profile.timezone_name = validated
            profile.updated_at = utc_now()
            session.flush()

            if self._security_audit is not None:
                # `that it changed`, by whom, when -- not the old and new values. See the event
                # type's own comment in `security_audit.py` and ADR-074 Human Decision 4.
                self._security_audit.record(
                    session,
                    event_type="facility_timezone_changed",
                    actor_identity_id=principal.identity_id,
                    outcome="updated",
                    organization_id=organization_id,
                    resource_type="facility_profile",
                    resource_id=profile.id,
                )

        record = self.get(organization_id)
        assert record is not None  # noqa: S101 -- just updated inside a committed transaction
        return record
