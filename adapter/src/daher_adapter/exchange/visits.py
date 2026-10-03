"""DHEALTH-CLINIC-VISIT-001: the native clinic visit lifecycle.

Before this module, DHealth could record that a diagnostic order existed but not that a consultation
happened. A clinician's own day had no representation in the product at all: no waiting list, no
"who am I seeing now", no place to write down why the patient came.
``docs/NATIVE-OPERATING-PRODUCT.md`` §12 named that the first gap standing between the Native
product and a clinic that could actually stop using its current software, and this module is the
narrowest thing that closes it.

Six operations, and deliberately only six: start a visit, take it, append a note revision, amend the
stated reason, complete it, cancel it. Reads are **four** since ADR-069: the organization's
worklist, one patient's visit history, one visit with its notes, and the narrow arrival
projection (`list_waiting`) that carries no clinical field at all.

── Four states, not five ──

``VisitStatus`` carries the full argument. In short: §12 defines ``PLANNED`` and ``NO_SHOW``, that
same specification's first-ticket brief (§36) omits both until a calendar exists, and no appointment
table exists in this codebase, so nothing could put a visit into either. A state reachable by
nothing is decoration -- §12's own words about the states it rejected.

── Who may act, and the one thing that is refused for a different reason ──

**[Superseded in part, 2026-09-30, DHEALTH-FRONT-DESK-001/ADR-069.** Authority is now
capability-scoped, not role-scoped, and it is no longer one authority. Registering an arrival is
``visit.check_in``; taking a waiting patient, writing a note, amending the reason and completing are
``visit.document``; reading a visit and its notes is ``visit.read``; cancelling an arrival is either
of the first two. A person may hold ``visit.check_in`` alone, put patients into the waiting list,
and be refused every clinical operation and every clinical read in this module -- which is the point
of the split. The paragraph below is otherwise unchanged and still governs: what follows is about
*which* holder of a clinical capability may act on *which* visit, and the answer is still "any of
them, in their own organization".]**

**Authority is organization-scoped, exactly like every other clinical operation in this package.**
Any ACTIVE member with the relevant capability at the provider organization may act on that
organization's visits. This is deliberately not a per-row ownership model: no such
model exists anywhere in this codebase (any clinician may read any patient in their organization and
order for them -- ``exchange/api.py``'s patient directory and order endpoints), and inventing one
here would be the "obviously should" instinct ``AGENTS.md`` names as the failure mode. A real clinic
has covering clinicians, and a product that locks a waiting patient to the one clinician who first
touched their visit fails law 3 (faster than what it replaces) for a case that happens daily.

**What is nonetheless refused: taking a visit another clinician is already seeing.** That refusal is
a *state* conflict, not an authority decision, and the difference matters. ``clinician_identity_id``
is the clinician of record for the encounter; silently overwriting it when a second clinician opens
the visit would rewrite who saw the patient, which is a false clinical record rather than a
permission problem. A covering clinician may still append notes and complete the visit -- their own
identity is recorded on each note revision they write (``VisitNote.author_identity_id``), so the
record stays truthful without needing to be exclusive.

── Nothing here completes itself ──

``COMPLETED`` is reached only by a clinician saying so. §12: "The clinician says so; nothing
auto-completes." In particular **visit completion does not depend on lab completion in any
direction**, and this module contains no reference to lab order status at all. A clinician who has
finished with the patient completes the visit; results arriving days later are the ordering
workflow's business, reachable from the order and from the patient, and a visit left artificially
open until a lab replies would make the waiting list lie about who is in the building.

There is also no requirement that a visit have a note before it can be completed. A required note
would be a clinical documentation policy, and this repository does not invent clinical policy
(``AGENTS.md``). An empty visit is an honest record of a contact nobody wrote about.

── What a note is, and what it is not ──

Notes are append-only revisions (``VisitNote``). Correcting a note appends a new revision; the
highest ``revision_number`` is current and earlier ones remain readable. Nothing is updated in place
and nothing is deleted, which is both the concurrency design (see ``VisitNote``'s docstring: the
unique constraint makes a lost update impossible rather than unlikely) and the same discipline
ADR-045/048 established for corrected and amended lab results.

Notes may be appended only while the visit is ``IN_PROGRESS``. **Amending the note of a completed
visit is deliberately not built.** It is a real clinical need, and it is exactly the kind of need
that has an explicit, audited, versioned amendment workflow in this codebase already (ADR-048's
versioned review for lab results) -- inventing a second, weaker one here, in a ticket whose scope is
the visit lifecycle, would be worse than leaving the gap open and named. It is recorded as a gap in
``docs/NATIVE-OPERATING-PRODUCT.md`` rather than guessed at.

── Concurrency ──

Every transition locks the visit row ``FOR UPDATE`` and re-reads the status *inside* the lock, then
decides. A transition decided against a status read before the lock is precisely the check two
concurrent callers both pass -- two clinicians clicking "terminer" simultaneously would otherwise
both write ``completed_at`` and produce two audit rows for one completion. The row lock is a no-op
on SQLite, which is why the real-PostgreSQL qualification for this ticket exists at all
(``tests/integration/test_dhealthvisit001_postgres_qualification.py``).

Note appends take the same lock, so ``revision_number`` is computed against a serialized view --
with ``uq_exchange_visit_notes_visit_revision`` as the database-level backstop behind it.

── What this module never does ──

No clinical interpretation, no decision support, no alerting, no scoring, no triage, no diagnosis,
no coded clinical content, no vitals, no structured examination, no prescription. It records and it
routes (``AGENTS.md``). No note body is ever written to an audit row, a log line, or an error
message, and no note body ever leaves this system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.models import (
    OrganizationType,
    Patient,
    Visit,
    VisitNote,
    VisitStatus,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import (
    ExchangeSecurityAuditRepository,
    SecurityEventType,
    SecurityOutcome,
)
from daher_adapter.models import utc_now


class VisitNotFound(RuntimeError):
    """No such visit, or it exists and belongs to a different provider organization.

    **One exception for both cases, deliberately.** Distinguishing them would confirm that a visit
    id is real to a clinician in an organization with no claim on it -- the same anti-enumeration
    rule ``exchange/authorization.py``'s ``ResourceNotFound`` and DH-IDENTITY-001 §26 already apply
    to organizations and patients. Maps to HTTP 404."""


class VisitPatientNotFound(RuntimeError):
    """No such native ``Patient`` in this provider organization's own directory. Maps to HTTP 404,
    for the same anti-enumeration reason as ``VisitNotFound``."""


class VisitTransitionRefused(RuntimeError):
    """The requested transition is not legal from the visit's current status, as read inside the
    lock. Maps to HTTP 409: a conflict with the visit's state, not a permission problem. Covers the
    already-completed visit, the already-cancelled visit, the attempt to cancel a visit that is
    already in progress (§12: that is a completed-with-note), and the attempt to write to a terminal
    visit."""


class VisitAlreadyTaken(RuntimeError):
    """The visit is already ``IN_PROGRESS`` under a *different* clinician. Maps to HTTP 409 -- a
    state conflict, not an authorization refusal: see this module's docstring for why overwriting
    the clinician of record is refused while covering care is not."""


class VisitCannotReceiveOrder(RuntimeError):
    """A diagnostic order was raised naming this visit, and the visit cannot carry it: either it is
    not ``IN_PROGRESS``, or it is about a different patient than the order's own patient reference.
    Maps to HTTP 409.

    **Why ``IN_PROGRESS`` specifically**, rather than merely non-terminal: an order raised during a
    visit is a statement that a clinician was with the patient and decided to order something. A
    ``CHECKED_IN`` visit has no clinician yet by construction, so an order attached to one would
    claim a consultation that had not started. Requiring the stronger state also means a cancelled
    visit can never have orders hanging off it -- ``CANCELLED`` is reachable only from
    ``CHECKED_IN``, so the two sets cannot overlap.

    **Why the patient must match:** the visit and the order each name a patient independently (the
    visit a native ``Patient``, the order a ``PatientReference`` that links to one). If those
    disagree, one of them is wrong about who is being treated, and picking a winner silently is
    exactly the kind of identity guesswork ADR-051 forbids."""


class ActiveVisitAlreadyExists(RuntimeError):
    """This patient already has an active visit (``CHECKED_IN`` or ``IN_PROGRESS``) at this provider
    organization. Maps to HTTP 409.

    **Distinct from ``VisitIdempotencyKeyConflict``, and the difference is the whole reason both
    exist.** A replayed idempotency key is the *same* arrival arriving twice, and the right answer
    is
    to hand back the visit that already exists. A *different* key for a patient who is already
    waiting is a *different* arrival for somebody already in the building -- two reception workers
    checking the same person in, or one worker who did not see that a colleague already had.
    Returning the existing visit there would silently tell the second worker their action succeeded,
    and the honest answer is that this patient is already waiting.

    DHEALTH-FRONT-DESK-001/ADR-069. Enforced by ``uq_exchange_visits_one_active_per_patient``, not
    only by the pre-read that raises this first -- see ``VisitRepository.start``."""


class VisitIdempotencyKeyConflict(RuntimeError):
    """This ``(provider_organization_id, idempotency_key)`` pair already started a visit for a
    different patient or with a different stated reason. Maps to HTTP 409, and deliberately does not
    return the existing visit: handing back a *different* patient's visit because a client reused a
    key is the cross-wiring ``LabOrderIntent``'s identical guard exists to prevent."""


