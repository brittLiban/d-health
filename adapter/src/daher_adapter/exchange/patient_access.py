"""DH-EX-005: opaque, order-scoped patient access grants.

A patient never gets an account, an ``ExchangeIdentity``, or an OIDC login for this MVP.
Possession of a high-entropy (256-bit) random bearer secret is the entire authorization
mechanism, scoped to exactly one ``LabOrder``. Only a SHA-256 digest of that secret is ever
persisted (``PatientAccessGrant.token_hash``) -- the raw value exists only in the single issuance
HTTP response and the patient's own possession of it, never in the database, a log, an exception,
or an authoritative audit row. A fast cryptographic hash, not a slow password KDF (bcrypt/PBKDF2),
is the correct choice here specifically *because* the input is already uniformly-random,
256-bit, machine-generated -- there is no low-entropy human password to protect against
dictionary/brute-force guessing; the hash exists only so a database read (or a backup, or a
careless log) cannot recover the bearer secret itself.

**Why this repository never creates a fake ``ExchangeIdentity`` for the patient.** A patient using
an opaque grant is not an authenticated Exchange identity in any sense the rest of this codebase
uses that word -- ``ExchangeSecurityAuditEvent.actor_identity_id`` is a real FK to
``exchange_identities``, and populating it with a fabricated row (or with the *clinician's*
identity, as if the clinician had made the selection) would both be a lie recorded permanently in
an authoritative security table. Instead, ``LabSelection`` itself carries its own authoritative
provenance for a patient-made selection: ``selected_via_patient_access_grant_id`` (see
``exchange/selection.py``) is a real FK to the exact grant that authorized it. That immutable row
-- which order, which lab, when, which scoped grant -- *is* the record; there is no missing
capability that a generic patient-action audit table would add, so this ticket does not invent one.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    LabOrder,
    LabOrderStatus,
    LabSelection,
    PatientAccessGrant,
    PatientAccessMode,
)
from daher_adapter.models import utc_now


def _aware(value: datetime) -> datetime:
    """PostgreSQL's `timestamptz` always returns a timezone-aware `datetime`, but SQLite (used
    only by this repo's fast test suite, never production) has no native timestamptz type and
    hands back a naive one on a fresh read -- comparing that directly against `utc_now()` raises
    `TypeError: can't compare offset-naive and offset-aware datetimes`. Every value this module
    writes is already UTC, so re-attaching the UTC tzinfo when it's missing is lossless and a
    no-op against the real Postgres driver."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# 256 bits of entropy -- `secrets.token_urlsafe(n)` returns roughly `4*ceil(n/3)` URL-safe
# base64 characters encoding `n` random bytes; 32 bytes is the conventional "256-bit secret" size
# and matches this ticket's own explicit minimum.
_TOKEN_ENTROPY_BYTES = 32
_MAX_TOKEN_GENERATION_ATTEMPTS = 5


class OrderNotFound(RuntimeError):
    """No LabOrder matches the given id."""


class OrderNotIssuable(RuntimeError):
    """DH-EX-010A: no truthful ``PatientAccessMode`` exists for this order's current state. The
    order has no canonical ``LabSelection`` (so ``ORDER_READ`` would have nothing to authorize
    reading a settled destination for) *and* is not ``AWAITING_LAB_SELECTION`` either (so
    ``LAB_SELECTION`` would be false -- there is no lab left to choose). This is a fail-closed
    branch, not a guess: an order in this shape (e.g. ``CANCELLED``/``REJECTED`` reached directly
    from ``AWAITING_LAB_SELECTION`` with no lab ever chosen) has no access grant that could
    describe it honestly, so none is issued."""


class ActiveGrantAlreadyExists(RuntimeError):
    """An unrevoked, unexpired PatientAccessGrant already exists for this order -- issuance never
    silently rotates a link that may already have been delivered to the patient. The caller must
    explicitly revoke the existing grant first."""


@dataclass(frozen=True, slots=True)
class IssuedGrant:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    raw_token: str
    """Present only on the object returned directly from `issue()` -- never persisted, never
    logged, never present on any other record type in this module."""
    issued_at: datetime
    expires_at: datetime
    access_mode: PatientAccessMode


@dataclass(frozen=True, slots=True)
class GrantRecord:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    access_mode: PatientAccessMode


