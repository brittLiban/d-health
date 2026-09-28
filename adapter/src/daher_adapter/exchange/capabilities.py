"""DHEALTH-BILLING-001/ADR-066: what a person may **do**, as distinct from what they **are**.

── Why this module exists ──

A role is a job title. A capability is a permission. DHealth has always conflated them --
``require_clinician`` asks "are you a CLINICIAN", and every clinical call site therefore hard-codes
a role name. That was fine while roles and permissions were one-to-one, and it stops being fine the
moment somebody legitimately needs *part* of another job's authority: a front-desk employee who also
takes payment, an administrator covering the till on a Saturday, a clinician at a one-person clinic
who is inevitably also the cashier.

So billing asks a different question. Not *"is this person a ``BILLING_CLERK``"* but *"may this
person operate billing here"* -- and this module is the single place that turns the second question
into the first.

``BILLING_CLERK`` is therefore a **convenient bundle, not a gate**. It is the role a facility grants
to somebody whose job is the till, and it is the only role that currently carries
``BILLING_OPERATE``. Nothing in ``exchange/billing.py`` or in any billing endpoint knows that: they
ask for the capability. When a future ticket lets a facility grant ``BILLING_OPERATE`` to a Front
Desk role, to ``ORG_ADMIN``, or to one named individual, **only the resolution below changes** -- no
service, no endpoint, and no test of billing behaviour is rewritten.

── What this module deliberately is *not* ──

It is not a permission system. There is no grant table, no revocation, no per-user override, no
inheritance, no effective-permission computation and no UI for any of that. Those are
**`DHEALTH-ACCESS-CAPABILITIES-001`** -- *Organization Roles, Permission Grants and
Capability-Driven UI* -- which will define role templates, granular grants and revocations,
protected capabilities that may never be delegated, effective-permission calculation, and
navigation/action visibility driven by the result. Building any of that here would have been a
second, worse version of that ticket.

What exists here is one enum, one mapping, and one question. That is the whole seam.

── Why only billing uses it today ──

The clinical gates (``require_clinician``, ``require_lab_tech``, ``require_lab_result_releaser``)
are deliberately **not** migrated by this ticket. Each carries hard-won ADR history about who may do
clinical work -- ADR-044's separation of result entry from release most of all -- and rewriting them
in passing, inside a billing ticket, would put that history at risk for no benefit billing needs.
`DHEALTH-ACCESS-CAPABILITIES-001` is where that migration belongs, and this module is the shape it
should follow.
"""

from __future__ import annotations

from enum import StrEnum

from daher_adapter.exchange.models import OrganizationRole


class OrganizationCapability(StrEnum):
    """One thing a person may do inside one organization.

    Deliberately two, both financial. A capability is added when a real call site needs to ask a
    permission question that a role name cannot answer honestly -- never speculatively, and never
    one-per-endpoint.
    """

    #: Create invoices, record payments, read balances, issue receipts. The money itself.
    BILLING_OPERATE = "BILLING_OPERATE"

    #: Decide what the facility charges: create services, set and change prices.
    #:
    #: Separate from ``BILLING_OPERATE`` on purpose, and the separation is a financial control
    #: rather than tidiness: a person who can quietly change a price *and* take the payment is the
    #: oldest problem in facility billing. `docs/NATIVE-OPERATING-PRODUCT.md` §35's own ticket brief
    #: assigns prices to the administrator and the till to the cashier, and this keeps them apart
    #: even once both become configurable.
    BILLING_CONFIGURE_PRICES = "BILLING_CONFIGURE_PRICES"


#: **The single seam.** Which roles currently carry which capability.
#:
#: This mapping is the whole of today's "permission system", and replacing it with configurable
#: per-organization grants is precisely `DHEALTH-ACCESS-CAPABILITIES-001`'s job. Because every
#: billing call site asks for a *capability*, that replacement touches this dictionary and the
#: function below it -- and nothing else.
#:
#: Note what is **not** here: `BILLING_OPERATE` is not granted to `ORG_ADMIN`. Administering a
#: facility is not the same as handling its money, and ADR-066 records that an administrator must be
#: granted the till explicitly rather than inheriting it. A facility whose administrator does cover
#: the till grants them `BILLING_CLERK` too -- one click in `Équipe et accès`, and visible in the
#: audit trail, which "ORG_ADMIN implies everything" would not be.
CAPABILITY_ROLE_GRANTS: dict[OrganizationCapability, frozenset[OrganizationRole]] = {
    OrganizationCapability.BILLING_OPERATE: frozenset({OrganizationRole.BILLING_CLERK}),
    OrganizationCapability.BILLING_CONFIGURE_PRICES: frozenset({OrganizationRole.ORG_ADMIN}),
}


def roles_granting(capability: OrganizationCapability) -> frozenset[OrganizationRole]:
    """The roles that currently carry this capability.

    A function rather than direct dictionary access at call sites, so that when grants become
    configurable the signature can grow an ``organization_id`` without every caller changing shape.
    """
    return CAPABILITY_ROLE_GRANTS[capability]


def capability_is_held(
    capability: OrganizationCapability, held_roles: frozenset[str] | set[str]
) -> bool:
    """Does somebody holding exactly ``held_roles`` have this capability here?

    The one place a role set becomes a yes or no. Everything above it asks about capabilities;
    everything below it is the role model as it stands today."""
    return any(role.value in held_roles for role in roles_granting(capability))
