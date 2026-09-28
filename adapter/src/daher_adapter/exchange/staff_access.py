"""DHEALTH-NATIVE-ACCESS-001: the organization staff access lifecycle.

DHEALTH-NATIVE-SEC-001's P1 finding (F1) was not that DHealth failed to enforce a withdrawn
membership or a removed role -- it enforces both, on the very next request, against a token that is
still cryptographically valid. The finding was that **nothing in the product could produce that
state**. ``OrganizationMembershipRepository.set_inactive`` and
``OrganizationRoleAssignmentRepository.remove`` both existed and neither had a caller, so the
security qualification had to reach into the database with ``psql`` to create the state it was
testing. Authority could be granted through the product and withdrawn only by a database
administrator.

This module is the supported path. Four operations, and deliberately only four:

- deactivate a membership
- reactivate a membership
- add one allowed organization role
- remove one organization role

Everything else an administration product would eventually want -- inviting people, creating or
disabling Keycloak accounts, resetting passwords, editing an organization -- is absent on purpose.

── Why a service module rather than more handler bodies in ``api.py`` ──

Each of these four operations is a transaction with four steps that must not be reordered, and
``api.py`` is already 8400 lines. Putting them here means the ordering is stated once, is unit
testable without an HTTP client, and cannot silently diverge between the four endpoints -- exactly
the reasoning ``exchange/authorization.py``'s own docstring gives for being the single place
organization-scoped authorization decisions are made.

── The transaction, in order, and why that order ──

1. **Lock the organization row.** ``OrganizationRepository.lock_in_session`` takes a ``FOR UPDATE``
   lock on the organization, which serializes that organization's whole staff access lifecycle
   against itself. Locking the membership row each operation happens to touch would not be enough:
   two administrators removing *each other's* ``ORG_ADMIN`` touch two different membership rows,
   both pass a pre-flight "there are two admins" check, and the organization ends with zero.
2. **Re-check the actor's own authority, inside the lock.** The HTTP layer already called
   ``require_org_admin``, but that ran in its own earlier session. Between then and here, another
   administrator may have deactivated this actor or removed their ``ORG_ADMIN``. Without this
   re-read, an administrator who was removed a moment ago could still complete the request they had
   already started -- an inactive admin administering, which §14 of this ticket forbids. This is the
   one step that exists purely because of concurrency, and it is why the authorization decision is
   made twice rather than once.
3. **Resolve the target membership and prove it belongs to this organization.** The membership id
   comes from the URL. A membership that exists but belongs to another organization is reported as
   *not found*, never as "exists elsewhere" -- the same anti-enumeration rule
   ``exchange/authorization.py`` applies to organizations, applied to memberships.
4. **Mutate, flush, then evaluate the last-administrator invariant, then audit.** The invariant is
   checked *after* the flush, against what the database would actually commit, because a check
   against the pre-mutation state is precisely the check two concurrent callers can both pass. The
   audit row is inserted in the same transaction as the mutation (DH-EX-002R), so a committed
   authority change can never exist without its audit row, or the reverse.

── The last-active-administrator invariant, and its exact shape ──

    An access mutation may not take an organization from *one or more* active ``ORG_ADMIN``
    memberships to *zero*.

No policy for this existed anywhere in the repository (no ADR, no code, no test) before this ticket,
so this is the narrow rule §12 prescribes in that case. Three deliberate properties:

- **It is a transition rule, not a floor.** It refuses an operation that *causes* the count to reach
  zero. An organization that already has zero active administrators -- which is a real, reachable
  state today, since ``POST .../memberships`` will happily create a membership holding only
  ``CLINICIAN`` -- is not frozen: removing an unrelated role there is still allowed, and a platform
  administrator can still grant ``ORG_ADMIN`` to somebody. A naive "after the change there must be
  at least one admin" check would refuse both and would brick exactly the organizations that most
  need fixing.
- **It binds a platform administrator too — ADR-063, decided by Liban 2026-09-27.**
  ``require_org_admin`` lets a platform admin through, so they do reach these endpoints
  (``FacilityTeam.tsx`` already depends on that), and the rule applies to them unchanged. That is
  stricter than the ticket's §12 wording, which speaks of an "ORG_ADMIN action"; the ticket raised
  it as ``HUMAN DECISION REQUIRED`` rather than settling it, and the answer is that **platform
  authority does not silently bypass this invariant.**

  Why it is safe rather than merely strict: granting ``ORG_ADMIN`` is never blocked by this rule, so
  the recovery direction stays open by construction. A platform administrator who must replace an
  organization's only administrator adds the replacement first and then removes the outgoing one --
  one extra step, and the organization is administrable at every instant in between, which a bypass
  would not guarantee. A successful bypass would also be **invisible in the audit trail**: it would
  write an ordinary ``role_removed`` row identical to any other, with nothing recording that a
  lockout had just been permitted.

  **Emergency administrator recovery, if ever required, is an explicit and separately audited
  break-glass capability** (ADR-063 decision 4). It is not built, it is not designed here, and it is
  deliberately **not** an implicit exemption threaded through these four operations.
- **It covers self-removal with no separate rule.** Removing your own ``ORG_ADMIN`` and deactivating
  your own membership are refused when you are the last active administrator, for the same reason
  and through the same code path as doing it to somebody else. There is deliberately no "you may
  never modify yourself" rule: an administrator may drop their own ``CLINICIAN`` role, or hand over
  and then step down, because those do not strand the organization.

── Reactivation restores the roles that are still attached ──

Deactivation changes ``status`` and nothing else; role assignment rows survive it untouched, and
``assign`` refuses an inactive membership. So a reactivated membership comes back holding exactly
the roles it still carries. This is the existing model, **observed rather than chosen here** -- §9
of this ticket is explicit that it must not be silently redesigned. Two consequences are handled
rather than hidden: the API's reactivation response states the roles being restored, and the UI's
confirmation names them before the administrator commits, so "let them back in" is never a blind
act. An administrator who wants somebody back with less authority removes the roles first -- which
is why ``remove_in_session`` deliberately works on an inactive membership.

── What this module does not touch ──

**Keycloak.** Nothing here disables, deletes or modifies a Keycloak account. Organization membership
and global identity are different things, and conflating them would break a real case this network
is built for: a physician who works at two clinics must not lose their sign-in because one of them
let them go. Global identity disablement, if it is ever wanted, is a separate platform capability
with a separate authority model.

**The role taxonomy.** ``ALLOWED_ROLES_BY_ORG_TYPE`` is used exactly as it stands. No role is
invented, and ``PLATFORM_ADMIN`` is not an ``OrganizationRole`` at all -- it is a Keycloak realm
role, so it cannot be granted through this module, and the database's own CHECK constraints refuse
it as a second layer if this one is ever removed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.capabilities import (
    Capability,
    OverrideEffect,
    grantable_capabilities,
    parse_capability,
)
from daher_adapter.exchange.membership import (
    ALLOWED_ROLES_BY_ORG_TYPE,
    MembershipCapabilityRepository,
    MembershipNotFound,
    MembershipRecord,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    OrganizationMembershipStatus,
    OrganizationRole,
    OrganizationStatus,
    OrganizationType,
)
from daher_adapter.exchange.organizations import OrganizationNotFound, OrganizationRepository
from daher_adapter.exchange.security_audit import (
    ExchangeSecurityAuditRepository,
    SecurityEventType,
    SecurityOutcome,
)


class StaffAccessTargetNotFound(RuntimeError):
    """The target membership does not exist, or exists but belongs to a different organization.

    **One exception type for both cases, deliberately.** Telling a caller "this membership exists
    but belongs to another organization" would confirm the existence of a row in a tenant they have
    no claim on, which is the exact disclosure ``exchange/authorization.py``'s ``ResourceNotFound``
    exists to prevent one level up. Maps to HTTP 404."""


class StaffAccessAuthorityLost(RuntimeError):
    """The actor's own administrative authority over this organization is gone as of *now*, inside
    the locked transaction -- their membership was deactivated, their ``ORG_ADMIN`` was removed, or
    the organization itself was deactivated, after the request's first authorization check passed.
    Maps to HTTP 403."""


class LastAdministratorProtected(RuntimeError):
    """The operation would have taken this organization from one or more active ``ORG_ADMIN``
    memberships to zero. Maps to HTTP 409 -- a conflict with the organization's current state, not a
    permission problem: the same actor may well be allowed to do this once a second administrator
    exists."""


class ForbiddenCapability(RuntimeError):
    """A capability was named that does not exist, or that this organization's type may not hold.

    DHEALTH-ACCESS-CAPABILITIES-001. Mirrors `ForbiddenRole` exactly, including the deliberate
    collapsing of "no such capability" and "not valid here" into one answer: distinguishing them
    would let a caller enumerate the catalog's non-grantable members."""


