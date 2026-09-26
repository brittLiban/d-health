"""DH-EX-001: Organization creation and lookup. Deliberately no onboarding/auth/user model yet."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import Organization, OrganizationStatus, OrganizationType


class InvalidOrganizationType(ValueError):
    """An organization type outside {PROVIDER, LAB} was requested."""


class OrganizationNotFound(RuntimeError):
    """No organization matches the given id."""


@dataclass(frozen=True, slots=True)
class OrganizationRecord:
    id: uuid.UUID
    org_type: str
    name: str
    status: str


class OrganizationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def create(
        self, *, org_type: str, name: str, session: Session | None = None
    ) -> OrganizationRecord:
        # `org_type` is a plain `str`, not `OrganizationType`, deliberately: this is the
        # boundary an eventual org-onboarding API would expose to a caller, and the negative
        # control ("invalid organization type refused") needs a value Python's own enum
        # constructor would not already reject before this method runs.
        try:
            valid_type = OrganizationType(org_type)
        except ValueError as error:
            raise InvalidOrganizationType(f"unsupported organization type {org_type!r}") from error
        if not name.strip():
            raise ValueError("name is required")
        organization = Organization(
            org_type=valid_type.value, name=name, status=OrganizationStatus.ACTIVE.value
        )
        # No uniqueness constraint on `name` -- unchanged from DH-EX-001's original scope
        # ("Do NOT model full onboarding/auth yet"), not a decision this ticket revisits.
        # Duplicate organization names are therefore permitted by design, not an oversight.
        if session is not None:
            # Caller owns the transaction (DH-EX-002R: organization creation + its authoritative
            # audit row must commit or roll back together) -- flush only, never commit here.
            session.add(organization)
            session.flush()
            return _to_record(organization)
        with self._sessions() as owned_session:
            owned_session.add(organization)
            owned_session.commit()
            return _to_record(organization)

    def get(self, organization_id: uuid.UUID) -> OrganizationRecord:
        with self._sessions() as session:
            organization = session.get(Organization, organization_id)
            if organization is None:
                raise OrganizationNotFound(str(organization_id))
            return _to_record(organization)

    def lock_in_session(self, session: Session, organization_id: uuid.UUID) -> OrganizationRecord:
        """DHEALTH-NATIVE-ACCESS-001: take a row-level write lock on the organization, inside the
        caller's transaction, and return it.

        The organization row is used here as the **serialization point for that organization's whole
        staff access lifecycle** -- deactivate, reactivate, add role, remove role. It is not
        mutated; it is locked because every one of those four operations has to evaluate the
        last-active-administrator invariant against a membership *set*, and a per-row lock on the
        membership each operation happens to touch cannot serialize two operations touching two
        different memberships. Two administrators removing each other's ORG_ADMIN simultaneously is
        exactly that case, and it is the race that would otherwise leave an organization with zero
        administrators and no way back in without platform intervention.

        Coarse on purpose. The alternative -- a narrower lock plus a serializable isolation level
        and retry loop -- would be more concurrent and would need its own retry semantics on a code
        path an administrator uses a handful of times a year. Contention here is a screen with one
        person on it.

        ``with_for_update()`` is a no-op on SQLite (the dialect omits the clause), so the
        invariant's *concurrency* claim is qualified against real PostgreSQL, never inferred from
        the SQLite
        unit suite -- see ``tests/integration/test_dhealthaccess001_postgres_qualification.py``."""
        organization = session.scalar(
            select(Organization).where(Organization.id == organization_id).with_for_update()
        )
        if organization is None:
            raise OrganizationNotFound(str(organization_id))
        return _to_record(organization)

    def require_type(self, organization_id: uuid.UUID, expected: OrganizationType) -> None:
        """Fails closed if ``organization_id`` does not reference an organization of the
        expected type (or does not exist / is not ACTIVE) -- the repository-level check standing
        in for the cross-table CHECK constraint SQL cannot express portably."""
        record = self.get(organization_id)
        if record.status != OrganizationStatus.ACTIVE.value:
            raise InvalidOrganizationType(
                f"organization {organization_id} is not ACTIVE (status={record.status!r})"
            )
        if record.org_type != expected.value:
            raise InvalidOrganizationType(
                f"organization {organization_id} is {record.org_type!r}, "
                f"expected {expected.value!r}"
            )

    def list_active(self, org_type: OrganizationType) -> list[OrganizationRecord]:
        with self._sessions() as session:
            organizations = session.scalars(
                select(Organization).where(
                    Organization.org_type == org_type.value,
                    Organization.status == OrganizationStatus.ACTIVE.value,
                )
            ).all()
            return [_to_record(organization) for organization in organizations]

    def get_many(
        self, organization_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, OrganizationRecord]:
        """DH-LAB-001 §21: batched sibling of ``get`` for list-response builders -- one query
        regardless of how many distinct organization ids a page needs, never one lookup per row.
        An id absent from the returned dict simply was not found; unlike ``get``, this never
        raises -- callers that require every id to resolve (they always do, in practice, since
        these ids come from a foreign key) check for missing keys themselves, exactly like
        ``_require_patient_identities``'s own missing-id discipline."""
        unique_ids = list(dict.fromkeys(organization_ids))
        if not unique_ids:
            return {}
        with self._sessions() as session:
            rows = session.scalars(
                select(Organization).where(Organization.id.in_(unique_ids))
            ).all()
            return {row.id: _to_record(row) for row in rows}


def _to_record(organization: Organization) -> OrganizationRecord:
    return OrganizationRecord(
        id=organization.id,
        org_type=organization.org_type,
        name=organization.name,
        status=organization.status,
    )
