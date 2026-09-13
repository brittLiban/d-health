"""DH-EX-002: exchange identity bootstrap, organization membership, and role assignment.

``ExchangeIdentityRepository.get_or_create`` is insert-first-wins on ``(issuer, subject)``, the
same pattern ``daher_adapter.ledger.LedgerRepository.claim`` and
``exchange/identity.py``'s patient/practitioner ``get_or_create`` already prove -- a valid token
for a never-seen ``(issuer, subject)`` creates a row; a replay resolves back to the same row. This
creates an *identity*, never organization access -- ``OrganizationMembershipRepository`` is the
only thing that grants that, and only an explicit call ever creates a membership row.

``OrganizationRoleAssignmentRepository.assign`` enforces the provider/lab role matrix itself, by
joining through to the membership's own organization, rather than trusting a caller-supplied
organization type -- the same "the repository is the enforcement point, not just one caller" rule
``exchange/catalog.py``'s lab-offering org-type check already follows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    ExchangeIdentity,
    ExchangeIdentityStatus,
    Organization,
    OrganizationMembership,
    OrganizationMembershipStatus,
    OrganizationRole,
    OrganizationRoleAssignment,
    OrganizationType,
)

# DH-EX-002's approved provider/lab role matrix. PLATFORM_ADMIN is never a member of this
# dictionary -- it is not an OrganizationRole and is never granted through this repository.
# Public (no leading underscore): `exchange/api.py` pre-validates a whole requested role list
# against this same matrix *before* creating a membership row, so an invalid role in a multi-role
# request 422s before anything is committed, rather than after a partial grant. `assign()` below
# still re-checks it independently -- the repository stays the enforcement point, not just the
# one caller that happens to pre-validate today.
ALLOWED_ROLES_BY_ORG_TYPE: dict[OrganizationType, frozenset[OrganizationRole]] = {
    OrganizationType.PROVIDER: frozenset({OrganizationRole.ORG_ADMIN, OrganizationRole.CLINICIAN}),
    OrganizationType.LAB: frozenset(
        {
            OrganizationRole.ORG_ADMIN,
            OrganizationRole.LAB_TECH,
            # DH-EX-012/ADR-044: additive, not a replacement for LAB_TECH -- a membership may hold
            # both roles simultaneously (the same human may enter and later release), exactly one
            # of them (entry-only or release-only), or neither. ORG_ADMIN does not imply this role.
            OrganizationRole.LAB_RESULT_RELEASER,
        }
    ),
}


class InvalidRoleForOrganizationType(RuntimeError):
    """A role was requested that this organization's type does not permit (e.g. LAB_TECH inside
    a PROVIDER organization)."""


class MembershipInactive(RuntimeError):
    """A role was requested against a membership that is not ACTIVE. An inactive membership
    grants nothing and receives nothing -- reactivation is a distinct, explicit action that this
    ticket's endpoint set does not implement (see `exchange/api.py`)."""


class MembershipNotFound(RuntimeError):
    pass


class IdentityNotFound(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExchangeIdentityRecord:
    id: uuid.UUID
    issuer: str
    subject: str
    display_name: str | None
    email: str | None
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    id: uuid.UUID
    organization_id: uuid.UUID
    identity_id: uuid.UUID
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RoleAssignmentRecord:
    id: uuid.UUID
    membership_id: uuid.UUID
    role: str
    created_at: datetime


class ExchangeIdentityRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def get_or_create(
        self,
        *,
        issuer: str,
        subject: str,
        display_name: str | None = None,
        email: str | None = None,
    ) -> tuple[ExchangeIdentityRecord, bool]:
        """Returns ``(record, created)``. Only a validated token's ``(issuer, subject)`` may ever
        reach this method (see ``exchange/api.py``'s authentication dependency) -- creating this
        row grants zero organization access on its own."""
        if not issuer.strip() or not subject.strip():
            raise ValueError("issuer and subject are required -- exact identity only")
        identity = ExchangeIdentity(
            issuer=issuer,
            subject=subject,
            display_name=display_name,
            email=email,
            status=ExchangeIdentityStatus.ACTIVE.value,
        )
        with self._sessions() as session:
            try:
                session.add(identity)
                session.commit()
                return _to_identity_record(identity), True
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(ExchangeIdentity).where(
                        ExchangeIdentity.issuer == issuer, ExchangeIdentity.subject == subject
                    )
                )
                if existing is None:
                    raise
                return _to_identity_record(existing), False

    def get(self, identity_id: uuid.UUID) -> ExchangeIdentityRecord:
        with self._sessions() as session:
            identity = session.get(ExchangeIdentity, identity_id)
            if identity is None:
                raise IdentityNotFound(str(identity_id))
            return _to_identity_record(identity)


class OrganizationMembershipRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def create(
        self,
        *,
        organization_id: uuid.UUID,
        identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> tuple[MembershipRecord, bool]:
        """Insert-first-wins on ``(organization_id, identity_id)``. Returns ``(record, created)``
        -- a repeat call for the same pair is a no-op replay, never a duplicate row and never an
        error, matching the same idempotent-write discipline used throughout this package.

        When `session` is given, the caller owns the outer transaction (DH-EX-002R: membership
        creation + its authoritative audit row must commit or roll back together) -- the
        insert-or-fetch attempt below runs inside a SAVEPOINT so a duplicate-key hit only unwinds
        to that savepoint, never poisoning the caller's outer transaction. When `session` is
        omitted, this method still owns and commits its own transaction exactly as before,
        preserving every existing caller."""
        if session is not None:
            return self._create_in_session(
                session, organization_id=organization_id, identity_id=identity_id
            )
        with self._sessions() as owned_session:
            record, created = self._create_in_session(
                owned_session, organization_id=organization_id, identity_id=identity_id
            )
            owned_session.commit()
            return record, created

    def _create_in_session(
        self, session: Session, *, organization_id: uuid.UUID, identity_id: uuid.UUID
    ) -> tuple[MembershipRecord, bool]:
        membership = OrganizationMembership(
            organization_id=organization_id,
            identity_id=identity_id,
            status=OrganizationMembershipStatus.ACTIVE.value,
        )
        try:
            with session.begin_nested():
                session.add(membership)
                session.flush()
            return _to_membership_record(membership), True
        except IntegrityError:
            existing = session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.identity_id == identity_id,
                )
            )
            if existing is None:
                raise
            return _to_membership_record(existing), False

    def find_active(
        self, *, organization_id: uuid.UUID, identity_id: uuid.UUID
    ) -> MembershipRecord | None:
        with self._sessions() as session:
            membership = session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.identity_id == identity_id,
                    OrganizationMembership.status == OrganizationMembershipStatus.ACTIVE.value,
                )
            )
            return None if membership is None else _to_membership_record(membership)

    def list_for_organization(self, organization_id: uuid.UUID) -> list[MembershipRecord]:
        with self._sessions() as session:
            memberships = session.scalars(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id
                )
            ).all()
            return [_to_membership_record(membership) for membership in memberships]

    def list_for_identity(self, identity_id: uuid.UUID) -> list[MembershipRecord]:
        with self._sessions() as session:
            memberships = session.scalars(
                select(OrganizationMembership).where(
                    OrganizationMembership.identity_id == identity_id
                )
            ).all()
            return [_to_membership_record(membership) for membership in memberships]

    def set_inactive(self, membership_id: uuid.UUID) -> MembershipRecord:
        with self._sessions.begin() as session:
            membership = session.scalar(
                select(OrganizationMembership)
                .where(OrganizationMembership.id == membership_id)
                .with_for_update()
            )
            if membership is None:
                raise MembershipNotFound(str(membership_id))
            membership.status = OrganizationMembershipStatus.INACTIVE.value
            session.flush()
            return _to_membership_record(membership)


class OrganizationRoleAssignmentRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def assign(
        self,
        *,
        membership_id: uuid.UUID,
        role: OrganizationRole,
        session: Session | None = None,
    ) -> tuple[RoleAssignmentRecord, bool]:
        """Fails closed with `InvalidRoleForOrganizationType` if `role` is not valid for the
        membership's own organization's type -- looked up here, never trusted from a caller.
        Fails closed with `MembershipInactive` if the membership is not ACTIVE -- an inactive
        membership never gains a role through this path, silently or otherwise. Insert-first-wins
        on ``(membership_id, role)``: re-assigning the same role is a no-op replay, matching every
        other idempotent write in this package.

        When `session` is given, the caller owns the outer transaction (DH-EX-002R: role
        assignment + its authoritative audit row must commit or roll back together); the
        insert-or-fetch attempt runs inside a SAVEPOINT so a duplicate-key hit does not poison the
        caller's outer transaction. When `session` is omitted, this method still owns and commits
        its own transaction exactly as before."""
        if session is not None:
            return self._assign_in_session(session, membership_id=membership_id, role=role)
        with self._sessions() as owned_session:
            record, created = self._assign_in_session(
                owned_session, membership_id=membership_id, role=role
            )
            owned_session.commit()
            return record, created

    def _assign_in_session(
        self, session: Session, *, membership_id: uuid.UUID, role: OrganizationRole
    ) -> tuple[RoleAssignmentRecord, bool]:
        membership = session.get(OrganizationMembership, membership_id)
        if membership is None:
            raise MembershipNotFound(str(membership_id))
        if membership.status != OrganizationMembershipStatus.ACTIVE.value:
            raise MembershipInactive(str(membership_id))
        organization = session.get(Organization, membership.organization_id)
        if organization is None:
            raise RuntimeError(f"organization missing for membership {membership_id} despite FK")
        allowed = ALLOWED_ROLES_BY_ORG_TYPE[OrganizationType(organization.org_type)]
        if role not in allowed:
            raise InvalidRoleForOrganizationType(
                f"role {role.value!r} is not valid for a {organization.org_type!r} organization"
            )

        assignment = OrganizationRoleAssignment(membership_id=membership_id, role=role.value)
        try:
            with session.begin_nested():
                session.add(assignment)
                session.flush()
            return _to_role_record(assignment), True
        except IntegrityError:
            existing = session.scalar(
                select(OrganizationRoleAssignment).where(
                    OrganizationRoleAssignment.membership_id == membership_id,
                    OrganizationRoleAssignment.role == role.value,
                )
            )
            if existing is None:
                raise
            return _to_role_record(existing), False

    def list_for_membership(self, membership_id: uuid.UUID) -> frozenset[str]:
        with self._sessions() as session:
            roles = session.scalars(
                select(OrganizationRoleAssignment.role).where(
                    OrganizationRoleAssignment.membership_id == membership_id
                )
            ).all()
            return frozenset(roles)

    def remove(self, *, membership_id: uuid.UUID, role: OrganizationRole) -> None:
        with self._sessions.begin() as session:
            assignment = session.scalar(
                select(OrganizationRoleAssignment).where(
                    OrganizationRoleAssignment.membership_id == membership_id,
                    OrganizationRoleAssignment.role == role.value,
                )
            )
            if assignment is not None:
                session.delete(assignment)


def _to_identity_record(identity: ExchangeIdentity) -> ExchangeIdentityRecord:
    return ExchangeIdentityRecord(
        id=identity.id,
        issuer=identity.issuer,
        subject=identity.subject,
        display_name=identity.display_name,
        email=identity.email,
        status=identity.status,
        created_at=identity.created_at,
    )


def _to_membership_record(membership: OrganizationMembership) -> MembershipRecord:
    return MembershipRecord(
        id=membership.id,
        organization_id=membership.organization_id,
        identity_id=membership.identity_id,
        status=membership.status,
        created_at=membership.created_at,
    )


def _to_role_record(assignment: OrganizationRoleAssignment) -> RoleAssignmentRecord:
    return RoleAssignmentRecord(
        id=assignment.id,
        membership_id=assignment.membership_id,
        role=assignment.role,
        created_at=assignment.created_at,
    )