class PatientAccessGrantRepository:
    def __init__(self, session_factory: sessionmaker[Session], *, ttl_hours: int) -> None:
        self._sessions = session_factory
        self._ttl_hours = ttl_hours

    def issue(self, *, lab_order_id: uuid.UUID, session: Session | None = None) -> IssuedGrant:
        """Locks the order row first, which fully serializes concurrent issuance attempts for the
        *same* order (a second caller's own `SELECT ... FOR UPDATE` blocks until the first
        transaction commits or rolls back, then re-reads the now-current grant state) -- no
        separate lock on the grants table is needed for that race. An expired-but-not-yet-revoked
        prior grant is retired (`revoked_at` set) in the same transaction as the replacement's
        insert, never left to block issuance forever. A genuine `token_hash` collision (the only
        remaining `IntegrityError` source once the order-level race is excluded by the lock above)
        is retried with a fresh random token, bounded, never an infinite loop."""
        if session is not None:
            return self._issue_in_session(session, lab_order_id=lab_order_id)
        with self._sessions() as owned_session:
            result = self._issue_in_session(owned_session, lab_order_id=lab_order_id)
            owned_session.commit()
            return result

    def _issue_in_session(self, session: Session, *, lab_order_id: uuid.UUID) -> IssuedGrant:
        order = session.scalar(
            select(LabOrder).where(LabOrder.id == lab_order_id).with_for_update()
        )
        if order is None:
            raise OrderNotFound(str(lab_order_id))

        # DH-EX-010A: decided once, here, inside the same lock that serializes against a
        # concurrent `assign_by_provider`/`select()` for this order -- whichever of "issue a
        # grant" or "create the canonical LabSelection" commits first is what the other observes,
        # so this decision is always made against a fully-resolved, race-free view of whether a
        # destination lab has been chosen. The *existence* of a canonical LabSelection -- not
        # `order.status == SELECTED` specifically -- is the signal: a lab may have already been
        # accepted, started, or even completed by the time a clinician gets around to issuing the
        # patient's link (DH-EX-010A's "fast-lab race" requirement), and every one of those later
        # statuses still carries the same LabSelection row a lab-choice action would need to exist.
        existing_selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == lab_order_id)
        )
        if existing_selection is not None:
            access_mode = PatientAccessMode.ORDER_READ
        elif order.status == LabOrderStatus.AWAITING_LAB_SELECTION.value:
            access_mode = PatientAccessMode.LAB_SELECTION
        else:
            # No lab has ever been chosen, and none can be chosen from here (e.g. CANCELLED or
            # REJECTED reached directly from AWAITING_LAB_SELECTION with no selection ever made) --
            # fail closed rather than guess at a capability this order's state cannot support.
            raise OrderNotIssuable(
                f"LabOrder {lab_order_id} is in status {order.status!r} with no lab ever "
                f"selected -- no truthful patient access mode exists for this order"
            )

        now = utc_now()
        existing = session.scalar(
            select(PatientAccessGrant).where(
                PatientAccessGrant.lab_order_id == lab_order_id,
                PatientAccessGrant.revoked_at.is_(None),
            )
        )
        if existing is not None:
            if _aware(existing.expires_at) > now:
                raise ActiveGrantAlreadyExists(
                    f"an active, unexpired patient access grant already exists for LabOrder "
                    f"{lab_order_id} -- revoke it explicitly before issuing a replacement"
                )
            # Expired but never revoked -- retire it before creating the replacement, in the
            # same transaction, so the partial unique index never sees two "live" rows.
            existing.revoked_at = now

        last_error: IntegrityError | None = None
        for _attempt in range(_MAX_TOKEN_GENERATION_ATTEMPTS):
            raw_token = secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
            token_hash = _hash_token(raw_token)
            expires_at = now + timedelta(hours=self._ttl_hours)
            grant = PatientAccessGrant(
                lab_order_id=lab_order_id,
                token_hash=token_hash,
                issued_at=now,
                expires_at=expires_at,
                access_mode=access_mode.value,
            )
            try:
                with session.begin_nested():
                    session.add(grant)
                    session.flush()
                return IssuedGrant(
                    id=grant.id,
                    lab_order_id=lab_order_id,
                    raw_token=raw_token,
                    issued_at=now,
                    expires_at=expires_at,
                    access_mode=access_mode,
                )
            except IntegrityError as error:
                # Re-query rather than parse the driver's error message (matching
                # `exchange/catalog.py`'s established `create_entry`/`create_offering` pattern):
                # only a genuine `token_hash` collision is worth a blind retry with a fresh random
                # token. Anything else -- e.g. the partial "one live grant per order" unique index
                # firing because the FOR UPDATE lock above somehow didn't exclude a concurrent
                # issuer, or an unrelated schema violation -- must surface immediately rather than
                # being retried 5x and then misreported as "failed to generate a unique token".
                collision = session.scalar(
                    select(PatientAccessGrant).where(PatientAccessGrant.token_hash == token_hash)
                )
                if collision is None:
                    raise
                last_error = error
                continue
        raise RuntimeError(
            "failed to generate a unique patient access token after "
            f"{_MAX_TOKEN_GENERATION_ATTEMPTS} attempts"
        ) from last_error

    def revoke(
        self, *, lab_order_id: uuid.UUID, session: Session | None = None
    ) -> tuple[str, uuid.UUID | None]:
        """Returns ``(outcome, grant_id)`` where outcome is ``"revoked"`` or
        ``"no_active_grant"`` -- idempotent: revoking an order with no active grant is a safe
        no-op, never an error, and the caller uses the outcome to decide whether a
        ``patient_access_revoked`` audit row is warranted (never on a no-op)."""
        if session is not None:
            return self._revoke_in_session(session, lab_order_id=lab_order_id)
        with self._sessions.begin() as owned_session:
            return self._revoke_in_session(owned_session, lab_order_id=lab_order_id)

    def _revoke_in_session(
        self, session: Session, *, lab_order_id: uuid.UUID
    ) -> tuple[str, uuid.UUID | None]:
        # Locks the *order* row first, exactly like `_issue_in_session` -- without this, a
        # concurrent issue()/revoke() pair for the same order lock different rows (the order vs.
        # the grant) and are not serialized against each other: a revoke that starts just before a
        # concurrent issue() retires an expired grant and inserts its replacement can see "no
        # active grant" (the old one, not yet committed-retired) and return a false
        # `no_active_grant` success while a brand-new live token silently exists. Locking the order
        # row here makes the two paths block on each other exactly like two concurrent issue()
        # calls already do.
        order = session.scalar(
            select(LabOrder).where(LabOrder.id == lab_order_id).with_for_update()
        )
        if order is None:
            raise OrderNotFound(str(lab_order_id))
        grant = session.scalar(
            select(PatientAccessGrant).where(
                PatientAccessGrant.lab_order_id == lab_order_id,
                PatientAccessGrant.revoked_at.is_(None),
            )
        )
        if grant is None:
            return "no_active_grant", None
        grant.revoked_at = utc_now()
        session.flush()
        return "revoked", grant.id

    def get_active_for_order(self, lab_order_id: uuid.UUID) -> GrantRecord | None:
        """DH-PATIENT-001: the metadata read a clinician needs to answer "is a patient access link
        already outstanding for this order?" -- and nothing more. Returns ``None`` for an order
        with no grant at all, for a revoked grant, and for an expired-but-not-yet-revoked one:
        expiry is evaluated as a *read-time* predicate, never by mutating ``revoked_at`` as a side
        effect of being asked (the caller is a GET; retiring a grant is issuance's own job, inside
        its own lock -- see ``_issue_in_session``).

        One order, one indexed lookup, no N+1 and no lock: ``PatientAccessGrant``'s partial unique
        index over ``(lab_order_id) WHERE revoked_at IS NULL`` already guarantees at most one
        unrevoked row per order, so the single-row read below cannot silently drop a second live
        grant. The returned ``GrantRecord`` deliberately carries no ``token_hash`` -- that field
        exists on the ORM row and on no record type this module hands out."""
        with self._sessions() as session:
            grant = session.scalar(
                select(PatientAccessGrant).where(
                    PatientAccessGrant.lab_order_id == lab_order_id,
                    PatientAccessGrant.revoked_at.is_(None),
                )
            )
            if grant is None:
                return None
            if _aware(grant.expires_at) <= utc_now():
                return None
            return _to_record(grant)

    def validate_token(self, raw_token: str) -> GrantRecord | None:
        """Returns the grant if ``raw_token`` resolves to a currently valid (unexpired,
        unrevoked) grant, else ``None`` -- every failure mode (unknown token, expired, revoked,
        malformed) collapses to this same ``None``, so the API layer maps all of them to the
        identical generic 401, never distinguishing *why* to an unauthenticated caller."""
        token_hash = _hash_token(raw_token)
        with self._sessions() as session:
            grant = session.scalar(
                select(PatientAccessGrant).where(PatientAccessGrant.token_hash == token_hash)
            )
            if grant is None:
                return None
            if grant.revoked_at is not None:
                return None
            if _aware(grant.expires_at) <= utc_now():
                return None
            return _to_record(grant)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _to_record(grant: PatientAccessGrant) -> GrantRecord:
    """DH-PATIENT-001: every timestamp leaves this module timezone-aware, normalized through
    ``_aware`` exactly like the comparisons above already are. Without this, a record read back on
    SQLite carries naive datetimes, and ``PatientAccessMetadataResponse.expires_at`` would then
    serialize as ``2026-09-27T07:46:44`` with no offset -- which a browser parses as *local* time,
    silently shifting a clinician-visible expiry by the timezone offset (the issuance response,
    whose value never round-trips through the database, correctly emits ``...Z``; the two must
    agree).
    Lossless: every value this module writes is already UTC."""
    return GrantRecord(
        id=grant.id,
        lab_order_id=grant.lab_order_id,
        issued_at=_aware(grant.issued_at),
        expires_at=_aware(grant.expires_at),
        revoked_at=None if grant.revoked_at is None else _aware(grant.revoked_at),
        access_mode=PatientAccessMode(grant.access_mode),
    )
