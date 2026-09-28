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

from sqlalchemy import false, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.capabilities import (
    SYSTEM_ROLE_TEMPLATES,
    Capability,
    CapabilityGrantView,
    OverrideEffect,
    check_templates_survive_the_type_filter,
    describe_capabilities,
    resolve_effective_capabilities,
)
from daher_adapter.exchange.models import (
    ExchangeIdentity,
    ExchangeIdentityStatus,
    MembershipCapabilityOverride,
    Organization,
    OrganizationMembership,
    OrganizationMembershipStatus,
    OrganizationRole,
    OrganizationRoleAssignment,
    OrganizationStatus,
    OrganizationType,
)
from daher_adapter.models import utc_now

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


# DHEALTH-ACCESS-CAPABILITIES-001: run at import, immediately after the matrix above exists.
# Catches a role whose template carries a capability its organization type cannot hold -- which
# would silently evaporate under the resolver's type filter rather than raising anywhere.
check_templates_survive_the_type_filter(ALLOWED_ROLES_BY_ORG_TYPE)


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

    def get(self, membership_id: uuid.UUID) -> MembershipRecord:
        """One membership by id, in a session of its own.

        DHEALTH-ACCESS-CAPABILITIES-001: for the Team & Access *read* path, which has no
        transaction to join. Every mutation path keeps using `get_in_session` -- it must resolve
        the target inside the lock it already holds, or two administrators can act on a membership
        whose state changed between the lookup and the write."""
        with self._sessions() as session:
            return self.get_in_session(session, membership_id)

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


