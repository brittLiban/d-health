"""DH-IDENTITY-001/ADR-051: provider-scoped native Patient directory registration.

This is the *native* creation path -- ``POST /organizations/{provider_id}/patients``, the new
endpoint a DHealth Clinic "Créer un patient" form calls directly. It is deliberately a separate
repository from ``exchange/identity.py``'s ``PatientReferenceRepository`` (the *legacy* exact
source-identity binding path, which also now creates a ``Patient`` -- see its own docstring): this
path additionally owns idempotent replay/conflict semantics, a DOB/contact-pair-validated public
API surface, and its own immutable registration-request fingerprint record, none of which the
generic exact-identity bind endpoint needs or should carry.

**No fuzzy/demographic matching, here or anywhere else in this codebase.** Two ``Patient`` rows
with byte-identical ``display_name``/``date_of_birth``/contact are always allowed to coexist --
``create`` never searches for "a patient who looks like this" before inserting. The *only* thing
that ever makes two registration attempts resolve to the *same* ``Patient`` is an exact
``(provider_organization_id, idempotency_key)`` replay of the identical request -- a different
idempotency key with identical demographics is, by design, a second, different ``Patient``
(DH-IDENTITY-001 §16, "demographics are NOT an identity key", proven by
``test_identical_demographics_different_key_creates_second_patient`` in this ticket's own test
suite).

**Idempotency, not demographic matching, is the replay mechanism.** ``request_fingerprint`` is a
SHA-256 digest over a fixed, deterministically-canonicalized field tuple -- see ``_fingerprint``
below, the single place this is ever computed, both at creation and at replay-comparison time.
Never Python's own process-randomized ``hash()``, which cannot be compared across two separate
requests (or two separate processes). An exact replay (same key, same fingerprint) returns the
original ``Patient`` unchanged, `created=False`; a conflicting replay (same key, different
fingerprint) raises ``PatientRegistrationConflict`` (mapped to ``409`` at the HTTP layer) rather
than silently returning mismatched data -- the identical discipline
``LabOrderRepository``/``LabOrderIntentRepository`` already established for their own idempotency
keys.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    OrganizationType,
    Patient,
    PatientReference,
    PatientRegistrationRequest,
)
from daher_adapter.exchange.organizations import OrganizationRepository

# DH-IDENTITY-001 §13: the platform-owned source namespace for a natively-created Patient's own
# PatientReference -- deliberately "dhealth", never "dhealth-clinic". Native patient identity
# belongs to DHealth itself, not to today's React screen named "clinic" -- a future DHealth Lab
# frontend registering a patient through this same endpoint carries the identical namespace.
NATIVE_PATIENT_SOURCE_SYSTEM = "dhealth"
NATIVE_PATIENT_SOURCE_IDENTITY_SYSTEM = "urn:dhealth:patient-id"


class PatientNotFound(RuntimeError):
    """No Patient matches the given id (or it belongs to a different provider organization)."""


class PatientRegistrationConflict(RuntimeError):
    """The same (provider_organization_id, idempotency_key) was resubmitted with a different
    canonical request fingerprint -- refused rather than silently returning the original,
    mismatched Patient. Mapped to HTTP 409 by the caller."""


@dataclass(frozen=True, slots=True)
class PatientRecord:
    id: uuid.UUID
    provider_organization_id: uuid.UUID
    display_name: str | None
    date_of_birth: date | None
    contact_channel_type: str | None
    contact_value: str | None
    created_at: datetime
    primary_patient_reference_id: uuid.UUID
    """DH-IDENTITY-001 §23: trivial, not a ranking engine -- every Patient this ticket creates or
    migrates starts with exactly one PatientReference, so that one reference is always "the"
    primary one. No reference-linking API exists to make this field's meaning any less trivial."""


@dataclass(frozen=True, slots=True)
class CreatePatientResult:
    patient: PatientRecord
    created: bool
    """``False`` means an existing Patient for this idempotency key was returned instead of a new
    one being created -- the caller's retry/duplicate-click case, not an error."""


