"""DH-EX-001: patient and practitioner references.

Both repositories share one rule: source identity is looked up and bound **exactly**, never
fuzzy-matched. ``get_or_create`` is insert-first-wins, the same pattern
``daher_adapter.ledger.LedgerRepository.claim`` already proves for message identity -- attempt the
insert, and on the unique-constraint violation, look up and return the row that already owns that
exact ``(source_organization_id, source_system, source_identity_system, source_identity_value)``
tuple instead of creating a second, ambiguous one. The uniqueness is scoped to the asserting
organization deliberately (DH-EX-001R finding) -- two different organizations reusing the same
identity namespace/value (e.g. two Provider Portal accounts both minting sequential local ids
starting at 1) are two different patients, not one, and must never be silently merged just because
the raw identity string happens to collide. There is no code path anywhere in this module that
compares two different identity values and decides they probably refer to the same person -- that
is the fuzzy/demographic matching DH-PIVOT-001 puts out of MVP scope, permanently, not just for
this ticket.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import PatientReference, PractitionerReference


class ReferenceNotFound(RuntimeError):
    """No reference matches the given id."""


@dataclass(frozen=True, slots=True)
class PatientReferenceRecord:
    id: uuid.UUID
    source_organization_id: uuid.UUID
    source_system: str
    source_identity_system: str
    source_identity_value: str
    display_name: str | None
    contact_channel_type: str | None = None
    contact_value: str | None = None


@dataclass(frozen=True, slots=True)
class PractitionerReferenceRecord:
    id: uuid.UUID
    source_organization_id: uuid.UUID
    source_system: str
    source_identity_system: str
    source_identity_value: str
    display_name: str | None


class PatientReferenceRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def get_or_create(
        self,
        *,
        source_organization_id: uuid.UUID,
        source_system: str,
        source_identity_system: str,
        source_identity_value: str,
        display_name: str | None = None,
        contact_channel_type: str | None = None,
        contact_value: str | None = None,
    ) -> PatientReferenceRecord:
        _require_identity(source_system, source_identity_system, source_identity_value)
        reference = PatientReference(
            source_organization_id=source_organization_id,
            source_system=source_system,
            source_identity_system=source_identity_system,
            source_identity_value=source_identity_value,
            display_name=display_name,
            contact_channel_type=contact_channel_type,
            contact_value=contact_value,
        )
        with self._sessions() as session:
            try:
                session.add(reference)
                session.commit()
                return _to_patient_record(reference)
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(PatientReference).where(
                        PatientReference.source_organization_id == source_organization_id,
                        PatientReference.source_system == source_system,
                        PatientReference.source_identity_system == source_identity_system,
                        PatientReference.source_identity_value == source_identity_value,
                    )
                )
                if existing is None:
                    raise
                return _to_patient_record(existing)

    def get_or_create_with_outcome(
        self,
        *,
        source_organization_id: uuid.UUID,
        source_system: str,
        source_identity_system: str,
        source_identity_value: str,
        display_name: str | None = None,
        contact_channel_type: str | None = None,
        contact_value: str | None = None,
        session: Session | None = None,
    ) -> tuple[PatientReferenceRecord, bool]:
        """DH-EX-009: same exact-identity, insert-first-wins semantics as ``get_or_create`` above,
        but exposes the created/existing outcome the provider-scoped binding API needs for its
        201-vs-200 response and audit `outcome`, and accepts an optional caller-owned ``session``
        so the bind and its authoritative audit event commit atomically -- the same optional-
        session/SAVEPOINT pattern already established by
        ``PractitionerReferenceRepository.get_or_create``. Deliberately a separate method rather
        than changing ``get_or_create``'s return type: many existing callers already treat that
        method's return value as a bare ``PatientReferenceRecord``, and this ticket's own
        instruction is to preserve that.

        A replay with the exact same identity tuple but different ``display_name``/contact fields
        returns the existing row **unchanged** -- this is identity binding, not a patient-
        demographic update API; the new snapshot values are silently discarded, never applied,
        exactly like ``get_or_create``'s existing behavior for the same case."""
        _require_identity(source_system, source_identity_system, source_identity_value)
        if session is not None:
            return self._get_or_create_with_outcome_in_session(
                session,
                source_organization_id=source_organization_id,
                source_system=source_system,
                source_identity_system=source_identity_system,
                source_identity_value=source_identity_value,
                display_name=display_name,
                contact_channel_type=contact_channel_type,
                contact_value=contact_value,
            )
        with self._sessions() as owned_session:
            record, created = self._get_or_create_with_outcome_in_session(
                owned_session,
                source_organization_id=source_organization_id,
                source_system=source_system,
                source_identity_system=source_identity_system,
                source_identity_value=source_identity_value,
                display_name=display_name,
                contact_channel_type=contact_channel_type,
                contact_value=contact_value,
            )
            owned_session.commit()
            return record, created

    def _get_or_create_with_outcome_in_session(
        self,
        session: Session,
        *,
        source_organization_id: uuid.UUID,
        source_system: str,
        source_identity_system: str,
        source_identity_value: str,
        display_name: str | None,
        contact_channel_type: str | None,
        contact_value: str | None,
    ) -> tuple[PatientReferenceRecord, bool]:
        reference = PatientReference(
            source_organization_id=source_organization_id,
            source_system=source_system,
            source_identity_system=source_identity_system,
            source_identity_value=source_identity_value,
            display_name=display_name,
            contact_channel_type=contact_channel_type,
            contact_value=contact_value,
        )
        try:
            with session.begin_nested():
                session.add(reference)
                session.flush()
            return _to_patient_record(reference), True
        except IntegrityError:
            existing = session.scalar(
                select(PatientReference).where(
                    PatientReference.source_organization_id == source_organization_id,
                    PatientReference.source_system == source_system,
                    PatientReference.source_identity_system == source_identity_system,
                    PatientReference.source_identity_value == source_identity_value,
                )
            )
            if existing is None:
                raise
            return _to_patient_record(existing), False

    def get(self, reference_id: uuid.UUID) -> PatientReferenceRecord:
        with self._sessions() as session:
            reference = session.get(PatientReference, reference_id)
            if reference is None:
                raise ReferenceNotFound(str(reference_id))
            return _to_patient_record(reference)

    def get_for_organization(
        self, reference_id: uuid.UUID, organization_id: uuid.UUID
    ) -> PatientReferenceRecord | None:
        """DH-EX-004: tenant-safe lookup -- returns ``None`` both when the reference does not
        exist at all and when it exists but belongs to a *different* organization, so a caller
        can map either case to the same 404 without ever disclosing that a patient reference
        exists elsewhere (the same anti-enumeration discipline `authorization.py` already applies
        to organizations)."""
        with self._sessions() as session:
            reference = session.get(PatientReference, reference_id)
            if reference is None or reference.source_organization_id != organization_id:
                return None
            return _to_patient_record(reference)

    def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        query: str | None = None,
        limit: int = 50,
    ) -> list[PatientReferenceRecord]:
        """DH-CLINIC-002: provider-scoped patient lookup.
        Strictly filters by ``source_organization_id == organization_id`` -- never searches across
        providers. If ``query`` is non-empty, performs deterministic substring search over
        approved snapshot fields (``display_name``, ``contact_value``, ``source_identity_value``).
        Orders deterministically by ``created_at.desc(), id.asc()`` with bounded limit."""
        bounded_limit = max(1, min(limit, 100))
        stmt = select(PatientReference).where(
            PatientReference.source_organization_id == organization_id
        )

        clean_query = query.strip() if query is not None else None
        if clean_query:
            escaped = _escape_like(clean_query)
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    PatientReference.display_name.ilike(pattern, escape="\\"),
                    PatientReference.contact_value.ilike(pattern, escape="\\"),
                    PatientReference.source_identity_value.ilike(pattern, escape="\\"),
                )
            )

        stmt = stmt.order_by(PatientReference.created_at.desc(), PatientReference.id.asc()).limit(
            bounded_limit
        )

        with self._sessions() as session:
            rows = session.scalars(stmt).all()
            return [_to_patient_record(row) for row in rows]


class PractitionerReferenceRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def get_or_create(
        self,
        *,
        source_organization_id: uuid.UUID,
        source_system: str,
        source_identity_system: str,
        source_identity_value: str,
        display_name: str | None = None,
        session: Session | None = None,
    ) -> PractitionerReferenceRecord:
        """DH-EX-004: ``session`` lets a caller (order creation) fold this get-or-create into its
        own outer transaction -- when given, the insert-or-fetch attempt runs inside a SAVEPOINT
        (``begin_nested``) so a duplicate-key race only unwinds to that savepoint, never poisoning
        the caller's outer transaction, matching the exact pattern already established for
        offerings/prices/memberships. When omitted, this method still owns and commits its own
        transaction exactly as before, preserving every existing caller."""
        _require_identity(source_system, source_identity_system, source_identity_value)
        if session is not None:
            return self._get_or_create_in_session(
                session,
                source_organization_id=source_organization_id,
                source_system=source_system,
                source_identity_system=source_identity_system,
                source_identity_value=source_identity_value,
                display_name=display_name,
            )
        with self._sessions() as owned_session:
            record = self._get_or_create_in_session(
                owned_session,
                source_organization_id=source_organization_id,
                source_system=source_system,
                source_identity_system=source_identity_system,
                source_identity_value=source_identity_value,
                display_name=display_name,
            )
            owned_session.commit()
            return record

    def _get_or_create_in_session(
        self,
        session: Session,
        *,
        source_organization_id: uuid.UUID,
        source_system: str,
        source_identity_system: str,
        source_identity_value: str,
        display_name: str | None,
    ) -> PractitionerReferenceRecord:
        reference = PractitionerReference(
            source_organization_id=source_organization_id,
            source_system=source_system,
            source_identity_system=source_identity_system,
            source_identity_value=source_identity_value,
            display_name=display_name,
        )
        try:
            with session.begin_nested():
                session.add(reference)
                session.flush()
            return _to_practitioner_record(reference)
        except IntegrityError:
            existing = session.scalar(
                select(PractitionerReference).where(
                    PractitionerReference.source_organization_id == source_organization_id,
                    PractitionerReference.source_system == source_system,
                    PractitionerReference.source_identity_system == source_identity_system,
                    PractitionerReference.source_identity_value == source_identity_value,
                )
            )
            if existing is None:
                raise
            return _to_practitioner_record(existing)

    def get(self, reference_id: uuid.UUID) -> PractitionerReferenceRecord:
        with self._sessions() as session:
            reference = session.get(PractitionerReference, reference_id)
            if reference is None:
                raise ReferenceNotFound(str(reference_id))
            return _to_practitioner_record(reference)

    def get_for_organization(
        self, reference_id: uuid.UUID, organization_id: uuid.UUID
    ) -> PractitionerReferenceRecord | None:
        """DH-EX-004: same tenant-safe shape as `PatientReferenceRepository.get_for_organization`
        -- a practitioner reference belonging to a different organization must never be usable,
        even by id."""
        with self._sessions() as session:
            reference = session.get(PractitionerReference, reference_id)
            if reference is None or reference.source_organization_id != organization_id:
                return None
            return _to_practitioner_record(reference)


def _require_identity(
    source_system: str, source_identity_system: str, source_identity_value: str
) -> None:
    for name, value in (
        ("source_system", source_system),
        ("source_identity_system", source_identity_system),
        ("source_identity_value", source_identity_value),
    ):
        if not value.strip():
            raise ValueError(f"{name} is required -- exact source identity only, never inferred")


def _escape_like(val: str) -> str:
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_patient_record(reference: PatientReference) -> PatientReferenceRecord:
    return PatientReferenceRecord(
        id=reference.id,
        source_organization_id=reference.source_organization_id,
        source_system=reference.source_system,
        source_identity_system=reference.source_identity_system,
        source_identity_value=reference.source_identity_value,
        display_name=reference.display_name,
        contact_channel_type=reference.contact_channel_type,
        contact_value=reference.contact_value,
    )


def _to_practitioner_record(reference: PractitionerReference) -> PractitionerReferenceRecord:
    return PractitionerReferenceRecord(
        id=reference.id,
        source_organization_id=reference.source_organization_id,
        source_system=reference.source_system,
        source_identity_system=reference.source_identity_system,
        source_identity_value=reference.source_identity_value,
        display_name=reference.display_name,
    )