class MembershipCapabilityRepository:
    """Member-specific capability overrides, and the one place effective authority is read.

    DHEALTH-ACCESS-CAPABILITIES-001/ADR-067. `exchange/capabilities.py` owns the *rule*
    (`(templates ∪ grants) − denies`); this class owns the *rows* and the membership-state
    preconditions the rule is not allowed to guess at.

    Every method has an ``_in_session`` form because the caller that matters most --
    `staff_access.py`'s mutation path -- must read effective authority **inside its own
    transaction, after its own flush, under the organization lock it already holds**. An effective
    count taken outside that transaction is the version of the last-administrator check that two
    concurrent administrators both pass.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    # -- reading ---------------------------------------------------------------------------------

    def overrides_for_membership_in_session(
        self, session: Session, membership_id: uuid.UUID
    ) -> tuple[frozenset[str], frozenset[str]]:
        """``(grants, denies)`` as raw capability keys, exactly as stored.

        Returned as keys rather than `Capability` members so an unknown key -- a row written by a
        newer schema than this build -- travels intact to the resolver, which ignores it. Parsing
        here would raise on a row that must simply have no effect."""
        rows = session.scalars(
            select(MembershipCapabilityOverride).where(
                MembershipCapabilityOverride.membership_id == membership_id
            )
        ).all()
        grants = {row.capability for row in rows if row.effect == OverrideEffect.GRANT.value}
        denies = {row.capability for row in rows if row.effect == OverrideEffect.DENY.value}
        return frozenset(grants), frozenset(denies)

    def effective_for_membership_in_session(
        self,
        session: Session,
        *,
        membership_id: uuid.UUID,
        membership_status: str,
        organization_type: OrganizationType | str,
        organization_status: str,
    ) -> frozenset[Capability]:
        """What this membership may actually do, right now, in this transaction.

        **The membership-state gate lives here and nowhere else.** An INACTIVE membership, or any
        membership of an INACTIVE organization, has *no* ordinary facility capabilities regardless
        of what its templates or overrides say (§16) -- returned as an empty set before any row is
        read, so a deactivated member cannot be given authority by a stale grant. Putting this in
        the pure resolver was considered and rejected: the resolver would then need to be handed
        membership state by every caller, and the one caller that forgot would be a silent
        authorization hole.
        """
        if membership_status != OrganizationMembershipStatus.ACTIVE.value:
            return frozenset()
        if organization_status != OrganizationStatus.ACTIVE.value:
            return frozenset()
        roles, grants, denies = self._authority_rows_in_session(session, membership_id)
        return resolve_effective_capabilities(
            roles=roles,
            grants=grants,
            denies=denies,
            organization_type=organization_type,
        )

    def _authority_rows_in_session(
        self, session: Session, membership_id: uuid.UUID
    ) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """This membership's role names, granted capabilities and denied capabilities, in **one**
        round trip.

        A `UNION ALL` of two tables projected onto the same `(kind, value)` shape, rather than two
        `SELECT`s. This runs on every authorized request, so its cost is the cost of authorization
        itself: the two-query version added one statement to every gated endpoint in the
        application, which `test_provider_release_version_history_query_count_does_not_scale_with_
        version_count` noticed immediately and correctly.

        Deliberately **not** a join between the two tables. Role assignments and overrides are
        independent one-to-many relations, so joining them produces a cartesian product -- three
        overrides and two roles would return six rows to express five facts, and the waste grows
        with both. A union returns exactly one row per fact.
        """
        rows = session.execute(
            select(
                literal("ROLE").label("kind"),
                OrganizationRoleAssignment.role.label("value"),
            )
            .where(OrganizationRoleAssignment.membership_id == membership_id)
            .union_all(
                select(
                    MembershipCapabilityOverride.effect.label("kind"),
                    MembershipCapabilityOverride.capability.label("value"),
                ).where(MembershipCapabilityOverride.membership_id == membership_id)
            )
        ).all()

        roles = {value for kind, value in rows if kind == "ROLE"}
        grants = {value for kind, value in rows if kind == OverrideEffect.GRANT.value}
        denies = {value for kind, value in rows if kind == OverrideEffect.DENY.value}
        return frozenset(roles), frozenset(grants), frozenset(denies)

    def effective_for_membership(
        self,
        *,
        membership_id: uuid.UUID,
        membership_status: str,
        organization_type: OrganizationType | str,
        organization_status: str,
    ) -> frozenset[Capability]:
        """`effective_for_membership_in_session` in a session of its own.

        For the authorization gates, which are called once per request and have no transaction to
        join. The mutation paths must **not** use this form -- they need the read inside their own
        transaction and under their own lock, which is what makes the last-administrator invariant
        hold under concurrency."""
        with self._sessions() as session:
            return self.effective_for_membership_in_session(
                session,
                membership_id=membership_id,
                membership_status=membership_status,
                organization_type=organization_type,
                organization_status=organization_status,
            )

    def describe_for_membership_in_session(
        self,
        session: Session,
        *,
        membership_id: uuid.UUID,
        membership_status: str,
        organization_type: OrganizationType | str,
    ) -> tuple[CapabilityGrantView, ...]:
        """The whole catalog for this member: held or not, and why (§20).

        What `Équipe et accès` renders. Deliberately does **not** take `organization_status`: this
        is a description of how a member's access is *configured*, and an administrator looking at
        a suspended organization still needs to see what its staff would hold. The gates are what
        enforce the organization being active, and they do."""
        roles, grants, denies = self._authority_rows_in_session(session, membership_id)
        return describe_capabilities(
            roles=roles,
            grants=grants,
            denies=denies,
            organization_type=organization_type,
            membership_is_active=membership_status == OrganizationMembershipStatus.ACTIVE.value,
        )

    def describe_for_membership(
        self,
        *,
        membership_id: uuid.UUID,
        membership_status: str,
        organization_type: OrganizationType | str,
    ) -> tuple[CapabilityGrantView, ...]:
        """`describe_for_membership_in_session` in a session of its own, for read endpoints."""
        with self._sessions() as session:
            return self.describe_for_membership_in_session(
                session,
                membership_id=membership_id,
                membership_status=membership_status,
                organization_type=organization_type,
            )

    def count_active_with_capability_in_session(
        self,
        session: Session,
        *,
        organization_id: uuid.UUID,
        capability: Capability,
    ) -> int:
        """How many ACTIVE memberships of this organization hold ``capability`` **effectively**.

        This replaces `count_active_with_role_in_session(role=ORG_ADMIN)` as the last-administrator
        invariant's counter, and the difference is the whole of §22. The old query asked whether
        anybody still held a role *name*; an administrator who kept `ORG_ADMIN` and was denied
        `staff.access.manage` individually passed it while leaving the organization with nobody who
        could actually administer it. This asks the question that matters.

        **One SQL statement, not a Python loop.** The loop version -- fetch the members, resolve
        each in turn, count -- is the shape that races: two concurrent removals each see the other's
        victim still counted. Expressed as `EXISTS`/`NOT EXISTS` subqueries, the whole count is
        evaluated by the database inside the caller's transaction, under the lock it already holds.

        The template half is translated through the code-defined catalog before the query is built:
        `SYSTEM_ROLE_TEMPLATES` says which role names carry this capability, and that becomes a
        literal `role IN (...)`. That is a constant lookup, not a per-member computation, so it
        costs nothing and keeps the templates in one place.
        """
        granting_roles = sorted(
            role.value
            for role, capabilities in SYSTEM_ROLE_TEMPLATES.items()
            if capability in capabilities
        )

        via_template = (
            select(OrganizationRoleAssignment.id)
            .where(
                OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
                OrganizationRoleAssignment.role.in_(granting_roles),
            )
            .exists()
            if granting_roles
            # A capability no template carries -- possible, and it must not silently become
            # "everybody". `false()` rather than an empty `IN ()`, which some backends reject.
            else false()
        )
        via_grant = (
            select(MembershipCapabilityOverride.id)
            .where(
                MembershipCapabilityOverride.membership_id == OrganizationMembership.id,
                MembershipCapabilityOverride.capability == capability.value,
                MembershipCapabilityOverride.effect == OverrideEffect.GRANT.value,
            )
            .exists()
        )
        denied = (
            select(MembershipCapabilityOverride.id)
            .where(
                MembershipCapabilityOverride.membership_id == OrganizationMembership.id,
                MembershipCapabilityOverride.capability == capability.value,
                MembershipCapabilityOverride.effect == OverrideEffect.DENY.value,
            )
            .exists()
        )

        statement = (
            select(func.count())
            .select_from(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.status == OrganizationMembershipStatus.ACTIVE.value,
                or_(via_template, via_grant),
                ~denied,
            )
        )
        return int(session.scalar(statement) or 0)

    # -- writing ---------------------------------------------------------------------------------

    def set_override_in_session(
        self,
        session: Session,
        *,
        membership_id: uuid.UUID,
        capability: Capability,
        effect: OverrideEffect,
        actor_identity_id: uuid.UUID,
    ) -> bool:
        """Set or flip one override. Returns ``True`` if anything actually changed.

        Idempotent by design: setting a GRANT that is already a GRANT reports no change rather than
        writing a second row, which the unique constraint would refuse anyway. Flipping a GRANT to
        a DENY updates the existing row instead of leaving two contradictory ones -- the model has
        exactly one opinion per (membership, capability) and the constraint enforces it.

        The returned flag is what lets the caller audit a real mutation and stay silent on a no-op,
        so the audit table records decisions rather than clicks."""
        existing = session.scalar(
            select(MembershipCapabilityOverride).where(
                MembershipCapabilityOverride.membership_id == membership_id,
                MembershipCapabilityOverride.capability == capability.value,
            )
        )
        if existing is not None:
            if existing.effect == effect.value:
                return False
            existing.effect = effect.value
            existing.created_by_identity_id = actor_identity_id
            existing.created_at = utc_now()
            session.flush()
            return True
        session.add(
            MembershipCapabilityOverride(
                membership_id=membership_id,
                capability=capability.value,
                effect=effect.value,
                created_by_identity_id=actor_identity_id,
            )
        )
        session.flush()
        return True

    def clear_override_in_session(
        self, session: Session, *, membership_id: uuid.UUID, capability: Capability
    ) -> bool:
        """Remove one override, returning it to whatever the member's templates say. ``True`` if a
        row was actually removed.

        A genuine delete rather than a tombstone: the *decision* survives in the audit table
        (`capability_override_cleared`), which is where §56 requires the history to live, and
        keeping a dead row here would mean the unique constraint could no longer be the thing that
        guarantees one opinion per capability."""
        existing = session.scalar(
            select(MembershipCapabilityOverride).where(
                MembershipCapabilityOverride.membership_id == membership_id,
                MembershipCapabilityOverride.capability == capability.value,
            )
        )
        if existing is None:
            return False
        session.delete(existing)
        session.flush()
        return True