@dataclass(frozen=True, slots=True)
class VisitNoteRecord:
    id: uuid.UUID
    revision_number: int
    author_identity_id: uuid.UUID
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VisitRecord:
    """One visit as the database holds it.

    ``patient_display_name``/``patient_date_of_birth`` are carried so a worklist row can identify a
    human without a second round trip per row. Both are deliberately included rather than name
    alone: ADR-051 explicitly allows two patients with identical demographics to coexist, so a
    worklist showing only names can be genuinely ambiguous, and a clinician resolving that ambiguity
    by guessing is the risk the pair removes."""

    id: uuid.UUID
    provider_organization_id: uuid.UUID
    patient_id: uuid.UUID
    patient_display_name: str | None
    patient_date_of_birth: date | None
    clinician_identity_id: uuid.UUID | None
    status: str
    #: ``None`` when nobody stated a reason -- the ordinary case for an arrival registered by
    #: somebody holding only ``visit.check_in`` (ADR-069 §15: a chief complaint typed at reception
    #: is clinical documentation under another name). Never an empty string; the service refuses
    #: one.
    reason_for_visit: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None


@dataclass(frozen=True, slots=True)
class WaitingArrivalRecord:
    """One waiting arrival, as the **arrival** workflow is allowed to see it. Six fields, and the
    absences are the specification.

    DHEALTH-FRONT-DESK-001/ADR-069 §12. A person holding ``visit.check_in`` and no clinical
    capability must be able to identify the right patient and see that they are waiting -- and must
    not be able to read the encounter. So this is a separate, deliberately enumerated projection
    rather than ``VisitRecord`` with fields the frontend happens not to render: "the browser does
    not
    show it" is not a privacy boundary, because the response is what the caller actually receives.

    **Excluded on purpose, each for a stated reason:**

    * ``reason_for_visit`` -- what the patient said about why they are here. The single most
      sensitive field on the row, and the one a waiting list has no operational need for.
    * ``clinician_identity_id`` -- who is treating them. Not needed to run a waiting room, and it
      turns the queue into a record of which clinician sees which patient.
    * ``started_at`` / ``completed_at`` / ``cancelled_at`` -- the consultation's own history.
    * notes, orders, results, review dispositions -- structurally unreachable: this record has no
      field for them and the query that builds it joins no such table.

    **``checked_in_at`` is ``Visit.created_at``**, renamed at the boundary rather than stored twice.
    A visit row is created by the check-in operation and by nothing else, so the moment it was
    created *is* the moment the arrival was recorded -- for every row, including every row that
    predates ADR-069. The name says what the field means to the caller; the column keeps its own.

    **There is no waiting duration here, and that is a recorded decision rather than an omission.**
    ``AGENTS.md`` (2026-09-27) and ``docs/NATIVE-OPERATING-PRODUCT.md`` §38 both forbid deriving a
    waiting time from visit timestamps without the clinical lead's sign-off: a number a clinic would
    act on is an operational signal DHealth must not produce, even when arithmetic produced it. The
    recorded arrival time is a fact and is returned; the subtraction is not performed here, and must
    not be performed in the browser either. See ``PROJECT_STATE.md``'s DHEALTH-FRONT-DESK-001
    blocker."""

    visit_id: uuid.UUID
    patient_id: uuid.UUID
    patient_display_name: str | None
    #: The wrong-patient safeguard. ADR-051 allows two patients with identical names to coexist, so
    #: a queue showing only names can be genuinely ambiguous and a worker resolving that by guessing
    #: is exactly the risk this field removes (`docs/NATIVE-OPERATING-PRODUCT.md` §9).
    patient_date_of_birth: date | None
    #: ``CHECKED_IN`` or ``IN_PROGRESS`` -- coarse state, so the desk can tell "still waiting" from
    #: "already with the clinician" (§23). Nothing finer, and no clinical content either way.
    status: str
    checked_in_at: datetime


