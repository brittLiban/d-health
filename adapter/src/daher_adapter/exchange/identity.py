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

from sqlalchemy import select
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

    def get(self, reference_id: uuid.UUID) -> PatientReferenceRecord:
        with self._sessions() as session:
            reference = session.get(PatientReference, reference_id)
            if reference is None:
                raise ReferenceNotFound(str(reference_id))
            return _to_patient_record(reference)


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
    ) -> PractitionerReferenceRecord:
        _require_identity(source_system, source_identity_system, source_identity_value)
        reference = PractitionerReference(
            source_organization_id=source_organization_id,
            source_system=source_system,
            source_identity_system=source_identity_system,
            source_identity_value=source_identity_value,
            display_name=display_name,
        )
        with self._sessions() as session:
            try:
                session.add(reference)
                session.commit()
                return _to_practitioner_record(reference)
            except IntegrityError:
                session.rollback()
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


def _to_patient_record(reference: PatientReference) -> PatientReferenceRecord:
    return PatientReferenceRecord(
        id=reference.id,
        source_organization_id=reference.source_organization_id,
        source_system=reference.source_system,
        source_identity_system=reference.source_identity_system,
        source_identity_value=reference.source_identity_value,
        display_name=reference.display_name,
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
