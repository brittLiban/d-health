"""DHEALTH-ACCESS-CAPABILITIES-001: capability-driven staff authorization, over HTTP.

The ticket's own completion standard, as tests:

    DHealth no longer needs to know someone's job title in order to decide whether they may
    perform a facility operation. The backend asks for an explicit capability. Roles are reusable
    templates for those capabilities. An organization may give or remove allowed capabilities from
    an individual without creating a new combination role. Removing authority takes effect on the
    next request even with the same valid login session.

Every scenario below is driven through `TestClient` against the real router, by people holding real
memberships, because the claim is about *reachability*: a resolver that returns the right set while
no endpoint consults it would pass a unit test and fail the product.

── The six required proofs (§46-§51) ──

    CAP-1  billing without BILLING_CLERK -- a direct grant, then its removal
    CAP-2  BILLING_CLERK with an individual deny -- the role stays, the authority goes
    CAP-3  an administrator composes billing independently of administration
    CAP-4  a clinician composes billing independently of clinical authority
    CAP-5  result release stays separately assignable, and entry never implies it
    CAP-6  the last administrator cannot be removed by *any* pathway

Plus the cross-tenant set (§52), the protected-capability set (§53), and the same-token revocation
and grant invariants (§36/§37) that DHEALTH-NATIVE-SEC-001 established for roles and this ticket
must preserve for capabilities.

All patient, service and price data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.capabilities import Capability
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent
from daher_adapter.exchange.organizations import OrganizationRepository


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, tmp_path: Path) -> TestClient:
    _, public_key = keypair
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
        audit_writer=ExchangeAuditWriter(tmp_path / "exchange-audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap(client: TestClient, private_key, subject: str) -> tuple[str, str]:
    token = make_token(private_key, subject=subject)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"], token


def _attach(client, admin_token, organization_id, identity_id, roles) -> str:
    response = client.post(
        f"/exchange/v1/organizations/{organization_id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["membership_id"]


@pytest.fixture
def world(client: TestClient, keypair, organizations: OrganizationRepository):
    """Two organizations, and one person per authority worth composing against.

    Every membership holds **exactly one** role. That is what makes the composition proofs real: if
    `clinician` also held `BILLING_CLERK`, granting them billing would prove nothing.
    """
    private_key, _ = keypair
    platform = make_token(private_key, subject="platform", roles=[PLATFORM_ADMIN_ROLE])

    provider = make_provider(organizations, "Clinique Capacités")
    other = make_provider(organizations, "Clinique Voisine")
    lab = make_lab(organizations, "Laboratoire Capacités")

    people: dict[str, dict[str, str]] = {}
    for name, org, roles in (
        ("admin", provider, ["ORG_ADMIN"]),
        ("admin2", provider, ["ORG_ADMIN"]),
        ("clinician", provider, ["CLINICIAN"]),
        ("clerk", provider, ["BILLING_CLERK"]),
        ("other_admin", other, ["ORG_ADMIN"]),
        ("lab_admin", lab, ["ORG_ADMIN"]),
        ("tech", lab, ["LAB_TECH"]),
        ("releaser", lab, ["LAB_RESULT_RELEASER"]),
    ):
        identity_id, token = _bootstrap(client, private_key, name)
        membership_id = _attach(client, platform, str(org.id), identity_id, roles)
        people[name] = {"token": token, "identity": identity_id, "membership": membership_id}

    return {
        "provider_id": str(provider.id),
        "other_id": str(other.id),
        "lab_id": str(lab.id),
        "platform": platform,
        "people": people,
    }


# -- the operations each capability actually gates -----------------------------------------------


def _billing(client, world, token, org=None):
    return client.get(
        f"/exchange/v1/organizations/{org or world['provider_id']}/billing/invoices",
        headers=_auth(token),
    )


def _patients(client, world, token):
    return client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/patients", headers=_auth(token)
    )


def _release_queue(client, world, token):
    return client.get(
        f"/exchange/v1/organizations/{world['lab_id']}/release-queue", headers=_auth(token)
    )


def _lab_queue(client, world, token):
    return client.get(
        f"/exchange/v1/organizations/{world['lab_id']}/lab-orders", headers=_auth(token)
    )


def _memberships(client, world, token, org=None):
    return client.get(
        f"/exchange/v1/organizations/{org or world['provider_id']}/memberships",
        headers=_auth(token),
    )


def _set(client, world, actor, membership_id, capability, effect, org=None):
    """Grant or deny one capability, as an administrator does through the product."""
    return client.put(
        f"/exchange/v1/organizations/{org or world['provider_id']}"
        f"/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(actor),
        json={"effect": effect},
    )


def _clear(client, world, actor, membership_id, capability, org=None):
    return client.delete(
        f"/exchange/v1/organizations/{org or world['provider_id']}"
        f"/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(actor),
    )


def _access(client, world, actor, membership_id, org=None):
    return client.get(
        f"/exchange/v1/organizations/{org or world['provider_id']}"
        f"/memberships/{membership_id}/access",
        headers=_auth(actor),
    )


def _me_capabilities(client, token, organization_id) -> list[str]:
    body = client.get("/exchange/v1/me", headers=_auth(token)).json()
    for membership in body["memberships"]:
        if membership["organization_id"] == organization_id:
            return membership["capabilities"]
    return []


# == CAP-1: billing without BILLING_CLERK ========================================================


def test_cap1_a_direct_grant_gives_billing_without_the_role(client, world):
    """**The heart of the ticket** (§46). If this does not work, the system is still role-hardcoded.

    An administrator is not a cashier and cannot reach the till. One direct grant later they can --
    with no role change, no new combination role, and no re-authentication: the same token they
    already held. Removing the grant closes it again on the very next request."""
    admin = world["people"]["admin"]
    provider = world["provider_id"]

    assert _billing(client, world, admin["token"]).status_code == 403
    assert Capability.BILLING_OPERATE.value not in _me_capabilities(
        client, admin["token"], provider
    )

    granted = _set(
        client,
        world,
        admin["token"],
        admin["membership"],
        Capability.BILLING_OPERATE.value,
        "GRANT",
    )
    assert granted.status_code == 200, granted.text

    # Same token, no logout, next request: authorized. §37.
    assert _billing(client, world, admin["token"]).status_code == 200
    assert Capability.BILLING_OPERATE.value in _me_capabilities(client, admin["token"], provider)
    # And the role list is untouched -- the point of §14 is not needing to reassign a role.
    assert sorted(_access(client, world, admin["token"], admin["membership"]).json()["roles"]) == [
        "ORG_ADMIN"
    ]

    cleared = _clear(
        client, world, admin["token"], admin["membership"], Capability.BILLING_OPERATE.value
    )
    assert cleared.status_code == 200, cleared.text

    # Same valid session again: refused. §36.
    assert _billing(client, world, admin["token"]).status_code == 403
    assert Capability.BILLING_OPERATE.value not in _me_capabilities(
        client, admin["token"], provider
    )


# == CAP-2: a role template with an individual deny ==============================================


def test_cap2_an_explicit_deny_beats_the_role_template(client, world):
    """§47. Two people with the same job title, different access -- without a second job title.

    The clerk keeps `BILLING_CLERK` throughout. What changes is one override, and the authority
    follows the override rather than the role. This is the property that makes `FRONT_DESK_WITH_
    BILLING` and `FRONT_DESK_NO_BILLING` unnecessary (§62)."""
    admin, clerk = world["people"]["admin"], world["people"]["clerk"]

    assert _billing(client, world, clerk["token"]).status_code == 200

    denied = _set(
        client,
        world,
        admin["token"],
        clerk["membership"],
        Capability.BILLING_OPERATE.value,
        "DENY",
    )
    assert denied.status_code == 200, denied.text

    assert _billing(client, world, clerk["token"]).status_code == 403
    sheet = _access(client, world, admin["token"], clerk["membership"]).json()
    assert sheet["roles"] == ["BILLING_CLERK"], "the role must survive the deny"
    entry = next(c for c in sheet["capabilities"] if c["key"] == Capability.BILLING_OPERATE.value)
    assert entry["effective"] is False
    assert entry["source"] == "DENY", "the administrator must see *why* it is refused (§20)"

    assert (
        _clear(
            client, world, admin["token"], clerk["membership"], Capability.BILLING_OPERATE.value
        ).status_code
        == 200
    )
    assert _billing(client, world, clerk["token"]).status_code == 200


def test_cap2b_a_deny_beats_a_direct_grant_too(client, world):
    """The precedence rule, stated once and tested at its hardest point.

    `(templates ∪ grants) − denies` means a DENY wins over a direct GRANT as well as over a
    template. There is no ordering subtlety and no "most recent wins": the model holds one opinion
    per (member, capability), so setting a DENY *replaces* a GRANT rather than racing it."""
    admin, clinician = world["people"]["admin"], world["people"]["clinician"]

    _set(
        client,
        world,
        admin["token"],
        clinician["membership"],
        Capability.BILLING_OPERATE.value,
        "GRANT",
    )
    assert _billing(client, world, clinician["token"]).status_code == 200

    _set(
        client,
        world,
        admin["token"],
        clinician["membership"],
        Capability.BILLING_OPERATE.value,
        "DENY",
    )
    assert _billing(client, world, clinician["token"]).status_code == 403

    sheet = _access(client, world, admin["token"], clinician["membership"]).json()
    entry = next(c for c in sheet["capabilities"] if c["key"] == Capability.BILLING_OPERATE.value)
    assert entry["source"] == "DENY"


# == CAP-3 and CAP-4: composition ================================================================


def test_cap3_administration_and_billing_compose_independently(client, world):
    """§48. An administrator gains and loses the till without their administration changing."""
    admin = world["people"]["admin"]

    assert _memberships(client, world, admin["token"]).status_code == 200
    assert _billing(client, world, admin["token"]).status_code == 403

    _set(
        client,
        world,
        admin["token"],
        admin["membership"],
        Capability.BILLING_OPERATE.value,
        "GRANT",
    )
    assert _billing(client, world, admin["token"]).status_code == 200
    assert _memberships(client, world, admin["token"]).status_code == 200, (
        "gaining billing must not cost them administration"
    )

    _clear(client, world, admin["token"], admin["membership"], Capability.BILLING_OPERATE.value)
    assert _billing(client, world, admin["token"]).status_code == 403
    assert _memberships(client, world, admin["token"]).status_code == 200


def test_cap4_clinical_authority_and_billing_compose_independently(client, world):
    """§49. A clinician gains the till and keeps every clinical authority; losing it costs nothing.

    §28 in the other direction too: the clinician does **not** get billing merely for being a
    clinician, which the first assertion pins."""
    admin, clinician = world["people"]["admin"], world["people"]["clinician"]

    assert _patients(client, world, clinician["token"]).status_code == 200
    assert _billing(client, world, clinician["token"]).status_code == 403

    _set(
        client,
        world,
        admin["token"],
        clinician["membership"],
        Capability.BILLING_OPERATE.value,
        "GRANT",
    )
    assert _billing(client, world, clinician["token"]).status_code == 200
    assert _patients(client, world, clinician["token"]).status_code == 200

    _clear(client, world, admin["token"], clinician["membership"], Capability.BILLING_OPERATE.value)
    assert _billing(client, world, clinician["token"]).status_code == 403
    assert _patients(client, world, clinician["token"]).status_code == 200


# == CAP-5: result release stays separate ========================================================


def test_cap5_result_release_is_independently_assignable(client, world):
    """§50/ADR-044. Entry authority and billing authority together never imply release.

    The technician gets the till as well, so the test cannot pass by the person simply having
    nothing: they hold two capabilities and still cannot release. Then release is granted, works,
    is withdrawn on the same valid session, and billing is unaffected throughout."""
    lab_admin, tech = world["people"]["lab_admin"], world["people"]["tech"]
    lab = world["lab_id"]

    _set(
        client,
        world,
        lab_admin["token"],
        tech["membership"],
        Capability.BILLING_OPERATE.value,
        "GRANT",
        org=lab,
    )
    assert _lab_queue(client, world, tech["token"]).status_code == 200
    assert _billing(client, world, tech["token"], org=lab).status_code == 200
    assert _release_queue(client, world, tech["token"]).status_code == 403, (
        "entry and money must never add up to release authority"
    )

    granted = _set(
        client,
        world,
        lab_admin["token"],
        tech["membership"],
        Capability.LAB_RESULT_RELEASE.value,
        "GRANT",
        org=lab,
    )
    assert granted.status_code == 200, granted.text
    assert _release_queue(client, world, tech["token"]).status_code == 200

    _clear(
        client,
        world,
        lab_admin["token"],
        tech["membership"],
        Capability.LAB_RESULT_RELEASE.value,
        org=lab,
    )
    assert _release_queue(client, world, tech["token"]).status_code == 403
    assert _billing(client, world, tech["token"], org=lab).status_code == 200, (
        "withdrawing release must not touch anything else"
    )


# == CAP-6: the last administrator ===============================================================


def test_cap6_the_last_administrator_cannot_be_removed_by_any_pathway(client, world):
    """§51. Every route to zero administrators, refused.

    The pathway that matters most is the **deny**: under the old role-name invariant an
    administrator could keep `ORG_ADMIN` and be denied `staff.access.manage`, passing the check
    while leaving the organization unadministrable. That is NC-CAP-4, and this is the assertion it
    reddens."""
    provider = world["provider_id"]
    admin, admin2 = world["people"]["admin"], world["people"]["admin2"]

    # Two administrators exist, so removing one is allowed.
    removed = client.delete(
        f"/exchange/v1/organizations/{provider}/memberships/{admin2['membership']}/roles/ORG_ADMIN",
        headers=_auth(admin["token"]),
    )
    assert removed.status_code == 200, removed.text

    # One administrator left. Every pathway to zero must now be refused.
    denied = _set(
        client,
        world,
        admin["token"],
        admin["membership"],
        Capability.STAFF_ACCESS_MANAGE.value,
        "DENY",
    )
    assert denied.status_code == 409, f"a self-deny left the org unadministrable: {denied.text}"

    role_removal = client.delete(
        f"/exchange/v1/organizations/{provider}/memberships/{admin['membership']}/roles/ORG_ADMIN",
        headers=_auth(admin["token"]),
    )
    assert role_removal.status_code == 409, role_removal.text

    deactivation = client.post(
        f"/exchange/v1/organizations/{provider}/memberships/{admin['membership']}/deactivate",
        headers=_auth(admin["token"]),
    )
    assert deactivation.status_code == 409, deactivation.text

    # They are still administering, which is the point of refusing.
    assert _memberships(client, world, admin["token"]).status_code == 200


def test_cap6b_a_capability_granted_administrator_counts_and_can_be_relinquished(client, world):
    """The other half of §51: authority built *through a grant* counts as a viable administrator,
    and once a second one exists the first may step down.

    This is what stops the invariant being a trap. The clinician becomes an administrator by
    capability alone -- no `ORG_ADMIN` role anywhere -- and that is enough for the original
    administrator to hand over and leave."""
    provider = world["provider_id"]
    admin, admin2 = world["people"]["admin"], world["people"]["admin2"]
    clinician = world["people"]["clinician"]

    client.delete(
        f"/exchange/v1/organizations/{provider}/memberships/{admin2['membership']}/roles/ORG_ADMIN",
        headers=_auth(admin["token"]),
    )
    assert (
        _set(
            client,
            world,
            admin["token"],
            admin["membership"],
            Capability.STAFF_ACCESS_MANAGE.value,
            "DENY",
        ).status_code
        == 409
    )

    # A successor, by capability rather than by role.
    promoted = _set(
        client,
        world,
        admin["token"],
        clinician["membership"],
        Capability.STAFF_ACCESS_MANAGE.value,
        "GRANT",
    )
    assert promoted.status_code == 200, promoted.text
    assert _memberships(client, world, clinician["token"]).status_code == 200
    assert _access(client, world, admin["token"], clinician["membership"]).json()["roles"] == [
        "CLINICIAN"
    ], "the successor administers without holding ORG_ADMIN"

    # Now the original may stand down.
    stepped_down = _set(
        client,
        world,
        admin["token"],
        admin["membership"],
        Capability.STAFF_ACCESS_MANAGE.value,
        "DENY",
    )
    assert stepped_down.status_code == 200, stepped_down.text
    assert _memberships(client, world, admin["token"]).status_code == 403


# == §52: cross-tenant ===========================================================================


def test_capauth1_an_admin_cannot_read_another_organizations_member_access(client, world):
    """CAP-AUTH-1 and CAP-AUTH-5 together: not forbidden, *not found*.

    Knowing a membership uuid from another facility must tell an outside administrator nothing --
    including whether it exists."""
    other_admin = world["people"]["other_admin"]
    target = world["people"]["clerk"]["membership"]

    # Asking their own organization about a membership that is not theirs.
    assert (
        _access(client, world, other_admin["token"], target, org=world["other_id"]).status_code
        == 404
    )
    # ...and asking the organization that does own it.
    assert _access(client, world, other_admin["token"], target).status_code == 404


def test_capauth2_an_admin_cannot_grant_a_capability_in_another_organization(client, world):
    """CAP-AUTH-2. The mutation half of the boundary, which is the one that would be a breach."""
    other_admin = world["people"]["other_admin"]
    clerk = world["people"]["clerk"]

    through_own = _set(
        client,
        world,
        other_admin["token"],
        clerk["membership"],
        Capability.STAFF_ACCESS_MANAGE.value,
        "GRANT",
        org=world["other_id"],
    )
    assert through_own.status_code == 404, through_own.text

    through_owner = _set(
        client,
        world,
        other_admin["token"],
        clerk["membership"],
        Capability.STAFF_ACCESS_MANAGE.value,
        "GRANT",
    )
    assert through_owner.status_code == 404, through_owner.text

    # And nothing was written: the clerk still cannot administer.
    assert _memberships(client, world, clerk["token"]).status_code == 403


def test_capauth6_an_inactive_administrator_cannot_mutate_with_a_stale_token(client, world):
    """CAP-AUTH-6. The actor's authority is re-read inside the mutation's own transaction, so an
    administrator deactivated a moment ago cannot finish the request they had started."""
    provider = world["provider_id"]
    admin, admin2, clerk = (
        world["people"]["admin"],
        world["people"]["admin2"],
        world["people"]["clerk"],
    )

    assert (
        client.post(
            f"/exchange/v1/organizations/{provider}/memberships/{admin2['membership']}/deactivate",
            headers=_auth(admin["token"]),
        ).status_code
        == 200
    )

    refused = _set(
        client,
        world,
        admin2["token"],
        clerk["membership"],
        Capability.BILLING_CONFIGURE_PRICES.value,
        "GRANT",
    )
    assert refused.status_code in (403, 404), refused.text


def test_an_inactive_membership_holds_no_capability_however_it_was_granted(client, world):
    """§16: an inactive membership produces no ordinary facility capabilities.

    Tested against a *directly granted* capability specifically -- a deactivation that cleared
    template authority but left direct grants live would be the subtle version of this hole."""
    provider = world["provider_id"]
    admin, clinician = world["people"]["admin"], world["people"]["clinician"]

    _set(
        client,
        world,
        admin["token"],
        clinician["membership"],
        Capability.BILLING_OPERATE.value,
        "GRANT",
    )
    assert _billing(client, world, clinician["token"]).status_code == 200

    assert (
        client.post(
            f"/exchange/v1/organizations/{provider}/memberships/{clinician['membership']}/deactivate",
            headers=_auth(admin["token"]),
        ).status_code
        == 200
    )

    assert _billing(client, world, clinician["token"]).status_code == 404
    assert _patients(client, world, clinician["token"]).status_code == 404
    assert _me_capabilities(client, clinician["token"], provider) == []


# == §53: protected and invalid capabilities =====================================================


def test_a_facility_administrator_cannot_manufacture_platform_authority(client, world):
    """§7/§53. Not because a list excludes `PLATFORM_ADMIN`, but because `Capability` has no such
    member -- the same structural refusal `_require_organization_role` gives for roles.

    `POST /capabilities/{anything}` is therefore not a privilege-injection endpoint: an
    unrecognised key is refused before a row can exist."""
    admin, clerk = world["people"]["admin"], world["people"]["clerk"]
    for invented in (
        "PLATFORM_ADMIN",
        "platform.admin",
        "platform.*",
        "clinic.custom_permission_239",
        "",
        "billing.operate.extra",
    ):
        response = _set(client, world, admin["token"], clerk["membership"], invented, "GRANT")
        assert response.status_code in (404, 422), f"{invented!r} was not refused: {response.text}"

    # The platform admin's own authority is unchanged and still not a facility capability.
    assert Capability.STAFF_ACCESS_MANAGE.value in _me_capabilities(
        client, admin["token"], world["provider_id"]
    )


def test_a_capability_the_organization_type_cannot_hold_is_refused(client, world):
    """§53. A laboratory has no patient directory of its own (D-6 is open), so
    `patient.directory.read` is provider-only and granting it at a laboratory is refused rather
    than stored inert.

    An inert grant would be worse than a refusal: Team & Access would show it held and every gate
    would refuse it, which is the shape of bug nobody can diagnose from the screen."""
    lab_admin, tech = world["people"]["lab_admin"], world["people"]["tech"]
    refused = _set(
        client,
        world,
        lab_admin["token"],
        tech["membership"],
        Capability.PATIENT_DIRECTORY_READ.value,
        "GRANT",
        org=world["lab_id"],
    )
    assert refused.status_code == 422, refused.text


# == §38: audit ==================================================================================


def test_every_capability_mutation_is_audited_and_no_read_is(client, world, sessions):
    """§38. Actor, organization, target member, capability, mutation, timestamp -- and nothing for
    a read.

    The read assertion matters as much as the write one: D-8 (whether successful sensitive reads
    should be audited) is still open, and this ticket must not answer it by accident."""
    admin, clerk = world["people"]["admin"], world["people"]["clerk"]

    _set(
        client,
        world,
        admin["token"],
        clerk["membership"],
        Capability.BILLING_CONFIGURE_PRICES.value,
        "GRANT",
    )
    _set(
        client,
        world,
        admin["token"],
        clerk["membership"],
        Capability.BILLING_CONFIGURE_PRICES.value,
        "DENY",
    )
    _clear(
        client,
        world,
        admin["token"],
        clerk["membership"],
        Capability.BILLING_CONFIGURE_PRICES.value,
    )
    _access(client, world, admin["token"], clerk["membership"])

    with sessions() as session:
        rows = list(
            session.scalars(
                select(ExchangeSecurityAuditEvent)
                .where(ExchangeSecurityAuditEvent.capability.is_not(None))
                .order_by(ExchangeSecurityAuditEvent.created_at)
            ).all()
        )

    assert [row.event_type for row in rows] == [
        "capability_granted",
        "capability_denied",
        "capability_override_cleared",
    ]
    for row in rows:
        assert row.capability == Capability.BILLING_CONFIGURE_PRICES.value
        assert str(row.organization_id) == world["provider_id"]
        assert str(row.actor_identity_id) == admin["identity"]
        assert str(row.membership_id) == clerk["membership"]
        assert row.created_at is not None


def test_a_no_op_mutation_writes_no_audit_row(client, world, sessions):
    """Setting a GRANT that is already a GRANT is not a decision, and the audit table records
    decisions rather than clicks."""
    admin, clerk = world["people"]["admin"], world["people"]["clerk"]
    for _ in range(3):
        _set(
            client,
            world,
            admin["token"],
            clerk["membership"],
            Capability.BILLING_CONFIGURE_PRICES.value,
            "GRANT",
        )

    with sessions() as session:
        count = len(
            session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.event_type == "capability_granted"
                )
            ).all()
        )
    assert count == 1


# == the member access sheet =====================================================================


def test_the_access_sheet_explains_where_each_authority_comes_from(client, world):
    """§20. Effective, and *why* -- so an administrator knows what changing a role will cost."""
    admin, clinician = world["people"]["admin"], world["people"]["clinician"]
    _set(
        client,
        world,
        admin["token"],
        clinician["membership"],
        Capability.BILLING_OPERATE.value,
        "GRANT",
    )

    sheet = _access(client, world, admin["token"], clinician["membership"]).json()
    by_key = {entry["key"]: entry for entry in sheet["capabilities"]}

    assert by_key[Capability.VISIT_DOCUMENT.value]["source"] == "TEMPLATE:CLINICIAN"
    assert by_key[Capability.VISIT_DOCUMENT.value]["effective"] is True
    assert by_key[Capability.BILLING_OPERATE.value]["source"] == "GRANT"
    assert by_key[Capability.BILLING_OPERATE.value]["effective"] is True
    assert by_key[Capability.STAFF_ACCESS_MANAGE.value]["source"] is None
    assert by_key[Capability.STAFF_ACCESS_MANAGE.value]["effective"] is False

    # Human words, never internal vocabulary (§9/§19).
    assert by_key[Capability.BILLING_OPERATE.value]["label"] == "Tenir la caisse"
    assert by_key[Capability.BILLING_OPERATE.value]["group"] == "Facturation"


def test_the_access_sheet_offers_only_what_this_organization_type_can_hold(client, world):
    """A laboratory administrator is not offered `patient.register`. Offering a toggle the server
    would refuse is the screen-level version of the inert-grant bug above."""
    lab_admin, tech = world["people"]["lab_admin"], world["people"]["tech"]
    sheet = _access(
        client, world, lab_admin["token"], tech["membership"], org=world["lab_id"]
    ).json()
    keys = {entry["key"] for entry in sheet["capabilities"]}

    assert Capability.LAB_RESULT_RELEASE.value in keys
    assert Capability.PATIENT_DIRECTORY_READ.value not in keys
    assert Capability.VISIT_DOCUMENT.value not in keys


def test_the_access_sheet_carries_no_clinical_or_financial_content(client, world):
    """§59: Team & Access administers people and authority. It is not a place patient data,
    results or invoices appear, and its response model cannot grow them by accident."""
    admin, clerk = world["people"]["admin"], world["people"]["clerk"]
    sheet = _access(client, world, admin["token"], clerk["membership"]).json()

    assert set(sheet) == {
        "membership_id",
        "identity_id",
        "display_name",
        "status",
        "roles",
        "capabilities",
    }
    for entry in sheet["capabilities"]:
        assert set(entry) == {"key", "label", "description", "group", "effective", "source"}


# == §35: organization scope =====================================================================


def test_the_same_identity_holds_different_capabilities_at_two_organizations(client, world):
    """§6/§35. Capabilities are scoped through membership, and two facilities' answers must not
    bleed together.

    Built by attaching one identity to both organizations with different roles, then reading `/me`:
    two entries, two different capability sets, neither contaminated by the other."""
    # The clerk is already `BILLING_CLERK` at the provider. Attaching the *same identity* to the
    # neighbouring clinic as an administrator is the whole setup: one person, two facilities, two
    # different jobs.
    dual = world["people"]["clerk"]
    _attach(client, world["platform"], world["other_id"], dual["identity"], ["ORG_ADMIN"])

    body = client.get("/exchange/v1/me", headers=_auth(dual["token"])).json()
    by_org = {m["organization_id"]: set(m["capabilities"]) for m in body["memberships"]}

    assert by_org[world["provider_id"]] == {Capability.BILLING_OPERATE.value}
    assert Capability.STAFF_ACCESS_MANAGE.value in by_org[world["other_id"]]
    assert Capability.BILLING_OPERATE.value not in by_org[world["other_id"]], (
        "authority at one facility must not appear at another"
    )
    # ...and the gates agree with `/me`.
    assert _memberships(client, world, dual["token"]).status_code == 403
    assert _memberships(client, world, dual["token"], org=world["other_id"]).status_code == 200
    assert _billing(client, world, dual["token"]).status_code == 200


def test_module_imports_uuid():
    """Keeps the import used by future additions from being stripped by a lint-only edit."""
    assert uuid is not None