@dataclass(frozen=True, slots=True)
class StartVisitResult:
    visit: VisitRecord
    created: bool
    """``False`` means an existing visit for this idempotency key was returned -- the retry or
    duplicate-click case, not an error."""


@dataclass(frozen=True, slots=True)
class VisitTransitionResult:
    visit: VisitRecord
    changed: bool
    """``False`` only for an idempotent replay that committed nothing (taking a visit the same
    clinician already took). Every other no-change case raises instead of reporting success."""


#: The complete legal transition table, as `docs/NATIVE-OPERATING-PRODUCT.md` §12 records it minus
#: the two omitted states. Spelled out as data rather than scattered through six methods so that
#: "which transitions exist" is answerable by reading one object, and so a test can assert the whole
#: machine rather than the paths someone remembered to exercise.
#:
#: The absent entry that matters most: `IN_PROGRESS -> CANCELLED` does not exist. §12 is explicit
#: that a visit already in progress is completed-with-a-note, never cancelled, because contact with
#: the patient happened and no status may erase that.
LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (VisitStatus.CHECKED_IN.value, VisitStatus.IN_PROGRESS.value),
        (VisitStatus.CHECKED_IN.value, VisitStatus.CANCELLED.value),
        (VisitStatus.IN_PROGRESS.value, VisitStatus.COMPLETED.value),
    }
)

