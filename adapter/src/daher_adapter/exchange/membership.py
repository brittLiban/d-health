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

from sqlalchemy import func, or_, select
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
    OrganizationType.PROVIDER: frozenset(
        {
            OrganizationRole.ORG_ADMIN,
            OrganizationRole.CLINICIAN,
            # DHEALTH-BILLING-001/ADR-066: valid at *both* organization types, unlike every other
            # non-admin role in this matrix. A clinic charges for the consultation it performed and
            # a laboratory charges for the tests it performed, so both need the same money
            # authority over their own invoices. Additive and never implied: ORG_ADMIN and
            # CLINICIAN do not confer it, and it confers neither of them.
            OrganizationRole.BILLING_CLERK,
        }
    ),
    OrganizationType.LAB: frozenset(
        {
            OrganizationRole.ORG_ADMIN,
            OrganizationRole.LAB_TECH,
            # DH-EX-012/ADR-044: additive, not a replacement for LAB_TECH -- a membership may hold
            # both roles simultaneously (the same human may enter and later release), exactly one
            # of them (entry-only or release-only), or neither. ORG_ADMIN does not imply this role.
            OrganizationRole.LAB_RESULT_RELEASER,
            OrganizationRole.BILLING_CLERK,
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

    def search(self, *, query: str, limit: int = 20) -> list[ExchangeIdentityRecord]:
        """DH-MVP-ONBOARD-001 §22: find a person who has already signed into DHealth.

        Exists so a platform administrator can attach a facility's first administrator without
        anybody reading a uuid out of the database by hand. Every constraint here is deliberate:

        - **Prefix matching on display name and email, never fuzzy.** This repository does not do
          approximate identity matching anywhere -- ``PatientReference`` is exact-or-create for the
          same reason -- and an administrative lookup for someone the operator is already speaking
          to does not need it.
        - **A bounded result set**, so the endpoint cannot be walked as an enumeration of everyone
          who has ever authenticated.
        - **Only ACTIVE identities.** An inactivated identity is not somebody to attach to a new
          facility.
        - **Ordered deterministically**, so two identical queries do not return different people in
          different orders and an operator can trust what they are looking at.

        A match grants nothing. Creating the membership is a separate, explicit, audited act -- the
        same separation ``ExchangeIdentity``'s own docstring describes between "DHealth has seen a
        valid token for this human" and "this human may do something".
        """
        needle = query.strip()
        if not needle:
            return []
        pattern = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._sessions() as session:
            identities = session.scalars(
                select(ExchangeIdentity)
                .where(
                    ExchangeIdentity.status == ExchangeIdentityStatus.ACTIVE.value,
                    or_(
                        ExchangeIdentity.display_name.ilike(pattern, escape="\\"),
                        ExchangeIdentity.email.ilike(pattern, escape="\\"),
                    ),
                )
                .order_by(ExchangeIdentity.display_name, ExchangeIdentity.id)
                .limit(limit)
            ).all()
            return [_to_identity_record(identity) for identity in identities]


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

    def find_active_in_session(
        self, session: Session, *, organization_id: uuid.UUID, identity_id: uuid.UUID
    ) -> MembershipRecord | None:
        """``find_active``'s session-participating sibling.

        DHEALTH-NATIVE-ACCESS-001 needs the caller's *own* membership re-read inside the mutating
        transaction, behind the organization lock, rather than in a separate earlier session:
        between the HTTP layer's authorization check and the mutation, another administrator may
        have deactivated this actor. ``find_active`` opens its own session and therefore cannot see
        the
        locked, in-flight state."""
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

    def get_in_session(self, session: Session, membership_id: uuid.UUID) -> MembershipRecord:
        """DHEALTH-NATIVE-ACCESS-001: read one membership by its own id, whatever its status,
        **inside the caller's transaction**.

        Deliberately status-agnostic, unlike ``find_active``: the staff access lifecycle must be
        able to see an INACTIVE membership in order to reactivate it. And deliberately
        session-taking with no own-session sibling: a version that opened its own session would
        report pre-lock committed state, which for a *status* is exactly the stale read this
        lifecycle must not make. No
        caller needs the unlocked form, so it does not exist."""
        membership = session.get(OrganizationMembership, membership_id)
        if membership is None:
            raise MembershipNotFound(str(membership_id))
        return _to_membership_record(membership)

    def set_inactive(self, membership_id: uuid.UUID) -> MembershipRecord:
        """Owns its own transaction. Retained for the callers that already have it -- the demo
        seed's test support and `FacilityReadinessEvaluator`'s fixtures -- while the product's
        supported revocation path goes through ``set_status_in_session`` below, because that one
        must commit in the same transaction as its audit row and its last-administrator check.

        Note what this method deliberately does **not** do: it does not check the membership's
        organization, and it does not check whether the organization is about to lose its last
        administrator. It is an unguarded primitive, which is exactly why it must not be reachable
        from an HTTP handler."""
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

    def set_status_in_session(
        self,
        session: Session,
        *,
        membership_id: uuid.UUID,
        status: OrganizationMembershipStatus,
    ) -> tuple[MembershipRecord, bool]:
        """DHEALTH-NATIVE-ACCESS-001: the transition the product's supported revocation/restoration
        path uses. Returns ``(record, changed)`` -- ``changed`` is False when the membership already
        held the requested status, so the caller can answer "already inactive"/"already active"
        without a second read that could race.

        The caller owns the transaction, for the reason DH-EX-002R established for every other
        security mutation in this package: the status change and its authoritative audit row must
        commit or roll back together. It also lets the caller take the organization-level lock
        *first* (see ``exchange/staff_access.py``), which is what makes the last-administrator
        invariant hold under concurrency rather than merely under a single-threaded test.

        ``with_for_update()`` on the row itself is belt-and-braces behind that organization lock:
        the organization lock serializes the four lifecycle operations against each other, and this
        one additionally serializes against any other writer of this membership row."""
        membership = session.scalar(
            select(OrganizationMembership)
            .where(OrganizationMembership.id == membership_id)
            .with_for_update()
        )
        if membership is None:
            raise MembershipNotFound(str(membership_id))
        if membership.status == status.value:
            return _to_membership_record(membership), False
        membership.status = status.value
        session.flush()
        return _to_membership_record(membership), True

    def count_active_with_role_in_session(
        self,
        session: Session,
        *,
        organization_id: uuid.UUID,
        role: OrganizationRole,
    ) -> int:
        """How many ACTIVE memberships of this organization currently hold ``role``.

        DHEALTH-NATIVE-ACCESS-001 uses this with ``role=ORG_ADMIN`` to enforce the
        last-active-administrator invariant. It is counted *inside* the mutation's own transaction,
        **after** the mutation has been flushed, so the number is what the database would actually
        commit -- not what it looked like before the change, which is the version of this check that
        two concurrent administrators could both pass.

        Counted twice per mutation -- once before, once after -- because the invariant is a
        *transition* rule: an organization that already had zero active administrators must not be
        frozen by it (see ``_guard_last_administrator``)."""
        statement = (
            select(func.count())
            .select_from(OrganizationMembership)
            .join(
                OrganizationRoleAssignment,
                OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
            )
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.status == OrganizationMembershipStatus.ACTIVE.value,
                OrganizationRoleAssignment.role == role.value,
            )
        )
        return int(session.scalar(statement) or 0)


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

    def list_for_membership_in_session(
        self, session: Session, membership_id: uuid.UUID
    ) -> frozenset[str]:
        """``list_for_membership``'s session-participating sibling. The staff access lifecycle needs
        the role set as it will actually be committed -- read inside the mutating transaction, after
        the flush -- so that what an administrator is told they changed is what the database holds,
        not a pre-mutation snapshot."""
        roles = session.scalars(
            select(OrganizationRoleAssignment.role).where(
                OrganizationRoleAssignment.membership_id == membership_id
            )
        ).all()
        return frozenset(roles)

    def remove(self, *, membership_id: uuid.UUID, role: OrganizationRole) -> None:
        """Owns its own transaction, and is deliberately silent about whether anything was there to
        remove. Retained unchanged for its existing test callers; the product's supported revocation
        path uses ``remove_in_session`` below, which reports that fact, because "this role was
        removed" and "this role was never held" are different answers an administrator is owed."""
        with self._sessions.begin() as session:
            assignment = session.scalar(
                select(OrganizationRoleAssignment).where(
                    OrganizationRoleAssignment.membership_id == membership_id,
                    OrganizationRoleAssignment.role == role.value,
                )
            )
            if assignment is not None:
                session.delete(assignment)

    def remove_in_session(
        self, session: Session, *, membership_id: uuid.UUID, role: OrganizationRole
    ) -> bool:
        """DHEALTH-NATIVE-ACCESS-001: returns True if a row was actually deleted, False if the
        membership did not hold this role. The caller owns the transaction so the deletion and its
        authoritative audit row commit or roll back together (DH-EX-002R), and so the
        last-administrator check can run behind the same lock before anything is committed.

        Deliberately does **not** require the membership to be ACTIVE, unlike ``assign``. Removing a
        role only ever *reduces* authority, so refusing it on an inactive membership would protect
        nothing and would leave an administrator unable to trim a departed colleague's roles before
        restoring their access -- which matters here, because reactivation restores exactly the
        roles
        still attached to the membership (see ``exchange/staff_access.py``'s reactivation note)."""
        assignment = session.scalar(
            select(OrganizationRoleAssignment)
            .where(
                OrganizationRoleAssignment.membership_id == membership_id,
                OrganizationRoleAssignment.role == role.value,
            )
            .with_for_update()
        )
        if assignment is None:
            return False
        session.delete(assignment)
        session.flush()
        return True


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
