"""DH-EX-002: the Exchange's explicit authorization layer.

Deliberately the only place organization-scoped authorization decisions are made -- API routes
call `require_*` methods here rather than comparing roles inline, so the anti-enumeration and
role-matrix rules exist in exactly one place, are independently testable, and cannot silently
diverge between endpoints.

Two exception types, mapped to two different HTTP statuses at the API layer, on purpose:

- `ResourceNotFound` (404): the identity has *no relationship at all* to the organization -- an
  org that does not exist and an org the caller is not a member of are indistinguishable
  responses, by design (DH-EX-002's anti-enumeration policy: never disclose a cross-tenant
  resource's existence to someone with no claim to it).
- `NotAuthorized` (403): the identity *does* legitimately belong to the organization (or is the
  platform admin) but lacks the specific permission being checked (e.g. a CLINICIAN calling an
  ORG_ADMIN-only action, or any member of a legitimately-INACTIVE organization).

Platform admin bypasses both checks entirely (per DH-EX-002: "platform admin may still inspect/
manage" an organization, including an inactive one) but still gets `ResourceNotFound` for an
organization that genuinely does not exist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from daher_adapter.exchange.capabilities import Capability
from daher_adapter.exchange.membership import (
    MembershipCapabilityRepository,
    MembershipRecord,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import OrganizationRole, OrganizationStatus
from daher_adapter.exchange.organizations import (
    OrganizationNotFound,
    OrganizationRecord,
    OrganizationRepository,
)


class NotAuthorized(RuntimeError):
    """The identity has a legitimate relationship to the organization but lacks the specific
    permission required. Maps to HTTP 403."""


class ResourceNotFound(RuntimeError):
    """The identity has no relationship to this organization -- deliberately indistinguishable
    from the organization not existing at all. Maps to HTTP 404."""


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    identity_id: uuid.UUID
    issuer: str
    subject: str
    platform_admin: bool


@dataclass(frozen=True, slots=True)
class OrganizationAccessContext:
    organization: OrganizationRecord
    membership: MembershipRecord | None
    via_platform_admin: bool


class AuthorizationService:
    def __init__(
        self,
        organizations: OrganizationRepository,
        memberships: OrganizationMembershipRepository,
        roles: OrganizationRoleAssignmentRepository,
        capabilities: MembershipCapabilityRepository,
    ) -> None:
        self._organizations = organizations
        self._memberships = memberships
        self._roles = roles
        self._capabilities = capabilities

    # -- the one gate ----------------------------------------------------------------------------

    def _effective(
        self, organization: OrganizationRecord, membership: MembershipRecord
    ) -> frozenset[Capability]:
        """This membership's effective capabilities. The single question every gate below asks.

        DHEALTH-ACCESS-CAPABILITIES-001 §16: one resolver, used by everything. Billing does not
        compute capabilities, the UI does not compute capabilities, Team & Access does not compute
        capabilities -- they all end up here, which is what stops two answers to one question
        existing."""
        return self._capabilities.effective_for_membership(
            membership_id=membership.id,
            membership_status=membership.status,
            organization_type=organization.org_type,
            organization_status=organization.status,
        )

    def _require(
        self,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        capabilities: tuple[Capability, ...],
        refusal: str,
        *,
        allow_platform_admin: bool = False,
    ) -> OrganizationAccessContext:
        """Require **any one** of ``capabilities`` in this organization.

        The shared body of every gate in this module, extracted because the five role gates it
        replaces were five near-identical copies of it and the copies are where a rewrite loses a
        line. Its behaviour is theirs, preserved exactly:

        * **no ``platform_admin`` bypass.** A network administrator is not a clinician, a
          laboratory worker or a cashier (ADR-063). This is deliberately stricter than
          ``require_membership``, which does let one through.
        * an inactive organization is refused;
        * a caller with *no* relationship to a real organization gets ``ResourceNotFound``
          (anti-enumeration), while a platform admin gets ``NotAuthorized`` for a real organization
          they lack the capability in -- they can already see it exists, so a 404 would be an
          inconsistent lie rather than protection;
        * a genuinely nonexistent organization is ``ResourceNotFound`` for everyone.

        ``refusal`` is the message the old role gate used, carried over verbatim. The wording is
        not cosmetic: existing tests and a browser qualification assert on it, and changing it
        would be an undocumented behaviour change hiding inside a refactor.

        ``allow_platform_admin`` exists because the gates this replaces did **not** all agree, and
        §18 forbids quietly making them agree. The clinical and financial gates have never let a
        platform administrator through (ADR-063); the *administrative* ones always have, because
        `require_org_admin` was built on `require_role`, which returns `require_membership`'s
        `via_platform_admin` context early. DH-EX-002 relies on that: a platform administrator
        manages a facility's staff and may do so even for a deactivated organization. So the flag
        is off by default -- the safe direction, and the one every clinical caller wants -- and set
        explicitly by the two administrative gates, where it is now visible instead of being an
        emergent property of a helper three calls away.
        """
        organization = self._get_organization_or_404(organization_id)
        if allow_platform_admin and principal.platform_admin:
            # No organization-ACTIVE check on this path, matching `require_membership`'s
            # long-standing behaviour: DH-EX-002 records that a platform administrator may still
            # administer a deactivated organization, which is how one gets reactivated.
            return OrganizationAccessContext(
                organization=organization, membership=None, via_platform_admin=True
            )
        if organization.status != OrganizationStatus.ACTIVE.value:
            raise NotAuthorized("organization is inactive")

        membership = self._memberships.find_active(
            organization_id=organization_id, identity_id=principal.identity_id
        )
        if membership is None:
            if principal.platform_admin:
                raise NotAuthorized(refusal)
            raise ResourceNotFound(f"organization {organization_id} is not accessible")

        held = self._effective(organization, membership)
        if not any(capability in held for capability in capabilities):
            raise NotAuthorized(refusal)

        return OrganizationAccessContext(
            organization=organization, membership=membership, via_platform_admin=False
        )

    def require_platform_admin(self, principal: AuthenticatedPrincipal) -> None:
        if not principal.platform_admin:
            raise NotAuthorized("platform admin required")

    def require_membership(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        if principal.platform_admin:
            organization = self._get_organization_or_404(organization_id)
            return OrganizationAccessContext(
                organization=organization, membership=None, via_platform_admin=True
            )

        membership = self._memberships.find_active(
            organization_id=organization_id, identity_id=principal.identity_id
        )
        if membership is None:
            raise ResourceNotFound(f"organization {organization_id} is not accessible")

        # A membership row can only exist for an organization that exists (FK), so this lookup
        # cannot itself 404 here -- but it can still find the organization INACTIVE, which is a
        # 403 (the caller legitimately belongs; the organization is disabled), never a 404.
        organization = self._organizations.get(organization_id)
        if organization.status != OrganizationStatus.ACTIVE.value:
            raise NotAuthorized("organization is inactive")

        return OrganizationAccessContext(
            organization=organization, membership=membership, via_platform_admin=False
        )

    def require_role(
        self,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        role: OrganizationRole,
    ) -> OrganizationAccessContext:
        context = self.require_membership(principal, organization_id)
        if context.via_platform_admin:
            return context
        if context.membership is None:
            raise RuntimeError(
                "require_membership returned a non-platform-admin context with no membership"
            )
        held_roles = self._roles.list_for_membership(context.membership.id)
        if role.value not in held_roles:
            raise NotAuthorized(f"role {role.value!r} required")
        return context

    def require_org_admin(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        """Administering this organization's **people**: memberships, roles, capabilities.

        DHEALTH-ACCESS-CAPABILITIES-001 §17: the name survives as a domain helper because every
        call site reads better for it, but the authority is `staff.access.manage` and no longer the
        `ORG_ADMIN` role name. That distinction is the whole of §22 -- an administrator who keeps
        the role and is individually denied this capability can no longer administer, and the
        last-administrator invariant counts *this*, not the role.

        Deliberately **not** the same capability as `require_facility_configure` below, even though
        `ORG_ADMIN` carries both today. Managing who works here and configuring what the facility
        offers are different jobs, and splitting them is what lets a facility eventually hand one
        to somebody without the other.

        One behavioural change from the `require_role` implementation this replaces, and it is a
        fix rather than drift: `require_role` returned early for a platform administrator, letting
        one manage staff at any organization. That is preserved where it was actually relied upon
        (`api.py` passes platform administrators through its own `_require_org_admin` wrapper), and
        the wrapper is where that decision now lives visibly instead of being buried in a shared
        helper."""
        return self._require(
            principal,
            organization_id,
            (Capability.STAFF_ACCESS_MANAGE,),
            "organization administrator required",
            allow_platform_admin=True,
        )

    def require_facility_configure(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        """Configuring what the facility **offers**: catalogue offerings, result templates, the
        public directory profile.

        Split out of `require_org_admin` by DHEALTH-ACCESS-CAPABILITIES-001. `ORG_ADMIN` carries
        both capabilities, so nobody's authority changed on the day of the split (§18) -- what
        changed is that the two can now be separated, which they could not be while both were the
        single word `ORG_ADMIN`."""
        return self._require(
            principal,
            organization_id,
            (Capability.FACILITY_CONFIGURE,),
            "organization administrator required",
            allow_platform_admin=True,
        )

    def require_clinician(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        """DH-EX-004: the gate for clinical lab-order creation/read -- deliberately does **not**
        let `platform_admin` bypass, unlike every other `require_*` method in this class. Network
        administration is not clinical access: a platform admin needs their own ACTIVE `CLINICIAN`
        membership in this specific provider organization to create or read its orders, exactly
        like anyone else. `ORG_ADMIN` alone (without also holding `CLINICIAN`) is refused too --
        organization administration and clinical ordering are separate authorities here, matching
        the ticket's explicit "a platform administrator is a network administrator, not
        automatically a clinician" instruction, generalized to org-admins of the provider itself.

        A genuinely nonexistent organization still 404s for everyone, platform admin included --
        that check happens before any membership/role lookup and never depends on `platform_admin`.
        For a normal (non-platform-admin) caller with *zero* relationship to the organization, this
        also 404s (anti-enumeration, matching `require_membership`'s convention: no claim at all on
        an org is indistinguishable from that org not existing). For a platform admin specifically,
        a real organization they lack `CLINICIAN` membership in is a 403, not a 404 -- a platform
        admin already has visibility that the organization exists (they can list/create/manage
        organizations), so pretending otherwise here would be an inconsistent lie, not genuine
        anti-enumeration."""
        # DHEALTH-ACCESS-CAPABILITIES-001 §17: the name and the reasoning above are unchanged;
        # the *authority* is now any of the clinical capabilities, which a role
        # template happens to carry. A member granted it directly passes here without holding
        # the role, and a member denied it individually is refused while still holding the role
        # -- neither of which the role lookup this replaced could express.
        #
        # **Narrowed by DHEALTH-FRONT-DESK-001/ADR-069 from six capabilities to four.**
        # `PATIENT_DIRECTORY_READ` and `PATIENT_REGISTER` were in this tuple because the single
        # `require_clinician` gate they came from guarded the patient directory too. That made the
        # any-of question "do you hold *any* provider capability at all", and it is the reason a
        # reception member -- who legitimately holds the two patient capabilities -- would
        # otherwise pass every gate in this list: order creation, result review, the whole visit
        # surface. The patient-directory and registration endpoints ask for their own capabilities
        # directly instead (`api.py`), so nothing lost a gate; what changed is that holding the
        # *patient* authorities no longer implies the *clinical* ones.
        #
        # Nobody holding a role template is affected: `CLINICIAN` carries all of these and more.
        # The members whose authority narrows are exactly those granted one patient capability
        # directly and no clinical one -- the composition ADR-069 exists to make possible.
        return self._require(
            principal,
            organization_id,
            (
                Capability.VISIT_READ,
                Capability.VISIT_DOCUMENT,
                Capability.ORDER_MANAGE,
                Capability.RESULT_REVIEW,
            ),
            "clinician role required",
        )

    def require_capability(
        self,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        *capabilities: Capability,
    ) -> OrganizationAccessContext:
        """Require **any one** of these capabilities in this organization.

        DHEALTH-BILLING-001/ADR-066. This is the gate billing uses, and it asks a different question
        from every other ``require_*`` method here: not *"is this person a ``BILLING_CLERK``"* but
        *"may this person operate billing here"*. ``exchange/capabilities.py`` is the single place
        that turns the second question into the first.

        **That promise was kept.** DHEALTH-ACCESS-CAPABILITIES-001 backed capabilities with
        per-member grants and denies, and no billing endpoint, service or behavioural test changed
        to make it happen -- which is the acceptance criterion the seam existed for. This method is
        now the public face of the shared gate every other ``require_*`` here also uses.

        ``BILLING_CLERK`` is consequently a **convenient bundle rather than a gate**: it is the role
        a facility grants somebody whose job is the till, and it happens to be the only role
        carrying
        ``BILLING_OPERATE`` today. Nothing downstream knows that.

        Otherwise this behaves exactly like ``require_clinician`` and ``require_lab_tech``, and the
        sameness is deliberate:

        * **no ``platform_admin`` bypass.** A network administrator is not a cashier. Note that this
          is stricter than ``require_membership``, which does let one through -- falling back to
          that
          would hand every facility's money to anyone holding the realm role, exactly the silent
          widening ADR-063 forbids.
        * an inactive organization is refused;
        * a caller with *no* relationship to a real organization gets ``ResourceNotFound``
          (anti-enumeration), while a platform admin gets ``NotAuthorized`` for a real organization
          they lack the capability in -- they can already see that it exists, so a 404 would be an
          inconsistent lie rather than protection;
        * a genuinely nonexistent organization is ``ResourceNotFound`` for everyone.
        """
        if not capabilities:  # pragma: no cover - a programming error, not a runtime state
            raise ValueError("require_capability needs at least one capability")

        wanted = ", ".join(sorted(capability.value for capability in capabilities))
        return self._require(principal, organization_id, capabilities, f"{wanted} required")

    def require_lab_tech(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        """DH-EX-006: the gate for selected-lab assigned-order workflow (list/detail/accept/
        reject/start-processing) -- deliberately does **not** let `platform_admin` bypass, the
        same reasoning `require_clinician` already established: network administration is not
        clinical authority, here specifically not clinical *laboratory processing* authority. An
        `ORG_ADMIN` of the lab organization who does not also hold `LAB_TECH` is refused too --
        organization administration and lab clinical work are separate authorities, exactly
        mirroring `require_clinician`'s provider-side "ORG_ADMIN alone is not CLINICIAN" rule.

        A genuinely nonexistent organization still 404s for everyone, platform admin included.
        For a normal (non-platform-admin) caller with *zero* relationship to the organization,
        this also 404s (anti-enumeration, matching `require_membership`'s convention). For a
        platform admin specifically, a real organization they lack `LAB_TECH` membership in is a
        403, not a 404 -- a platform admin already has visibility that the organization exists."""
        # DHEALTH-ACCESS-CAPABILITIES-001 §17: the name and the reasoning above are unchanged;
        # the *authority* is now `lab.queue.work`, which a role template happens to
        # carry. A member granted it directly passes here without holding the role, and a member
        # denied it individually is refused while still holding the role -- neither of which the
        # role lookup this replaced could express.
        return self._require(
            principal,
            organization_id,
            (Capability.LAB_QUEUE_WORK,),
            "lab technician role required",
        )

    def require_lab_result_releaser(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        """DH-EX-012/ADR-044: the gate for explicit order-level result release --
        deliberately does **not** let `platform_admin` bypass, the same reasoning
        `require_lab_tech`/`require_clinician` already establish: network administration is not
        clinical laboratory authority, here specifically not release authority. An `ORG_ADMIN` of
        the lab organization who does not also hold `LAB_RESULT_RELEASER` is refused too --
        ADR-044 is explicit that administrative authority never implies release authority. A
        `LAB_TECH` who does not also hold `LAB_RESULT_RELEASER` is refused as well -- entry and
        release are separate authorities that the same human may hold together, but neither one
        implies the other (ADR-044's "no forced second-person, no automatic same-role grant"
        binding decision).

        Anti-enumeration matches `require_lab_tech` exactly: a genuinely nonexistent organization
        404s for everyone; a normal caller with zero relationship to a real organization also
        404s; a platform admin (who already has visibility that the organization exists) gets 403
        for a real organization they lack this role in, never a 404."""
        # DHEALTH-ACCESS-CAPABILITIES-001 §17: the name and the reasoning above are unchanged;
        # the *authority* is now `lab.result.release`, which a role template happens to
        # carry. A member granted it directly passes here without holding the role, and a member
        # denied it individually is refused while still holding the role -- neither of which the
        # role lookup this replaced could express.
        return self._require(
            principal,
            organization_id,
            (Capability.LAB_RESULT_RELEASE,),
            "lab result releaser role required",
        )

    def require_lab_tech_or_result_releaser(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        """DH-EX-012: the gate for the lab's own pre-release result-inspection read -- a releaser
        must be able to see what they are about to release, and the tech who entered the results
        must be able to see their own work, so either role suffices for a read-only inspection.
        Same anti-enumeration and no-platform-admin-bypass discipline as `require_lab_tech`/
        `require_lab_result_releaser`, applied to an "either role" check instead of one role."""
        # DHEALTH-ACCESS-CAPABILITIES-001 §17: the name and the reasoning above are unchanged;
        # the *authority* is now either laboratory capability, which a role template happens to
        # carry. A member granted it directly passes here without holding the role, and a member
        # denied it individually is refused while still holding the role -- neither of which the
        # role lookup this replaced could express.
        return self._require(
            principal,
            organization_id,
            (
                Capability.LAB_QUEUE_WORK,
                Capability.LAB_RESULT_RELEASE,
            ),
            "lab technician or lab result releaser role required",
        )

    def require_any_active_membership(self, principal: AuthenticatedPrincipal) -> None:
        """DH-EX-003: the gate for network-wide reads (catalog list, lab directory, the
        marketplace whole-order query) that are not scoped to any single organization the caller
        already belongs to -- unlike every other `require_*` method here, which checks a specific
        `organization_id`. Platform admin always passes. Otherwise the caller must hold at least
        one ACTIVE membership in an ACTIVE organization; a freshly-bootstrapped identity with zero
        memberships (see `exchange/api.py`'s `get_principal`) may still call `/me`, but may not
        browse the network.

        DH-EX-003R correction: this originally checked membership status only, deliberately
        *not* the membership's own organization status ("an org's own active/inactive status only
        matters for org-scoped actions"). That reasoning does not hold for this specific check --
        an organization is deactivated precisely to cut off its access, and network-wide reads are
        access. An identity whose only membership is in a deactivated organization must not retain
        a standing ability to browse the catalog/directory/marketplace indefinitely through this
        non-org-scoped path; it is not "org-scoped" in name, but it is still access the deactivated
        organization's membership should no longer grant."""
        if principal.platform_admin:
            return
        memberships = self._memberships.list_for_identity(principal.identity_id)
        for membership in memberships:
            if membership.status != "ACTIVE":
                continue
            try:
                organization = self._organizations.get(membership.organization_id)
            except OrganizationNotFound:
                continue
            if organization.status == OrganizationStatus.ACTIVE.value:
                return
        raise NotAuthorized("at least one active membership in an active organization is required")

    def _get_organization_or_404(self, organization_id: uuid.UUID) -> OrganizationRecord:
        try:
            return self._organizations.get(organization_id)
        except OrganizationNotFound as error:
            raise ResourceNotFound(f"organization {organization_id} is not accessible") from error