#: Statuses from which no transition and no write is possible. Terminal in the strong sense: there
#: is no reopen operation, and adding one would need a decision about what a reopened completed
#: visit means for the record that said it was finished.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {VisitStatus.COMPLETED.value, VisitStatus.CANCELLED.value}
)


def require_visit_can_receive_order(
    session: Session,
    *,
    provider_organization_id: uuid.UUID,
    visit_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> None:
    """Prove that an order intent may name this visit, inside the caller's own transaction.

    A module-level function taking a session rather than a ``VisitRepository`` method, because its
    one caller (``exchange/order_intents.py``) is mid-transaction and must not open a second
    session: a check made in a separate session could pass against a visit that a concurrent
    request completes before the intent row is inserted. Keeping the rule here rather than inlining
    it there means "when may an order name a visit" has one definition, next to the state machine it
    depends on.

    Raises ``VisitNotFound`` (404, anti-enumeration) when the visit does not exist in this
    organization, and ``VisitCannotReceiveOrder`` (409) when it exists but cannot carry the order --
    see that exception for why the bar is ``IN_PROGRESS`` and why the patient must match."""
    row = session.execute(
        select(Visit.status, Visit.patient_id).where(
            Visit.id == visit_id,
            Visit.provider_organization_id == provider_organization_id,
        )
    ).first()
    if row is None:
        raise VisitNotFound("visit not found")
    status, visit_patient_id = row
    if status != VisitStatus.IN_PROGRESS.value:
        raise VisitCannotReceiveOrder(
            "an order can only be raised during a visit that is in progress"
        )
    if visit_patient_id != patient_id:
        raise VisitCannotReceiveOrder("this visit is about a different patient")


def _to_note_record(note: VisitNote) -> VisitNoteRecord:
    return VisitNoteRecord(
        id=note.id,
        revision_number=note.revision_number,
        author_identity_id=note.author_identity_id,
        body=note.body,
        created_at=note.created_at,
    )


def _to_record(visit: Visit, patient: Patient) -> VisitRecord:
    return VisitRecord(
        id=visit.id,
        provider_organization_id=visit.provider_organization_id,
        patient_id=visit.patient_id,
        patient_display_name=patient.display_name,
        patient_date_of_birth=patient.date_of_birth,
        clinician_identity_id=visit.clinician_identity_id,
        status=visit.status,
        reason_for_visit=visit.reason_for_visit,
        created_at=visit.created_at,
        started_at=visit.started_at,
        completed_at=visit.completed_at,
        cancelled_at=visit.cancelled_at,
    )


class VisitRepository:
    """The clinic visit lifecycle. Every mutating method is one transaction that locks the visit
    row, decides inside the lock, and writes its audit row in that same transaction."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        organizations: OrganizationRepository,
        security_audit: ExchangeSecurityAuditRepository,
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations
        self._audit = security_audit

    # -- creation ---------------------------------------------------------------------------------

    def start(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        reason_for_visit: str | None,
        idempotency_key: str,
    ) -> StartVisitResult:
        """Register a patient as present and waiting. ``CHECKED_IN``, with no clinician yet.

        The waiting list *is* this state (§12), which is why no clinician is recorded: at check-in,
        who will see the patient is genuinely not known, and a placeholder would be a guess written
        into a clinical record. DHEALTH-FRONT-DESK-001/ADR-069 makes that the *operative* reason
        rather than an incidental one: the caller here may hold ``visit.check_in`` and nothing
        clinical, so the clinician of record is not merely unknown -- the caller has no standing to
        name one, and the endpoint accepts no clinician identity from the request at all.

        ``reason_for_visit`` is **optional**. ``None`` records that nobody stated one, which is the
        ordinary outcome at a reception desk (§15). A string that is empty once stripped is refused
        rather than quietly stored as ``None``: "nobody was asked" and "somebody submitted an empty
        field" are different, and only the first is a fact worth recording.

        Two distinct conflicts can refuse this, and they are not the same event:

        * the same ``idempotency_key`` again -> the *same* arrival, replayed. Returns the existing
          visit (``created=False``). A double click, a retry, a lost response.
        * a *different* key for a patient who already has an active visit -> a *different* arrival
          for somebody already in the building. ``ActiveVisitAlreadyExists``, a 409. The pre-read
          below catches the ordinary case and ``uq_exchange_visits_one_active_per_patient`` catches
          the race the pre-read cannot -- two workers submitting at the same instant."""
        with self._sessions.begin() as session:
            return self.start_in_session(
                session,
                principal=principal,
                provider_organization_id=provider_organization_id,
                patient_id=patient_id,
                reason_for_visit=reason_for_visit,
                idempotency_key=idempotency_key,
            )

    def start_in_session(
        self,
        session: Session,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        reason_for_visit: str | None,
        idempotency_key: str,
    ) -> StartVisitResult:
        """``start()``'s whole body, as a step a *caller's* transaction can contain.

        DHEALTH-APPOINTMENTS-001 extracted this and changed nothing inside it. Checking a booked
        patient in has to create the visit and link the appointment to it **atomically** -- the one
        state the ticket names as unacceptable is a visit that exists while its booking still looks
        unarrived -- and `start()` owns its own `begin()`, so it could not be composed. The split is
        the same shape `selection.py`'s `assign_by_provider`/`_assign_by_provider_in_session` and
        `catalog.set_offering_active(..., session=...)` already use.

        **Appointment check-in calls this rather than reimplementing arrival.** Every rule stays
        exactly here and is enforced once: the provider-type check, the patient-belongs-to-this-
        clinic check, idempotent replay on a repeated key, the one-active-visit pre-read, the
        SAVEPOINT that turns a lost uniqueness race into a 409 instead of a 500, and the
        `visit_created` audit row. A second copy of any of those in the scheduling module would be
        a second place for them to drift.

        Public rather than `_`-prefixed, unlike the `selection.py` precedent, because the caller is
        a *different module* (`exchange/appointments.py`) rather than this class's own `start()`.
        """
        reason = reason_for_visit.strip() if reason_for_visit is not None else None
        if reason == "":
            raise ValueError("reason_for_visit must not be blank when it is provided")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")

        # Visits exist at providers. A lab has no outpatient consultation, and this check is
        # what stops a lab-typed organization from growing a clinic through this endpoint.
        self._organizations.require_type(provider_organization_id, OrganizationType.PROVIDER)

        patient = session.get(Patient, patient_id)
        if patient is None or patient.provider_organization_id != provider_organization_id:
            raise VisitPatientNotFound("patient not found in this provider organization")

        existing = session.scalar(
            select(Visit).where(
                Visit.provider_organization_id == provider_organization_id,
                Visit.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return self._replay(existing, patient, patient_id, reason, idempotency_key)

        # ADR-069 §18. An optimisation and a better error message, never the guarantee: two
        # simultaneous check-ins both pass this, which is why the unique index below exists.
        if self._active_visit_id(session, provider_organization_id, patient_id) is not None:
            raise ActiveVisitAlreadyExists(
                "this patient already has an active visit at this organization"
            )

        visit = Visit(
            idempotency_key=idempotency_key,
            provider_organization_id=provider_organization_id,
            patient_id=patient_id,
            status=VisitStatus.CHECKED_IN.value,
            reason_for_visit=reason,
        )
        # The insert runs inside a SAVEPOINT so that losing the uniqueness race unwinds to
        # *here* rather than poisoning the whole transaction. The pre-read above is an
        # optimisation, not the guarantee: two simultaneous check-ins both pass it, and without
        # this branch the loser's `IntegrityError` would escape `begin()` and reach the caller
        # as a 500. A double-clicked arrival on a slow connection is the ordinary case that
        # produces it, so 500 would be the ordinary outcome. Exactly the pattern
        # `order_intents._create_in_session` already established for the same race.
        try:
            with session.begin_nested():
                session.add(visit)
                session.flush()
        except IntegrityError:
            # `if visit in session` rather than a bare `expunge`, and the guard is load-bearing:
            # rolling the SAVEPOINT back restores the session to its state at `begin_nested()`,
            # which **already** evicts anything added inside it -- so the instance may be gone
            # and `expunge` then raises `InvalidRequestError`, masking the conflict with an
            # unrelated 500. The bare call survived DHEALTH-CLINIC-VISIT-001 only because that
            # ticket's one race was usually short-circuited by the pre-read; ADR-069's second
            # uniqueness rule reaches this line reliably, and the real-PostgreSQL qualification
            # (`test_pg_fd_1_*`) is what exposed it.
            if visit in session:
                session.expunge(visit)
            # **Two** uniqueness rules can land here since ADR-069, and they mean opposite
            # things, so both are re-checked before anything is re-raised. Checking only the
            # idempotency one -- as this branch did before ADR-069, when it was the only one --
            # would turn the ordinary two-receptionists race into a 500, because the loser's key
            # is its own and finds nothing.
            committed = session.scalar(
                select(Visit).where(
                    Visit.provider_organization_id == provider_organization_id,
                    Visit.idempotency_key == idempotency_key,
                )
            )
            if committed is not None:
                # The same arrival, replayed: the pre-read missed it because the other
                # transaction had not committed yet.
                return self._replay(committed, patient, patient_id, reason, idempotency_key)
            if self._active_visit_id(session, provider_organization_id, patient_id) is not None:
                # A different arrival for a patient somebody else just checked in. The index
                # refused it; the caller is told, not 500'd.
                raise ActiveVisitAlreadyExists(
                    "this patient already has an active visit at this organization"
                ) from None
            # Neither uniqueness rule explains this. Never swallowed: a composite-foreign-key or
            # CHECK violation must surface rather than be reported as a conflict that is not
            # one.
            raise

        self._record(
            session,
            event_type="visit_created",
            principal=principal,
            visit=visit,
            outcome="created",
        )
        return StartVisitResult(visit=_to_record(visit, patient), created=True)

    def _replay(
        self,
        existing: Visit,
        patient: Patient,
        patient_id: uuid.UUID,
        reason: str | None,
        idempotency_key: str,
    ) -> StartVisitResult:
        """The same canonical-request comparison ``LabOrderIntent`` makes, for the same reason its
        docstring gives: a reused key whose body differs is a client bug, and returning the *other*
        visit would silently attach this consultation to the wrong person.

        Shared by the pre-read path and the lost-the-race path so the two cannot drift apart about
        what counts as the same arrival. No audit row either way -- a replay commits nothing."""
        if existing.patient_id != patient_id or existing.reason_for_visit != reason:
            raise VisitIdempotencyKeyConflict(
                f"idempotency_key {idempotency_key!r} for provider organization "
                f"{existing.provider_organization_id} already started a visit for a different "
                f"patient or reason -- refusing to return mismatched data"
            )
        return StartVisitResult(visit=_to_record(existing, patient), created=False)

    # -- transitions ------------------------------------------------------------------------------

    def take(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        visit_id: uuid.UUID,
    ) -> VisitTransitionResult:
        """``CHECKED_IN`` -> ``IN_PROGRESS``, recording the acting clinician as the clinician of
        record. Re-taking a visit you already hold is an idempotent no-op; taking one another
        clinician holds is refused (see this module's docstring)."""
        with self._sessions.begin() as session:
            visit, patient = self._lock(session, provider_organization_id, visit_id)

            if visit.status == VisitStatus.IN_PROGRESS.value:
                if visit.clinician_identity_id == principal.identity_id:
                    return VisitTransitionResult(visit=_to_record(visit, patient), changed=False)
                raise VisitAlreadyTaken("this visit is already in progress under another clinician")

            self._require_transition(visit.status, VisitStatus.IN_PROGRESS.value)
            visit.status = VisitStatus.IN_PROGRESS.value
            visit.clinician_identity_id = principal.identity_id
            visit.started_at = utc_now()
            session.flush()
            self._record(
                session,
                event_type="visit_started",
                principal=principal,
                visit=visit,
                outcome="updated",
            )
            return VisitTransitionResult(visit=_to_record(visit, patient), changed=True)

    def complete(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        visit_id: uuid.UUID,
    ) -> VisitTransitionResult:
        """``IN_PROGRESS`` -> ``COMPLETED``. Terminal.

        Deliberately independent of every lab order raised during the visit: this method does not
        read lab order status, and no lab order status change reaches this method. See the module
        docstring."""
        with self._sessions.begin() as session:
            visit, patient = self._lock(session, provider_organization_id, visit_id)
            self._require_transition(visit.status, VisitStatus.COMPLETED.value)
            visit.status = VisitStatus.COMPLETED.value
            visit.completed_at = utc_now()
            session.flush()
            self._record(
                session,
                event_type="visit_completed",
                principal=principal,
                visit=visit,
                outcome="updated",
            )
            return VisitTransitionResult(visit=_to_record(visit, patient), changed=True)

    def cancel(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        visit_id: uuid.UUID,
    ) -> VisitTransitionResult:
        """``CHECKED_IN`` -> ``CANCELLED``, and from nowhere else.

        A visit already ``IN_PROGRESS`` is refused here on purpose -- §12: "Never from
        ``IN_PROGRESS`` -- that is a completed-with-note." The patient was seen; a status must not
        be able to say otherwise."""
        with self._sessions.begin() as session:
            visit, patient = self._lock(session, provider_organization_id, visit_id)
            self._require_transition(visit.status, VisitStatus.CANCELLED.value)
            visit.status = VisitStatus.CANCELLED.value
            visit.cancelled_at = utc_now()
            session.flush()
            self._record(
                session,
                event_type="visit_cancelled",
                principal=principal,
                visit=visit,
                outcome="updated",
            )
            return VisitTransitionResult(visit=_to_record(visit, patient), changed=True)

    # -- documentation ----------------------------------------------------------------------------

    def append_note(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        visit_id: uuid.UUID,
        body: str,
    ) -> VisitNoteRecord:
        """Append one note revision, authored by the acting clinician.

        Allowed only while the visit is ``IN_PROGRESS``. Amending a completed visit's note is
        deliberately not built here -- see the module docstring for why that gap is left named
        rather than filled with a weaker second amendment mechanism."""
        text = body.strip()
        if not text:
            raise ValueError("note body is required")

        with self._sessions.begin() as session:
            visit, _patient = self._lock(session, provider_organization_id, visit_id)
            if visit.status != VisitStatus.IN_PROGRESS.value:
                raise VisitTransitionRefused(
                    "a note may only be written while the visit is in progress"
                )

            # Computed under the row lock, with `uq_exchange_visit_notes_visit_revision` as the
            # database-level backstop if this ever runs without one.
            highest = session.scalar(
                select(func.max(VisitNote.revision_number)).where(VisitNote.visit_id == visit.id)
            )
            note = VisitNote(
                visit_id=visit.id,
                revision_number=(highest or 0) + 1,
                author_identity_id=principal.identity_id,
                body=text,
            )
            session.add(note)
            session.flush()
            # The visit, the author, the time. Never `text` -- see this module's docstring and
            # `VisitNote`'s.
            self._record(
                session,
                event_type="visit_note_appended",
                principal=principal,
                visit=visit,
                outcome="created",
            )
            return _to_note_record(note)

    def amend_reason(
        self,
        *,
        principal: AuthenticatedPrincipal,
        provider_organization_id: uuid.UUID,
        visit_id: uuid.UUID,
        reason_for_visit: str,
    ) -> VisitTransitionResult:
        """Correct the stated reason on a visit that is not terminal.

        Unlike a note, the reason is a single field rather than a clinical statement with a history,
        and correcting a typo in it at check-in is an ordinary front-of-house fix. It is refused
        once
        the visit is terminal, because at that point it is part of a closed record."""
        reason = reason_for_visit.strip()
        if not reason:
            raise ValueError("reason_for_visit is required")

        with self._sessions.begin() as session:
            visit, patient = self._lock(session, provider_organization_id, visit_id)
            if visit.status in TERMINAL_STATUSES:
                raise VisitTransitionRefused(
                    "the stated reason cannot be changed on a completed or cancelled visit"
                )
            if visit.reason_for_visit == reason:
                return VisitTransitionResult(visit=_to_record(visit, patient), changed=False)
            visit.reason_for_visit = reason
            session.flush()
            self._record(
                session,
                event_type="visit_reason_amended",
                principal=principal,
                visit=visit,
                outcome="updated",
            )
            return VisitTransitionResult(visit=_to_record(visit, patient), changed=True)

    # -- reads ------------------------------------------------------------------------------------

    def list_for_organization(
        self,
        provider_organization_id: uuid.UUID,
        *,
        statuses: frozenset[str] | None = None,
        limit: int = 50,
    ) -> list[VisitRecord]:
        """The worklist. Newest first, because a clinic's question is "who is here now".

        ``statuses`` is an explicit filter rather than a hardcoded "open visits" set: §13's worklist
        wants the two live states, but a clinician reviewing the day wants the completed ones, and
        baking one answer in would force a second endpoint for the other."""
        with self._sessions() as session:
            stmt = (
                select(Visit, Patient)
                .join(Patient, Patient.id == Visit.patient_id)
                .where(Visit.provider_organization_id == provider_organization_id)
                .order_by(Visit.created_at.desc())
                .limit(limit)
            )
            if statuses is not None:
                stmt = stmt.where(Visit.status.in_(sorted(statuses)))
            return [_to_record(visit, patient) for visit, patient in session.execute(stmt)]

    def list_waiting(
        self,
        provider_organization_id: uuid.UUID,
        *,
        limit: int = 100,
    ) -> list[WaitingArrivalRecord]:
        """The arrival/waiting projection. **Oldest arrival first**, and narrow by construction.

        DHEALTH-FRONT-DESK-001/ADR-069 §16. A separate method rather than
        ``list_for_organization(statuses={CHECKED_IN})`` for one reason that matters: that method
        returns ``VisitRecord``, which carries ``reason_for_visit``. A caller holding only
        ``visit.check_in`` must never receive it, and the safe way to guarantee that is a query that
        never selects the column -- not a caller remembering to strip it. See
        ``WaitingArrivalRecord`` for the full list of what is excluded and why.

        **Oldest first**, which is the opposite of ``list_for_organization``'s newest-first
        worklist, and deliberately so: a worklist answers "what changed", a waiting room answers
        "who has been here longest". ``docs/NATIVE-OPERATING-PRODUCT.md`` §8 specifies oldest-first
        for exactly this surface.

        **Ordering by the recorded arrival time is not a triage order** (§43). It is the order
        people
        arrived in, which is the order a waiting room already runs in; nothing here scores, weights,
        or reorders by anything about the patient, and no urgency, acuity or priority exists to sort
        by. ``visit_id`` breaks ties so two arrivals recorded in the same clock tick have a stable
        order rather than a database-dependent one.

        Both live states are returned so the desk can see that a patient it checked in has been
        taken
        (§23) without being able to see anything about the consultation."""
        with self._sessions() as session:
            rows = session.execute(
                select(
                    Visit.id,
                    Visit.patient_id,
                    Patient.display_name,
                    Patient.date_of_birth,
                    Visit.status,
                    Visit.created_at,
                )
                .join(Patient, Patient.id == Visit.patient_id)
                .where(
                    Visit.provider_organization_id == provider_organization_id,
                    Visit.status.in_((VisitStatus.CHECKED_IN.value, VisitStatus.IN_PROGRESS.value)),
                )
                .order_by(Visit.created_at.asc(), Visit.id.asc())
                .limit(limit)
            ).all()
            return [
                WaitingArrivalRecord(
                    visit_id=visit_id,
                    patient_id=patient_id,
                    patient_display_name=display_name,
                    patient_date_of_birth=date_of_birth,
                    status=status,
                    checked_in_at=created_at,
                )
                for visit_id, patient_id, display_name, date_of_birth, status, created_at in rows
            ]

    def list_for_patient(
        self,
        provider_organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[VisitRecord]:
        """One patient's visit history in this organization, newest first.

        Scoped by organization as well as patient, so this can never become a cross-provider
        longitudinal record by being called with somebody else's patient id (ADR-051)."""
        with self._sessions() as session:
            stmt = (
                select(Visit, Patient)
                .join(Patient, Patient.id == Visit.patient_id)
                .where(
                    Visit.provider_organization_id == provider_organization_id,
                    Visit.patient_id == patient_id,
                )
                .order_by(Visit.created_at.desc())
                .limit(limit)
            )
            return [_to_record(visit, patient) for visit, patient in session.execute(stmt)]

    def get_for_organization(
        self, provider_organization_id: uuid.UUID, visit_id: uuid.UUID
    ) -> VisitRecord | None:
        """Tenant-safe lookup. ``None`` both when no such visit exists and when it belongs to
        another organization, so the caller maps both to one 404."""
        with self._sessions() as session:
            row = session.execute(
                select(Visit, Patient)
                .join(Patient, Patient.id == Visit.patient_id)
                .where(
                    Visit.id == visit_id,
                    Visit.provider_organization_id == provider_organization_id,
                )
            ).first()
            if row is None:
                return None
            visit, patient = row
            return _to_record(visit, patient)

    def list_notes(
        self, provider_organization_id: uuid.UUID, visit_id: uuid.UUID
    ) -> list[VisitNoteRecord]:
        """Every revision, oldest first. The whole history is returned rather than only the current
        text: a clinician looking at a corrected note is entitled to see that it was corrected, the
        same principle ADR-045/048 applies to lab results.

        Organization-scoped through the visit, so a note can never be read by passing a visit id
        from another tenant."""
        with self._sessions() as session:
            notes = session.scalars(
                select(VisitNote)
                .join(Visit, Visit.id == VisitNote.visit_id)
                .where(
                    VisitNote.visit_id == visit_id,
                    Visit.provider_organization_id == provider_organization_id,
                )
                .order_by(VisitNote.revision_number.asc())
            ).all()
            return [_to_note_record(note) for note in notes]

    # -- internals --------------------------------------------------------------------------------

    def _lock(
        self, session: Session, provider_organization_id: uuid.UUID, visit_id: uuid.UUID
    ) -> tuple[Visit, Patient]:
        """Take the row lock and resolve the tenant boundary, in that order.

        ``with_for_update`` is a no-op on SQLite, so every concurrency property this gives is
        qualified against real PostgreSQL rather than asserted by the unit tests."""
        visit = session.scalar(select(Visit).where(Visit.id == visit_id).with_for_update())
        if visit is None or visit.provider_organization_id != provider_organization_id:
            raise VisitNotFound("visit not found")
        patient = session.get(Patient, visit.patient_id)
        if patient is None:  # pragma: no cover - RESTRICT foreign keys make this unreachable
            raise VisitNotFound("visit not found")
        return visit, patient

    def _active_visit_id(
        self, session: Session, provider_organization_id: uuid.UUID, patient_id: uuid.UUID
    ) -> uuid.UUID | None:
        """This patient's active visit at this organization, if they have one.

        ADR-069 §18's invariant expressed once, so the pre-read and the ``IntegrityError`` recovery
        path cannot drift about what "already waiting" means. The status set is exactly the one
        ``uq_exchange_visits_one_active_per_patient`` indexes on -- if the two disagreed, the
        service
        would raise a conflict the database permits, or 500 on one the database refuses."""
        return session.scalar(
            select(Visit.id).where(
                Visit.provider_organization_id == provider_organization_id,
                Visit.patient_id == patient_id,
                Visit.status.in_((VisitStatus.CHECKED_IN.value, VisitStatus.IN_PROGRESS.value)),
            )
        )

    def _require_transition(self, current: str, target: str) -> None:
        if (current, target) not in LEGAL_TRANSITIONS:
            raise VisitTransitionRefused(f"a visit cannot go from {current} to {target}")

    def _record(
        self,
        session: Session,
        *,
        event_type: SecurityEventType,
        principal: AuthenticatedPrincipal,
        visit: Visit,
        outcome: SecurityOutcome,
    ) -> None:
        self._audit.record(
            session,
            event_type=event_type,
            actor_identity_id=principal.identity_id,
            organization_id=visit.provider_organization_id,
            resource_type="visit",
            resource_id=visit.id,
            outcome=outcome,
        )