def _fingerprint(
    display_name: str,
    date_of_birth: date | None,
    contact_channel_type: str | None,
    contact_value: str | None,
) -> str:
    """The single canonical-payload recipe used both at creation and at replay-comparison time --
    a fixed field tuple, ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` over already-
    normalized Python types (``date_of_birth`` as an ISO string or ``None``, never a ``date``
    object -- ``json.dumps`` cannot serialize one, and a caller that fingerprinted a raw ``date``
    at creation but a parsed ``str`` at replay would produce two different digests for the exact
    same request), then SHA-256 hex. Never Python's process-randomized ``hash()``."""
    payload = {
        "display_name": display_name,
        "date_of_birth": date_of_birth.isoformat() if date_of_birth is not None else None,
        "contact_channel_type": contact_channel_type,
        "contact_value": contact_value,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _to_record(patient: Patient, primary_patient_reference_id: uuid.UUID) -> PatientRecord:
    return PatientRecord(
        id=patient.id,
        provider_organization_id=patient.provider_organization_id,
        display_name=patient.display_name,
        date_of_birth=patient.date_of_birth,
        contact_channel_type=patient.contact_channel_type,
        contact_value=patient.contact_value,
        created_at=patient.created_at,
        primary_patient_reference_id=primary_patient_reference_id,
    )


class PatientRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        organizations: OrganizationRepository,
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations

    def create(
        self,
        *,
        provider_organization_id: uuid.UUID,
        idempotency_key: str,
        display_name: str,
        date_of_birth: date | None,
        contact_channel_type: str | None,
        contact_value: str | None,
        created_by_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> CreatePatientResult:
        """``session`` lets the HTTP handler fold this creation and its authoritative
        ``patient_created`` security-audit row into one outer transaction -- when given, the
        three-row insert attempt runs inside a SAVEPOINT (``begin_nested``), the same pattern
        already established by ``LabOrderRepository.create``/``LabOrderIntentRepository.create``.
        When omitted, this method owns and commits its own transaction."""
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if not display_name.strip():
            raise ValueError("display_name is required for native patient creation")
        if (contact_channel_type is None) != (contact_value is None):
            raise ValueError(
                "contact_channel_type and contact_value must both be provided or both omitted"
            )
        # Fails closed if the target is not an ACTIVE PROVIDER organization -- a LAB organization
        # never gets a patient directory (DH-IDENTITY-001 §25/P11).
        self._organizations.require_type(provider_organization_id, OrganizationType.PROVIDER)

        fingerprint = _fingerprint(display_name, date_of_birth, contact_channel_type, contact_value)
        if session is not None:
            return self._create_in_session(
                session,
                provider_organization_id=provider_organization_id,
                idempotency_key=idempotency_key,
                display_name=display_name,
                date_of_birth=date_of_birth,
                contact_channel_type=contact_channel_type,
                contact_value=contact_value,
                created_by_identity_id=created_by_identity_id,
                fingerprint=fingerprint,
            )
        with self._sessions() as owned_session:
            result = self._create_in_session(
                owned_session,
                provider_organization_id=provider_organization_id,
                idempotency_key=idempotency_key,
                display_name=display_name,
                date_of_birth=date_of_birth,
                contact_channel_type=contact_channel_type,
                contact_value=contact_value,
                created_by_identity_id=created_by_identity_id,
                fingerprint=fingerprint,
            )
            owned_session.commit()
            return result

    def _create_in_session(
        self,
        session: Session,
        *,
        provider_organization_id: uuid.UUID,
        idempotency_key: str,
        display_name: str,
        date_of_birth: date | None,
        contact_channel_type: str | None,
        contact_value: str | None,
        created_by_identity_id: uuid.UUID,
        fingerprint: str,
    ) -> CreatePatientResult:
        try:
            with session.begin_nested():
                patient = Patient(
                    provider_organization_id=provider_organization_id,
                    display_name=display_name,
                    date_of_birth=date_of_birth,
                    contact_channel_type=contact_channel_type,
                    contact_value=contact_value,
                )
                session.add(patient)
                session.flush()

                reference = PatientReference(
                    source_organization_id=provider_organization_id,
                    patient_id=patient.id,
                    source_system=NATIVE_PATIENT_SOURCE_SYSTEM,
                    source_identity_system=NATIVE_PATIENT_SOURCE_IDENTITY_SYSTEM,
                    source_identity_value=str(patient.id),
                    display_name=display_name,
                    contact_channel_type=contact_channel_type,
                    contact_value=contact_value,
                )
                session.add(reference)
                session.flush()

                registration = PatientRegistrationRequest(
                    provider_organization_id=provider_organization_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    patient_id=patient.id,
                    created_by_identity_id=created_by_identity_id,
                )
                session.add(registration)
                session.flush()
            return CreatePatientResult(patient=_to_record(patient, reference.id), created=True)
        except IntegrityError:
            existing = session.scalar(
                select(PatientRegistrationRequest).where(
                    PatientRegistrationRequest.provider_organization_id == provider_organization_id,
                    PatientRegistrationRequest.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            if existing.request_fingerprint != fingerprint:
                raise PatientRegistrationConflict(
                    f"idempotency_key {idempotency_key!r} for provider organization "
                    f"{provider_organization_id} was already used for a patient registration "
                    f"with different content -- refusing to return mismatched data"
                ) from None
            existing_patient = session.get(Patient, existing.patient_id)
            existing_reference = session.scalar(
                select(PatientReference).where(
                    PatientReference.patient_id == existing.patient_id,
                    PatientReference.source_system == NATIVE_PATIENT_SOURCE_SYSTEM,
                    PatientReference.source_identity_system
                    == NATIVE_PATIENT_SOURCE_IDENTITY_SYSTEM,
                )
            )
            if existing_patient is None or existing_reference is None:
                raise
            return CreatePatientResult(
                patient=_to_record(existing_patient, existing_reference.id),
                created=False,
            )

    def get_for_organization(
        self, patient_id: uuid.UUID, provider_organization_id: uuid.UUID
    ) -> PatientRecord | None:
        """Tenant-safe lookup -- returns ``None`` both when no such Patient exists at all and when
        it exists but belongs to a *different* provider organization, so a caller maps either case
        to the same 404 (DH-IDENTITY-001 §26 anti-enumeration)."""
        with self._sessions() as session:
            patient = session.get(Patient, patient_id)
            if patient is None or patient.provider_organization_id != provider_organization_id:
                return None
            reference_id = session.scalar(
                select(PatientReference.id)
                .where(PatientReference.patient_id == patient.id)
                .order_by(PatientReference.created_at.asc())
                .limit(1)
            )
            if reference_id is None:
                return None
            return _to_record(patient, reference_id)

    def list_for_organization(
        self,
        provider_organization_id: uuid.UUID,
        *,
        query: str | None = None,
        limit: int = 50,
    ) -> list[PatientRecord]:
        """DH-IDENTITY-001 §20/§21: provider-scoped directory listing/search. Matches
        ``Patient.display_name``/``Patient.contact_value`` directly, or any linked
        ``PatientReference.source_identity_value`` via a scalar ``EXISTS`` subquery -- deliberately
        not a JOIN, so a Patient with more than one reference can never appear twice in the result
        (D8): there is nothing to de-duplicate because the query cannot produce a duplicate row in
        the first place. Deterministic ``created_at DESC, id ASC`` ordering, bounded limit,
        substring search only -- never fuzzy/demographic matching (retrieval, not identity
        resolution; see this module's own docstring)."""
        bounded_limit = max(1, min(limit, 100))
        # Correlated scalar subquery, not a JOIN -- picks this Patient's own earliest reference
        # (its only one, per §23) as one extra column on the same row, so the result can never
        # contain two rows for one Patient regardless of how many references it has (D8).
        primary_reference_id = (
            select(PatientReference.id)
            .where(PatientReference.patient_id == Patient.id)
            .order_by(PatientReference.created_at.asc())
            .limit(1)
            .correlate(Patient)
            .scalar_subquery()
        )
        stmt = select(Patient, primary_reference_id).where(
            Patient.provider_organization_id == provider_organization_id
        )

        clean_query = query.strip() if query is not None else None
        if clean_query:
            escaped = _escape_like(clean_query)
            pattern = f"%{escaped}%"
            reference_match = (
                select(PatientReference.id)
                .where(
                    PatientReference.patient_id == Patient.id,
                    PatientReference.source_identity_value.ilike(pattern, escape="\\"),
                )
                .exists()
            )
            stmt = stmt.where(
                or_(
                    Patient.display_name.ilike(pattern, escape="\\"),
                    Patient.contact_value.ilike(pattern, escape="\\"),
                    reference_match,
                )
            )

        stmt = stmt.order_by(Patient.created_at.desc(), Patient.id.asc()).limit(bounded_limit)

        with self._sessions() as session:
            rows = session.execute(stmt).all()
            return [
                _to_record(patient, reference_id)
                for patient, reference_id in rows
                if reference_id is not None
            ]

    def get_registration(
        self, provider_organization_id: uuid.UUID, idempotency_key: str
    ) -> CreatePatientResult | None:
        """DH-IDENTITY-001 §19: server-side recovery for an ambiguous create response -- resolves
        an opaque ``(provider_organization_id, idempotency_key)`` to the authoritative Patient the
        registration actually committed, or ``None`` if it never did (the caller must not infer a
        create happened; it must retry the same idempotent POST). Tenant-scoped like every other
        lookup here -- a different provider's registration under the same key string is invisible,
        never disclosed."""
        with self._sessions() as session:
            registration = session.scalar(
                select(PatientRegistrationRequest).where(
                    PatientRegistrationRequest.provider_organization_id == provider_organization_id,
                    PatientRegistrationRequest.idempotency_key == idempotency_key,
                )
            )
            if registration is None:
                return None
            patient = session.get(Patient, registration.patient_id)
            reference = session.scalar(
                select(PatientReference).where(
                    PatientReference.patient_id == registration.patient_id,
                    PatientReference.source_system == NATIVE_PATIENT_SOURCE_SYSTEM,
                    PatientReference.source_identity_system
                    == NATIVE_PATIENT_SOURCE_IDENTITY_SYSTEM,
                )
            )
            if patient is None or reference is None:
                return None
            return CreatePatientResult(patient=_to_record(patient, reference.id), created=False)


def _escape_like(val: str) -> str:
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