class StaffAccessStateUnchanged(RuntimeError):
    """The membership already held the requested state, or did not hold the role being removed.
    Maps to HTTP 409. Deliberately not a silent success: an administrator who clicked "deactivate"
    on somebody a colleague deactivated a moment ago is owed the difference between "you did this"
    and "this was already so"."""


class ForbiddenRole(RuntimeError):
    """The requested role is not an organization role this organization's type may hold -- including
    the case where it is not an ``OrganizationRole`` value at all (``PLATFORM_ADMIN``, or a typo).
    Maps to HTTP 422."""


@dataclass(frozen=True, slots=True)
class StaffAccessResult:
    """What actually happened, as the transaction committed it.

    ``roles`` is read inside the transaction after the flush, so it is the role set the database
    holds -- never a pre-mutation snapshot, and never the set the caller hoped for."""

    membership: MembershipRecord
    identity_id: uuid.UUID
    roles: frozenset[str]
    #: False only where the operation is an idempotent replay that committed nothing new (a role
    #: already held). Every other no-change case raises `StaffAccessStateUnchanged` instead.
    changed: bool


class StaffAccessService:
    """The four supported staff access lifecycle operations, each one transaction."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        organizations: OrganizationRepository,
        memberships: OrganizationMembershipRepository,
        roles: OrganizationRoleAssignmentRepository,
        capabilities: MembershipCapabilityRepository,
        security_audit: ExchangeSecurityAuditRepository,
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations
        self._memberships = memberships
        self._roles = roles
        self._capabilities = capabilities
        self._audit = security_audit

    # -- member-specific capability overrides ------------------------------------------------

    def set_capability_override(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        capability_key: str,
        effect: OverrideEffect,
    ) -> StaffAccessResult:
        """Grant or deny one capability to one member, on top of their role templates.

        DHEALTH-ACCESS-CAPABILITIES-001 §14/§15. This is what lets an administrator hold the till
        without becoming a cashier, and a cashier lose price configuration without losing their
        job title -- the two acceptance scenarios the ticket names, and the reason the
        combinatorial role explosion of §62 is unnecessary.

        Subject to **every** rule the other staff-access operations are, because it is one of them:
        the organization lock, the actor's live re-authorization against *effective* authority, the
        cross-organization target check, and the last-administrator invariant. That last one is the
        pathway §23 is most concerned with -- denying `staff.access.manage` to the only
        administrator is a self-lockout, and it is refused here exactly as removing their role
        would be.

        `capability_key` is a plain `str`, validated here rather than typed as an enum at the API
        boundary, for the reason `add_role` already records: a FastAPI enum parameter would make
        request parsing reject an invalid value before any DHealth code ran, and the negative
        control that proves this check is load-bearing would have nothing left to break.
        """
        capability = self._require_grantable_capability(
            capability_key, organization_id=organization_id
        )
        with self._sessions.begin() as session:
            target = self._open(
                session,
                principal=principal,
                organization_id=organization_id,
                membership_id=membership_id,
            )
            admins_before = self._count_active_admins(session, organization_id)
            changed = self._capabilities.set_override_in_session(
                session,
                membership_id=target.id,
                capability=capability,
                effect=effect,
                actor_identity_id=principal.identity_id,
            )
            self._guard_last_administrator(session, organization_id, admins_before=admins_before)
            roles = self._roles.list_for_membership_in_session(session, target.id)
            if changed:
                self._record(
                    session,
                    event_type=(
                        "capability_granted"
                        if effect is OverrideEffect.GRANT
                        else "capability_denied"
                    ),
                    principal=principal,
                    organization_id=organization_id,
                    target=target,
                    capability=capability.value,
                    outcome="created",
                )
            return StaffAccessResult(
                membership=target, identity_id=target.identity_id, roles=roles, changed=changed
            )

    def clear_capability_override(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        capability_key: str,
    ) -> StaffAccessResult:
        """Remove one member's override, returning them to what their role templates say.

        Validated with `_require_organization_capability` rather than
        `_require_grantable_capability`: removal asks only *is this a real capability*, never *may
        it be granted at this organization type*. That asymmetry is deliberate and copies
        `remove_role`'s own hard-won rule -- gating removal on grantability created a state the
        product could no longer withdraw from, where a row existed that nothing could delete.

        The last-administrator invariant applies here too, and not only in the obvious direction:
        clearing a **GRANT** of `staff.access.manage` removes authority, and if that member was the
        organization's only remaining administrator the removal is refused.
        """
        capability = self._require_organization_capability(capability_key)
        with self._sessions.begin() as session:
            target = self._open(
                session,
                principal=principal,
                organization_id=organization_id,
                membership_id=membership_id,
            )
            admins_before = self._count_active_admins(session, organization_id)
            changed = self._capabilities.clear_override_in_session(
                session, membership_id=target.id, capability=capability
            )
            self._guard_last_administrator(session, organization_id, admins_before=admins_before)
            roles = self._roles.list_for_membership_in_session(session, target.id)
            if changed:
                self._record(
                    session,
                    event_type="capability_override_cleared",
                    principal=principal,
                    organization_id=organization_id,
                    target=target,
                    capability=capability.value,
                    outcome="updated",
                )
            return StaffAccessResult(
                membership=target, identity_id=target.identity_id, roles=roles, changed=changed
            )

    # -- the four operations ---------------------------------------------------------------------

    def deactivate_membership(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
    ) -> StaffAccessResult:
        """ACTIVE -> INACTIVE. The membership row survives, so every clinical action this person
        performed stays attributable to them; only their authority stops."""
        with self._sessions.begin() as session:
            target = self._open(
                session,
                principal=principal,
                organization_id=organization_id,
                membership_id=membership_id,
            )
            admins_before = self._count_active_admins(session, organization_id)

            record, changed = self._memberships.set_status_in_session(
                session,
                membership_id=target.id,
                status=OrganizationMembershipStatus.INACTIVE,
            )
            if not changed:
                raise StaffAccessStateUnchanged("membership is already inactive")

            self._guard_last_administrator(session, organization_id, admins_before=admins_before)
            roles = self._roles.list_for_membership_in_session(session, target.id)
            self._record(
                session,
                event_type="membership_deactivated",
                principal=principal,
                organization_id=organization_id,
                target=record,
                outcome="updated",
            )
            return StaffAccessResult(
                membership=record, identity_id=record.identity_id, roles=roles, changed=True
            )

    def reactivate_membership(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
    ) -> StaffAccessResult:
        """INACTIVE -> ACTIVE, on the *same* row -- never a second membership for the same person,
        which would fork their audit history in two.

        The returned ``roles`` are the roles this membership still carries and is therefore getting
        back (see this module's docstring). No last-administrator check: reactivation can only ever
        increase the number of active administrators."""
        with self._sessions.begin() as session:
            target = self._open(
                session,
                principal=principal,
                organization_id=organization_id,
                membership_id=membership_id,
            )

            record, changed = self._memberships.set_status_in_session(
                session,
                membership_id=target.id,
                status=OrganizationMembershipStatus.ACTIVE,
            )
            if not changed:
                raise StaffAccessStateUnchanged("membership is already active")

            roles = self._roles.list_for_membership_in_session(session, target.id)
            self._record(
                session,
                event_type="membership_reactivated",
                principal=principal,
                organization_id=organization_id,
                target=record,
                outcome="updated",
            )
            return StaffAccessResult(
                membership=record, identity_id=record.identity_id, roles=roles, changed=True
            )

    def add_role(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        role_name: str,
    ) -> StaffAccessResult:
        """Grant one existing organization role. Idempotent: re-granting a held role commits nothing
        and reports ``changed=False``, matching ``assign``'s own insert-first-wins contract rather
        than inventing a conflict for a request whose intent is already satisfied.

        ``role_name`` is a plain ``str`` and is validated here, against this organization's own
        type. That is deliberate and mirrors the reasoning ``OrganizationRepository.create`` already
        records
        for ``org_type``: if the parameter were a FastAPI enum, a forbidden value such as
        ``PLATFORM_ADMIN`` would be rejected by request parsing before any DHealth code ran, and the
        negative control that proves this check is load-bearing would have nothing to break."""
        role = self._require_allowed_role(role_name, organization_id=organization_id)
        with self._sessions.begin() as session:
            target = self._open(
                session,
                principal=principal,
                organization_id=organization_id,
                membership_id=membership_id,
            )
            # `assign` re-checks the org-type matrix itself by joining through to the organization,
            # and refuses an inactive membership with `MembershipInactive` -- neither is duplicated
            # here. The repository stays the enforcement point; this call site adds the actor,
            # organization-boundary and audit concerns it cannot know about.
            _, created = self._roles.assign(membership_id=target.id, role=role, session=session)
            roles = self._roles.list_for_membership_in_session(session, target.id)
            self._record(
                session,
                event_type="role_assigned",
                principal=principal,
                organization_id=organization_id,
                target=target,
                role=role.value,
                outcome="created" if created else "returned_existing",
            )
            return StaffAccessResult(
                membership=target, identity_id=target.identity_id, roles=roles, changed=created
            )

    def remove_role(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        role_name: str,
    ) -> StaffAccessResult:
        """Remove one organization role. This is the operation that closes DHEALTH-NATIVE-SEC-001's
        P1 most directly: removing ``CLINICIAN`` or ``LAB_RESULT_RELEASER`` makes the next request
        from an already-signed-in browser lose that authority, with no logout and no token refresh,
        because every ``require_*`` reads the role set from the database on every request.

        **Removal deliberately does NOT check the organization-type matrix, and that is a fix rather
        than an omission.** It used to. The consequence was found by this ticket's own NC-ACCESS-5:
        that control widened `ALLOWED_ROLES_BY_ORG_TYPE` so a provider could hold `LAB_TECH`, a real
        `LAB_TECH` row was written at a provider, and then reverting the control made that row
        **unremovable through the product** -- `_require_allowed_role` answered 422 before the
        endpoint ever looked at the membership. A role that exists and cannot be withdrawn is the
        exact shape of the P1 this ticket was written to close, reintroduced in miniature.

        So removal validates only that the value names a real `OrganizationRole`. `PLATFORM_ADMIN`,
        a typo and an invented role are still refused, because none of them can name a row that
        exists. But any of the four real roles is removable whenever a membership actually holds it,
        whatever the organization's type now permits -- removal only ever *reduces* authority, so
        refusing it protects nothing and strands the row instead.

        `ALLOWED_ROLES_BY_ORG_TYPE` therefore governs what may be **granted**, which is where a
        matrix belongs; it never governs what may be taken away."""
        role = self._require_organization_role(role_name)
        with self._sessions.begin() as session:
            target = self._open(
                session,
                principal=principal,
                organization_id=organization_id,
                membership_id=membership_id,
            )
            admins_before = self._count_active_admins(session, organization_id)

            removed = self._roles.remove_in_session(session, membership_id=target.id, role=role)
            if not removed:
                raise StaffAccessStateUnchanged(f"membership does not hold role {role.value!r}")

            self._guard_last_administrator(session, organization_id, admins_before=admins_before)
            roles = self._roles.list_for_membership_in_session(session, target.id)
            self._record(
                session,
                event_type="role_removed",
                principal=principal,
                organization_id=organization_id,
                target=target,
                role=role.value,
                outcome="updated",
            )
            return StaffAccessResult(
                membership=target, identity_id=target.identity_id, roles=roles, changed=True
            )

    # -- the shared transaction preamble ----------------------------------------------------------

    def _open(
        self,
        session: Session,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
    ) -> MembershipRecord:
        """Steps 1-3 of every one of the four operations: lock, re-authorize, resolve the target.

        Shared rather than repeated so the ordering cannot drift between endpoints, and so a future
        fifth operation cannot accidentally be written without one of them."""
        try:
            organization = self._organizations.lock_in_session(session, organization_id)
        except OrganizationNotFound as error:
            # Reachable only if the organization was deleted between the HTTP-layer authorization
            # check and this lock. Reported as a missing target, same as any other unreachable
            # organization, rather than as a server error.
            raise StaffAccessTargetNotFound(str(organization_id)) from error

        self._require_current_admin_authority(
            session,
            principal=principal,
            organization_id=organization_id,
            organization_status=organization.status,
        )

        try:
            target = self._memberships.get_in_session(session, membership_id)
        except MembershipNotFound as error:
            raise StaffAccessTargetNotFound(str(membership_id)) from error
        if target.organization_id != organization_id:
            # A real membership in somebody else's organization. Indistinguishable from a
            # nonexistent one, on purpose -- see `StaffAccessTargetNotFound`.
            raise StaffAccessTargetNotFound(str(membership_id))
        return target

    def _require_current_admin_authority(
        self,
        session: Session,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        organization_status: str,
    ) -> None:
        """The second authorization check, and the reason it exists at all.

        A platform administrator is exempt from the membership re-read because they have no
        membership row to re-read -- their authority is a Keycloak realm role carried by the token,
        and ``require_membership`` returns them a context with ``membership=None``. They are also
        exempt from the organization-ACTIVE check, preserving DH-EX-002's recorded decision that a
        platform admin may still manage a deactivated organization.

        For everybody else this is a live re-read behind the organization lock. It is what makes §14
        true under concurrency: an administrator deactivated a moment ago cannot finish
        administering
        with the request they had already begun."""
        if principal.platform_admin:
            return
        if organization_status != OrganizationStatus.ACTIVE.value:
            raise StaffAccessAuthorityLost("organization is inactive")
        actor = self._memberships.find_active_in_session(
            session, organization_id=organization_id, identity_id=principal.identity_id
        )
        if actor is None:
            raise StaffAccessAuthorityLost("actor has no active membership in this organization")
        # DHEALTH-ACCESS-CAPABILITIES-001 §22: **effective** authority, not the role name.
        #
        # The role check this replaces asked whether the actor still held `ORG_ADMIN`. An
        # administrator who kept the role and had `staff.access.manage` denied individually passed
        # it and went on administering -- authority the access model says they do not have.
        organization = self._organizations.get(organization_id)
        held = self._capabilities.effective_for_membership_in_session(
            session,
            membership_id=actor.id,
            membership_status=actor.status,
            organization_type=organization.org_type,
            organization_status=organization_status,
        )
        if Capability.STAFF_ACCESS_MANAGE not in held:
            raise StaffAccessAuthorityLost(
                "actor no longer holds staff access authority in this organization"
            )

    # -- the invariant ----------------------------------------------------------------------------

    def _count_active_admins(self, session: Session, organization_id: uuid.UUID) -> int:
        """How many people can still administer this organization's access, **effectively**.

        DHEALTH-ACCESS-CAPABILITIES-001 §22 rebuilt this around the capability rather than the
        role, and the difference is the whole point: the old count asked who still held a role
        *name*, so every pathway that removed authority without removing the role -- an individual
        deny, a direct grant withdrawn -- walked straight past the last-administrator invariant and
        left an organization nobody could administer.

        One SQL statement inside the caller's transaction, under the organization lock `_open`
        already took. See `MembershipCapabilityRepository.count_active_with_capability_in_session`
        for why a Python loop over members is the version that races."""
        return self._capabilities.count_active_with_capability_in_session(
            session,
            organization_id=organization_id,
            capability=Capability.STAFF_ACCESS_MANAGE,
        )

    def _guard_last_administrator(
        self, session: Session, organization_id: uuid.UUID, *, admins_before: int
    ) -> None:
        """Refuse an operation that *took* this organization to zero active administrators.

        Called after the mutation's flush, so the "after" count is what would actually commit.
        ``admins_before > 0`` is the transition condition: an organization that already had none is
        not made unfixable by this rule (see the module docstring)."""
        if admins_before == 0:
            return
        if self._count_active_admins(session, organization_id) == 0:
            raise LastAdministratorProtected(
                "this would leave the organization with no active administrator"
            )

    # -- helpers ----------------------------------------------------------------------------------

    def _require_organization_role(self, role_name: str) -> OrganizationRole:
        """Is this value one of the four real organization roles at all?

        The gate that refuses ``PLATFORM_ADMIN``, a typo and an invented role. Deliberately separate
        from the org-type matrix below, because the two questions are not the same and are needed at
        different moments: **removal** asks only this one (see ``remove_role``), **granting** asks
        both.

        ``PLATFORM_ADMIN`` fails here because it is not an ``OrganizationRole`` member -- it is a
        Keycloak realm role, so no organization administrator can manufacture platform authority
        through this module. That is structural rather than a list this code consults, which is
        exactly why NC-ACCESS-5 could not make it succeed without inventing an enum member."""
        try:
            return OrganizationRole(role_name)
        except ValueError as error:
            raise ForbiddenRole(f"{role_name!r} is not an organization role") from error

    def _require_allowed_role(
        self, role_name: str, *, organization_id: uuid.UUID
    ) -> OrganizationRole:
        """The gate for **granting**: a real organization role, *and* one this organization's type
        may hold.

        Two refusals collapse into one ``ForbiddenRole``: a value that is not an
        ``OrganizationRole`` at all (``PLATFORM_ADMIN``, a typo, an invented role) and a real
        organization role this organization's *type* may not hold (``LAB_TECH`` at a provider). Both
        are the same answer to the caller -- this role cannot be granted here -- and distinguishing
        them would tell a caller which non-organization role names exist.

        Deliberately **not** used by ``remove_role``: see that method's own docstring for the trap
        that created (a real role row the product could no longer withdraw)."""
        role = self._require_organization_role(role_name)
        try:
            organization = self._organizations.get(organization_id)
        except OrganizationNotFound as error:
            raise StaffAccessTargetNotFound(str(organization_id)) from error
        allowed = ALLOWED_ROLES_BY_ORG_TYPE[OrganizationType(organization.org_type)]
        if role not in allowed:
            raise ForbiddenRole(
                f"role {role.value!r} is not valid for a {organization.org_type!r} organization"
            )
        return role

    def _record(
        self,
        session: Session,
        *,
        event_type: SecurityEventType,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        target: MembershipRecord,
        outcome: SecurityOutcome,
        role: str | None = None,
        capability: str | None = None,
    ) -> None:
        self._audit.record(
            session,
            event_type=event_type,
            actor_identity_id=principal.identity_id,
            organization_id=organization_id,
            subject_identity_id=target.identity_id,
            membership_id=target.id,
            role=role,
            capability=capability,
            outcome=outcome,
        )

    def _require_organization_capability(self, capability_key: str) -> Capability:
        """Is this value a real DHealth capability at all?

        The gate that makes `POST .../capabilities/{anything}` safe (§53). A facility cannot invent
        `clinic.custom_permission_239`, and it cannot name platform authority either -- not because
        a list excludes `PLATFORM_ADMIN`, but because `Capability` has no such member, exactly as
        `_require_organization_role` refuses it by not being an `OrganizationRole`. That is
        structural, which is why NC-CAP-5 has to add an enum member before it can even attempt the
        escalation."""
        try:
            return parse_capability(capability_key)
        except ValueError as error:
            raise ForbiddenCapability(f"{capability_key!r} is not a DHealth capability") from error

    def _require_grantable_capability(
        self, capability_key: str, *, organization_id: uuid.UUID
    ) -> Capability:
        """The gate for **granting or denying**: a real capability, *and* one this organization's
        type may hold at all.

        Two refusals collapse into one `ForbiddenCapability`, exactly as `_require_allowed_role`
        collapses its two: a value that is not a capability, and a real capability a laboratory
        cannot hold (`patient.directory.read` -- a laboratory keeps no patient directory, D-6 is
        open). Both are the same answer to the caller, and distinguishing them would tell them
        which non-grantable capability keys exist."""
        capability = self._require_organization_capability(capability_key)
        try:
            organization = self._organizations.get(organization_id)
        except OrganizationNotFound as error:
            raise StaffAccessTargetNotFound(str(organization_id)) from error
        if capability not in grantable_capabilities(organization.org_type):
            raise ForbiddenCapability(
                f"capability {capability.value!r} is not valid for a "
                f"{organization.org_type!r} organization"
            )
        return capability
