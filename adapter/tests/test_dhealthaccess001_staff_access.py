"""DHEALTH-NATIVE-ACCESS-001: the organization staff access lifecycle, end to end over HTTP.

Driven through `TestClient` against `build_exchange_router` rather than against `StaffAccessService`
directly, because the claim this ticket exists to earn is *"a facility administrator can now
actually revoke staff authority through the product"*. A test that called the service would prove
the domain logic and would have proved nothing about whether an administrator can reach it -- which
is precisely the shape of DHEALTH-NATIVE-SEC-001's P1 (F1): the primitives were correct and
unreachable. Some service-level edges that cannot be produced over HTTP get their own tests at the
bottom.

Every "loses authority immediately" assertion below reuses the **same token object** across the
revocation. No re-login, no refresh, no new `Authorization` header: that is the property under test.
The real-browser, real-Keycloak version of the same claims is `ui/e2e/native/07-access-lifecycle.
spec.ts`; this file is the fast, exhaustive one.
"""

from __future__ import annotations

import json
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
from daher_adapter.exchange.membership import (
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    ExchangeIdentity,
    ExchangeSecurityAuditEvent,
    OrganizationMembership,
    OrganizationRole,
    OrganizationRoleAssignment,
)
from daher_adapter.exchange.organizations import OrganizationRepository


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "exchange-audit.jsonl"


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, audit_path: Path) -> TestClient:
    _, public_key = keypair
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
        audit_writer=ExchangeAuditWriter(audit_path),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def memberships(sessions: sessionmaker[Session]) -> OrganizationMembershipRepository:
    return OrganizationMembershipRepository(sessions)


@pytest.fixture
def role_assignments(sessions: sessionmaker[Session]) -> OrganizationRoleAssignmentRepository:
    return OrganizationRoleAssignmentRepository(sessions)


# -- harness ------------------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap(client: TestClient, private_key, subject: str, **kwargs) -> tuple[str, str]:
    """Returns `(identity_id, token)`. The token is kept and reused deliberately -- the revocation
    tests must present the *same* credential after authority changed underneath it."""
    token = make_token(private_key, subject=subject, **kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"], token


def _attach(
    client: TestClient, admin_token: str, organization_id: str, identity_id: str, roles: list[str]
) -> str:
    """Attach a person through the existing, supported grant endpoint. Returns the membership id."""
    response = client.post(
        f"/exchange/v1/organizations/{organization_id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["membership_id"]


def _db_audit(sessions: sessionmaker[Session]) -> list[ExchangeSecurityAuditEvent]:
    with sessions() as session:
        return list(
            session.scalars(
                select(ExchangeSecurityAuditEvent).order_by(ExchangeSecurityAuditEvent.created_at)
            ).all()
        )


def _audit_of_type(
    sessions: sessionmaker[Session], event_type: str
) -> list[ExchangeSecurityAuditEvent]:
    return [event for event in _db_audit(sessions) if event.event_type == event_type]


def _file_audit(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line
    ]


@pytest.fixture
def world(client: TestClient, keypair, organizations: OrganizationRepository):
    """Two organizations, and one of everything the ticket's §26 names.

    - Clinique A (PROVIDER): `admin_a` (ORG_ADMIN), `clinician` (CLINICIAN)
    - Laboratoire B (LAB): `admin_b` (ORG_ADMIN), `tech` (LAB_TECH), `releaser`
      (LAB_RESULT_RELEASER)

    Two organizations with two *different* administrators is what makes the cross-organization
    assertions real rather than self-referential: `admin_a` genuinely is an administrator, just not
    of Laboratoire B.
    """
    private_key, _ = keypair
    platform_token = make_token(private_key, subject="platform", roles=[PLATFORM_ADMIN_ROLE])

    provider = make_provider(organizations, "Clinique A")
    lab = make_lab(organizations, "Laboratoire B")

    admin_a_id, admin_a_token = _bootstrap(client, private_key, "admin-a", name="Admin A")
    admin_b_id, admin_b_token = _bootstrap(client, private_key, "admin-b", name="Admin B")
    clinician_id, clinician_token = _bootstrap(client, private_key, "clin", name="Clinicienne C")
    tech_id, tech_token = _bootstrap(client, private_key, "tech", name="Technicien L")
    releaser_id, releaser_token = _bootstrap(client, private_key, "rel", name="Libérateur R")

    # A platform admin bootstraps the first administrator of each organization -- the same route
    # `NewFacilityWizard`/`FacilityTeam` already use. Everything after this is done by the
    # organization's own administrator, which is the point.
    admin_a_membership = _attach(
        client, platform_token, str(provider.id), admin_a_id, ["ORG_ADMIN"]
    )
    admin_b_membership = _attach(client, platform_token, str(lab.id), admin_b_id, ["ORG_ADMIN"])

    clinician_membership = _attach(
        client, admin_a_token, str(provider.id), clinician_id, ["CLINICIAN"]
    )
    tech_membership = _attach(client, admin_b_token, str(lab.id), tech_id, ["LAB_TECH"])
    releaser_membership = _attach(
        client, admin_b_token, str(lab.id), releaser_id, ["LAB_RESULT_RELEASER"]
    )

    return {
        "private_key": private_key,
        "platform_token": platform_token,
        "provider_id": str(provider.id),
        "lab_id": str(lab.id),
        "admin_a": {
            "identity": admin_a_id,
            "token": admin_a_token,
            "membership": admin_a_membership,
        },
        "admin_b": {
            "identity": admin_b_id,
            "token": admin_b_token,
            "membership": admin_b_membership,
        },
        "clinician": {
            "identity": clinician_id,
            "token": clinician_token,
            "membership": clinician_membership,
        },
        "tech": {"identity": tech_id, "token": tech_token, "membership": tech_membership},
        "releaser": {
            "identity": releaser_id,
            "token": releaser_token,
            "membership": releaser_membership,
        },
    }


def _deactivate(client: TestClient, token: str, org: str, membership: str):
    return client.post(
        f"/exchange/v1/organizations/{org}/memberships/{membership}/deactivate",
        headers=_auth(token),
    )


def _reactivate(client: TestClient, token: str, org: str, membership: str):
    return client.post(
        f"/exchange/v1/organizations/{org}/memberships/{membership}/reactivate",
        headers=_auth(token),
    )


def _add_role(client: TestClient, token: str, org: str, membership: str, role: str):
    return client.put(
        f"/exchange/v1/organizations/{org}/memberships/{membership}/roles/{role}",
        headers=_auth(token),
    )


def _remove_role(client: TestClient, token: str, org: str, membership: str, role: str):
    return client.delete(
        f"/exchange/v1/organizations/{org}/memberships/{membership}/roles/{role}",
        headers=_auth(token),
    )


# -- ACCESS-3: membership deactivation, same still-valid token ------------------------------------


def test_deactivation_revokes_org_access_on_the_next_request_with_the_same_token(
    client: TestClient, world
) -> None:
    provider, clinician = world["provider_id"], world["clinician"]

    # Authority confirmed first, so a later refusal cannot be a route that never worked.
    before = client.get(
        f"/exchange/v1/organizations/{provider}/orders", headers=_auth(clinician["token"])
    )
    assert before.status_code == 200, before.text

    response = _deactivate(client, world["admin_a"]["token"], provider, clinician["membership"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "INACTIVE"

    # The identical token, not refreshed, not re-issued.
    after = client.get(
        f"/exchange/v1/organizations/{provider}/orders", headers=_auth(clinician["token"])
    )
    assert after.status_code == 404, after.text  # anti-enumeration: no relationship at all now


def test_deactivation_does_not_disable_the_identity_or_other_organizations(
    client: TestClient,
    world,
    sessions: sessionmaker[Session],
    organizations: OrganizationRepository,
) -> None:
    """§3's mandatory distinction: organization access is not a Keycloak account, and is not the
    person's access to a *different* organization."""
    provider, lab = world["provider_id"], world["lab_id"]
    clinician = world["clinician"]

    # The same human also works at the laboratory.
    lab_membership = _attach(
        client, world["admin_b"]["token"], lab, clinician["identity"], ["LAB_TECH"]
    )

    assert (
        _deactivate(
            client, world["admin_a"]["token"], provider, clinician["membership"]
        ).status_code
        == 200
    )

    # Still a usable identity: `/me` answers, and the identity row is untouched.
    me = client.get("/exchange/v1/me", headers=_auth(clinician["token"]))
    assert me.status_code == 200, me.text
    with sessions() as session:
        identity = session.get(ExchangeIdentity, uuid.UUID(clinician["identity"]))
        assert identity is not None
        assert identity.status == "ACTIVE"

    # And the laboratory membership is untouched: Clinique A's decision is Clinique A's only.
    statuses = {m["organization_id"]: m["membership_status"] for m in me.json()["memberships"]}
    assert statuses[provider] == "INACTIVE"
    assert statuses[lab] == "ACTIVE"
    lab_work = client.get(
        f"/exchange/v1/organizations/{lab}/lab-orders", headers=_auth(clinician["token"])
    )
    assert lab_work.status_code == 200, lab_work.text
    assert lab_membership  # the membership id is what the other org's admin would administer


def test_deactivation_keeps_the_membership_row_and_its_history(
    client: TestClient, world, sessions: sessionmaker[Session]
) -> None:
    """§24: revocation must not destroy clinical attribution. The row survives; only status
    moves."""
    clinician = world["clinician"]
    _deactivate(client, world["admin_a"]["token"], world["provider_id"], clinician["membership"])

    with sessions() as session:
        row = session.get(OrganizationMembership, uuid.UUID(clinician["membership"]))
        assert row is not None, "the membership row must survive -- attribution hangs off it"
        assert row.status == "INACTIVE"
        assert row.identity_id == uuid.UUID(clinician["identity"])


def test_deactivating_an_already_inactive_membership_is_a_conflict(
    client: TestClient, world
) -> None:
    provider, clinician = world["provider_id"], world["clinician"]
    admin = world["admin_a"]["token"]
    assert _deactivate(client, admin, provider, clinician["membership"]).status_code == 200
    again = _deactivate(client, admin, provider, clinician["membership"])
    assert again.status_code == 409, again.text


# -- ACCESS-1 / ACCESS-2: role revocation, same still-valid token ---------------------------------


def test_removing_clinician_refuses_the_provider_action_on_the_next_request(
    client: TestClient, world
) -> None:
    provider, clinician = world["provider_id"], world["clinician"]

    assert (
        client.get(
            f"/exchange/v1/organizations/{provider}/orders", headers=_auth(clinician["token"])
        ).status_code
        == 200
    )

    removed = _remove_role(
        client, world["admin_a"]["token"], provider, clinician["membership"], "CLINICIAN"
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["roles"] == []

    after = client.get(
        f"/exchange/v1/organizations/{provider}/orders", headers=_auth(clinician["token"])
    )
    # 403, not 404: the membership is still ACTIVE, so the caller genuinely belongs to the
    # organization and is refused the specific authority. The two shapes are not interchangeable.
    assert after.status_code == 403, after.text


def test_removing_lab_result_releaser_refuses_the_release_surface(
    client: TestClient, world
) -> None:
    lab, releaser = world["lab_id"], world["releaser"]

    assert (
        client.get(
            f"/exchange/v1/organizations/{lab}/release-queue", headers=_auth(releaser["token"])
        ).status_code
        == 200
    )

    removed = _remove_role(
        client, world["admin_b"]["token"], lab, releaser["membership"], "LAB_RESULT_RELEASER"
    )
    assert removed.status_code == 200, removed.text

    after = client.get(
        f"/exchange/v1/organizations/{lab}/release-queue", headers=_auth(releaser["token"])
    )
    assert after.status_code == 403, after.text


def test_removing_a_role_the_membership_does_not_hold_is_a_conflict(
    client: TestClient, world
) -> None:
    response = _remove_role(
        client,
        world["admin_b"]["token"],
        world["lab_id"],
        world["tech"]["membership"],
        "LAB_RESULT_RELEASER",
    )
    assert response.status_code == 409, response.text


def test_role_removal_works_on_an_inactive_membership(client: TestClient, world) -> None:
    """Deliberate, and load-bearing for reactivation: roles survive deactivation, so trimming them
    while inactive is the only way to restore somebody with *less* authority than they left with."""
    lab, tech, admin = world["lab_id"], world["tech"], world["admin_b"]["token"]
    assert _deactivate(client, admin, lab, tech["membership"]).status_code == 200

    removed = _remove_role(client, admin, lab, tech["membership"], "LAB_TECH")
    assert removed.status_code == 200, removed.text
    assert removed.json()["roles"] == []

    restored = _reactivate(client, admin, lab, tech["membership"])
    assert restored.status_code == 200, restored.text
    assert restored.json()["roles"] == [], "nothing should come back that was removed first"


# -- ACCESS-4: reactivation ----------------------------------------------------------------------


def test_reactivation_restores_the_same_membership_and_its_surviving_roles(
    client: TestClient, world, sessions: sessionmaker[Session]
) -> None:
    provider, clinician = world["provider_id"], world["clinician"]
    admin = world["admin_a"]["token"]

    _deactivate(client, admin, provider, clinician["membership"])
    response = _reactivate(client, admin, provider, clinician["membership"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["membership_id"] == clinician["membership"], "the same row, never a duplicate"
    # The documented (not chosen) semantic: role rows survive deactivation, so reactivation restores
    # exactly what is still attached.
    assert body["roles"] == ["CLINICIAN"]

    with sessions() as session:
        rows = session.scalars(
            select(OrganizationMembership).where(
                OrganizationMembership.identity_id == uuid.UUID(clinician["identity"]),
                OrganizationMembership.organization_id == uuid.UUID(provider),
            )
        ).all()
        assert len(rows) == 1, "identity continuity: one membership, not a second one"

    # And the restored authority is real, on the next request.
    assert (
        client.get(
            f"/exchange/v1/organizations/{provider}/orders", headers=_auth(clinician["token"])
        ).status_code
        == 200
    )


def test_reactivating_an_active_membership_is_a_conflict(client: TestClient, world) -> None:
    response = _reactivate(
        client,
        world["admin_a"]["token"],
        world["provider_id"],
        world["clinician"]["membership"],
    )
    assert response.status_code == 409, response.text


# -- role addition ------------------------------------------------------------------------------


def test_adding_a_role_grants_authority_on_the_next_request_with_the_same_token(
    client: TestClient, world
) -> None:
    """§10's symmetry claim, and SEC-9's observation restated through the supported path."""
    lab, tech = world["lab_id"], world["tech"]

    before = client.get(
        f"/exchange/v1/organizations/{lab}/release-queue", headers=_auth(tech["token"])
    )
    assert before.status_code == 403, before.text

    added = _add_role(
        client, world["admin_b"]["token"], lab, tech["membership"], "LAB_RESULT_RELEASER"
    )
    assert added.status_code == 200, added.text
    assert sorted(added.json()["roles"]) == ["LAB_RESULT_RELEASER", "LAB_TECH"]

    after = client.get(
        f"/exchange/v1/organizations/{lab}/release-queue", headers=_auth(tech["token"])
    )
    assert after.status_code == 200, after.text


def test_adding_a_held_role_is_an_idempotent_success(client: TestClient, world) -> None:
    response = _add_role(
        client,
        world["admin_a"]["token"],
        world["provider_id"],
        world["clinician"]["membership"],
        "CLINICIAN",
    )
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["CLINICIAN"]


def test_adding_a_role_to_an_inactive_membership_is_refused(client: TestClient, world) -> None:
    """The existing model, unchanged: an inactive membership grants nothing and receives nothing."""
    provider, clinician = world["provider_id"], world["clinician"]
    admin = world["admin_a"]["token"]
    _deactivate(client, admin, provider, clinician["membership"])
    response = _add_role(client, admin, provider, clinician["membership"], "ORG_ADMIN")
    assert response.status_code == 409, response.text


# -- §6: platform authority cannot be manufactured -----------------------------------------------


@pytest.mark.parametrize("forbidden", ["PLATFORM_ADMIN", "dhealth-platform-admin", "SUPER_ADMIN"])
def test_org_admin_cannot_grant_platform_or_invented_authority(
    client: TestClient, world, forbidden: str
) -> None:
    response = _add_role(
        client,
        world["admin_a"]["token"],
        world["provider_id"],
        world["clinician"]["membership"],
        forbidden,
    )
    assert response.status_code == 422, response.text


def test_org_admin_cannot_grant_a_role_this_organization_type_may_not_hold(
    client: TestClient, world
) -> None:
    response = _add_role(
        client,
        world["admin_a"]["token"],
        world["provider_id"],
        world["clinician"]["membership"],
        "LAB_TECH",
    )
    assert response.status_code == 422, response.text


def test_granting_platform_authority_writes_no_role_assignment_and_no_audit_row(
    client: TestClient, world, sessions: sessionmaker[Session], role_assignments
) -> None:
    """The refusal must be a refusal all the way down -- no partial row, no audit row claiming a
    grant that did not happen."""
    before = len(_db_audit(sessions))
    _add_role(
        client,
        world["admin_a"]["token"],
        world["provider_id"],
        world["clinician"]["membership"],
        "PLATFORM_ADMIN",
    )
    assert role_assignments.list_for_membership(
        uuid.UUID(world["clinician"]["membership"])
    ) == frozenset({"CLINICIAN"})
    assert len(_db_audit(sessions)) == before


# -- ACCESS-5: cross-organization boundary -------------------------------------------------------


def test_org_a_admin_cannot_list_org_b_staff(client: TestClient, world) -> None:
    response = client.get(
        f"/exchange/v1/organizations/{world['lab_id']}/memberships",
        headers=_auth(world["admin_a"]["token"]),
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    "operation",
    ["deactivate", "reactivate", "add_role", "remove_role"],
    ids=["deactivate", "reactivate", "add-role", "remove-role"],
)
def test_org_a_admin_cannot_mutate_org_b_membership_even_knowing_every_uuid(
    client: TestClient, world, operation: str
) -> None:
    """The caller knows Laboratoire B's organization UUID, the membership UUID and the user UUID.
    Every one is real. Every attempt is refused, and the *reason* is not disclosed."""
    admin_a = world["admin_a"]["token"]
    lab, target = world["lab_id"], world["tech"]["membership"]

    if operation == "deactivate":
        response = _deactivate(client, admin_a, lab, target)
    elif operation == "reactivate":
        response = _reactivate(client, admin_a, lab, target)
    elif operation == "add_role":
        response = _add_role(client, admin_a, lab, target, "LAB_RESULT_RELEASER")
    else:
        response = _remove_role(client, admin_a, lab, target, "LAB_TECH")

    assert response.status_code == 404, response.text
    body = response.json()
    # `organization not found` is `_require_org_admin`'s own anti-enumeration answer, reached before
    # the membership is ever looked at. Either way the body must not mention the other organization,
    # the membership, or that a row exists somewhere.
    assert body["detail"] in ("organization not found", "membership not found")
    assert lab not in response.text
    assert target not in response.text


def test_a_membership_of_another_organization_is_reported_as_not_found_not_as_elsewhere(
    client: TestClient, world
) -> None:
    """The narrower leak: `admin_a` *is* an administrator of Clinique A, and names Clinique A in the
    URL while pointing at Laboratoire B's membership id. The organization check passes; the
    membership-ownership check is what must refuse, with no hint that the row exists."""
    response = _deactivate(
        client,
        world["admin_a"]["token"],
        world["provider_id"],
        world["tech"]["membership"],
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "membership not found"


def test_a_fabricated_membership_uuid_answers_exactly_like_a_real_foreign_one(
    client: TestClient, world
) -> None:
    fabricated = _deactivate(
        client, world["admin_a"]["token"], world["provider_id"], str(uuid.uuid4())
    )
    foreign = _deactivate(
        client, world["admin_a"]["token"], world["provider_id"], world["tech"]["membership"]
    )
    assert fabricated.status_code == foreign.status_code == 404
    assert fabricated.json() == foreign.json()


# -- §17: role-based refusal at the backend, whatever the browser rendered ------------------------


@pytest.mark.parametrize("persona", ["clinician", "tech", "releaser"])
def test_a_non_admin_cannot_administer_even_by_calling_the_endpoint_directly(
    client: TestClient, world, persona: str
) -> None:
    actor = world[persona]
    organization = world["provider_id"] if persona == "clinician" else world["lab_id"]
    target = world["clinician"] if persona == "clinician" else world["tech"]

    # A member of the organization who lacks ORG_ADMIN: 403, because they legitimately belong.
    assert (
        _deactivate(client, actor["token"], organization, target["membership"]).status_code == 403
    )
    assert (
        _remove_role(
            client, actor["token"], organization, target["membership"], "CLINICIAN"
        ).status_code
        == 403
    )


def test_an_unauthenticated_caller_is_refused_before_anything_else(
    client: TestClient, world
) -> None:
    response = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/memberships/"
        f"{world['clinician']['membership']}/deactivate"
    )
    assert response.status_code == 401, response.text


# -- ACCESS-6 / §12 / §13: last-active-administrator protection -----------------------------------


def test_the_last_admin_cannot_remove_their_own_org_admin_role(client: TestClient, world) -> None:
    admin = world["admin_a"]
    response = _remove_role(
        client, admin["token"], world["provider_id"], admin["membership"], "ORG_ADMIN"
    )
    assert response.status_code == 409, response.text
    assert "no active administrator" in response.json()["detail"]

    # And the authority genuinely survives the refusal -- a rolled-back transaction, not a
    # half-applied one.
    still_admin = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/memberships",
        headers=_auth(admin["token"]),
    )
    assert still_admin.status_code == 200, still_admin.text


def test_the_last_admin_cannot_deactivate_their_own_membership(client: TestClient, world) -> None:
    admin = world["admin_a"]
    response = _deactivate(client, admin["token"], world["provider_id"], admin["membership"])
    assert response.status_code == 409, response.text
    assert (
        client.get(
            f"/exchange/v1/organizations/{world['provider_id']}/memberships",
            headers=_auth(admin["token"]),
        ).status_code
        == 200
    )


def test_a_platform_admin_is_bound_by_the_same_protection(client: TestClient, world) -> None:
    """A deliberate choice, stricter than §12's literal wording, recorded in
    `staff_access.py`'s docstring: a platform administrator replacing an organization's only
    administrator adds the replacement first. The recovery direction is never blocked."""
    response = _deactivate(
        client,
        world["platform_token"],
        world["provider_id"],
        world["admin_a"]["membership"],
    )
    assert response.status_code == 409, response.text


def test_the_protection_is_a_transition_rule_not_a_floor(
    client: TestClient, world, sessions: sessionmaker[Session], memberships, role_assignments
) -> None:
    """An organization that *already* has zero active administrators must not be frozen by the
    last-administrator rule.

    This is a real, reachable state rather than a hypothetical: `POST .../memberships` will happily
    create a membership holding only `CLINICIAN`, so a facility can be enrolled and staffed with no
    administrator at all. A naive "after this change there must be at least one administrator" check
    would then refuse every unrelated role removal *and* every deactivation there — in exactly the
    organizations that most need repairing, and to the platform administrator sent to repair them.

    The zero-admin state is produced through `OrganizationRoleAssignmentRepository.remove`, the
    unguarded repository primitive, precisely because no supported endpoint can reach it: the guard
    under test is what prevents that. The count is then asserted to be zero before anything else
    runs, so this test cannot pass while silently never reaching the branch it claims to cover
    (`_guard_last_administrator`'s `admins_before == 0` early return).
    """
    provider = world["provider_id"]
    platform = world["platform_token"]

    role_assignments.remove(
        membership_id=uuid.UUID(world["admin_a"]["membership"]), role=OrganizationRole.ORG_ADMIN
    )
    with sessions() as session:
        stranded = memberships.count_active_with_role_in_session(
            session, organization_id=uuid.UUID(provider), role=OrganizationRole.ORG_ADMIN
        )
    assert stranded == 0, "the premise of this test is an organization with no active administrator"

    # An unrelated role removal is still allowed, and so is a deactivation. Neither *caused* the
    # organization to lose its last administrator, so neither is the rule's business.
    unrelated = _remove_role(
        client, platform, provider, world["clinician"]["membership"], "CLINICIAN"
    )
    assert unrelated.status_code == 200, unrelated.text

    deactivated = _deactivate(client, platform, provider, world["clinician"]["membership"])
    assert deactivated.status_code == 200, deactivated.text

    # And the recovery direction is open, which is what makes the rule safe to apply to a platform
    # administrator as well: granting ORG_ADMIN is never blocked by it.
    recovered = _add_role(client, platform, provider, world["admin_a"]["membership"], "ORG_ADMIN")
    assert recovered.status_code == 200, recovered.text
    # Now that there is one again, it is protected.
    assert (
        _remove_role(
            client, platform, provider, world["admin_a"]["membership"], "ORG_ADMIN"
        ).status_code
        == 409
    )


# -- ACCESS-7: safe handoff ---------------------------------------------------------------------


def test_an_admin_may_hand_over_and_then_step_down(client: TestClient, world) -> None:
    """ORG_ADMIN is assignable by an organization administrator under the *existing* policy
    (`ALLOWED_ROLES_BY_ORG_TYPE` includes it for both organization types and `POST .../memberships`
    already grants it), so succession needs no boundary weakening to work."""
    provider, admin_a = world["provider_id"], world["admin_a"]
    clinician = world["clinician"]

    promoted = _add_role(client, admin_a["token"], provider, clinician["membership"], "ORG_ADMIN")
    assert promoted.status_code == 200, promoted.text
    assert sorted(promoted.json()["roles"]) == ["CLINICIAN", "ORG_ADMIN"]

    # Two active administrators now, so the first may relinquish.
    stepped_down = _remove_role(
        client, admin_a["token"], provider, admin_a["membership"], "ORG_ADMIN"
    )
    assert stepped_down.status_code == 200, stepped_down.text

    # The organization still has exactly one administrator, and it is the new one.
    listing = client.get(
        f"/exchange/v1/organizations/{provider}/memberships",
        headers=_auth(clinician["token"]),
    )
    assert listing.status_code == 200, listing.text
    admins = [
        member
        for member in listing.json()
        if member["status"] == "ACTIVE" and "ORG_ADMIN" in member["roles"]
    ]
    assert len(admins) == 1
    assert admins[0]["membership_id"] == clinician["membership"]

    # And the outgoing administrator really has lost the surface, same token.
    assert (
        client.get(
            f"/exchange/v1/organizations/{provider}/memberships",
            headers=_auth(admin_a["token"]),
        ).status_code
        == 403
    )


def test_an_admin_may_drop_an_ordinary_role_from_themselves(client: TestClient, world) -> None:
    """§13: no hidden "you may never modify yourself" rule. Only lockout is refused."""
    provider, admin_a = world["provider_id"], world["admin_a"]
    assert (
        _add_role(
            client, admin_a["token"], provider, admin_a["membership"], "CLINICIAN"
        ).status_code
        == 200
    )
    response = _remove_role(client, admin_a["token"], provider, admin_a["membership"], "CLINICIAN")
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["ORG_ADMIN"]


# -- §14: an inactive administrator cannot continue administering ---------------------------------


def test_a_deactivated_admin_cannot_administer_with_their_still_valid_token(
    client: TestClient, world
) -> None:
    provider, admin_a, clinician = world["provider_id"], world["admin_a"], world["clinician"]

    # Two administrators, so deactivating one is permitted at all.
    assert (
        _add_role(
            client, admin_a["token"], provider, clinician["membership"], "ORG_ADMIN"
        ).status_code
        == 200
    )
    assert (
        _deactivate(client, clinician["token"], provider, admin_a["membership"]).status_code == 200
    )

    stale = admin_a["token"]
    assert (
        client.get(
            f"/exchange/v1/organizations/{provider}/memberships", headers=_auth(stale)
        ).status_code
        == 404
    ), "listing the team is refused, with the no-relationship shape"
    assert (
        _remove_role(client, stale, provider, clinician["membership"], "CLINICIAN").status_code
        == 404
    )
    assert _reactivate(client, stale, provider, admin_a["membership"]).status_code == 404, (
        "a deactivated administrator must not be able to let themselves back in"
    )


def test_authority_is_rechecked_inside_the_mutation_not_only_at_the_http_boundary(
    client: TestClient, world, sessions: sessionmaker[Session], memberships
) -> None:
    """The narrow race §25-B describes, made deterministic.

    `_require_org_admin` runs in its own session before the mutating transaction opens. If that were
    the only check, an administrator whose own membership was withdrawn *between* the two could
    still complete the request they had already begun. Simulating the interleaving faithfully needs
    two connections; what is asserted here is the property that makes the race safe -- the service
    re-reads the actor's authority inside the locked transaction -- by withdrawing the actor's
    ORG_ADMIN through the repository directly (standing in for the concurrent administrator) and
    then calling the service, bypassing the HTTP-layer check the way a lost race would.
    """
    from daher_adapter.exchange.authorization import AuthenticatedPrincipal
    from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
    from daher_adapter.exchange.staff_access import StaffAccessAuthorityLost, StaffAccessService

    roles = OrganizationRoleAssignmentRepository(sessions)
    service = StaffAccessService(
        session_factory=sessions,
        organizations=OrganizationRepository(sessions),
        memberships=memberships,
        roles=roles,
        security_audit=ExchangeSecurityAuditRepository(),
    )
    provider = uuid.UUID(world["provider_id"])
    admin_a, clinician = world["admin_a"], world["clinician"]

    # The concurrent administrator's effect: this actor is no longer an administrator.
    roles.remove(membership_id=uuid.UUID(admin_a["membership"]), role=OrganizationRole.ORG_ADMIN)

    with pytest.raises(StaffAccessAuthorityLost):
        service.remove_role(
            principal=AuthenticatedPrincipal(
                identity_id=uuid.UUID(admin_a["identity"]),
                issuer="https://exchange-test.invalid/realms/dhealth-exchange",
                subject="admin-a",
                platform_admin=False,
            ),
            organization_id=provider,
            membership_id=uuid.UUID(clinician["membership"]),
            role_name="CLINICIAN",
        )

    # Nothing was committed by the refused call.
    assert roles.list_for_membership(uuid.UUID(clinician["membership"])) == frozenset({"CLINICIAN"})


# -- §19: audit ----------------------------------------------------------------------------------


def test_every_successful_access_mutation_writes_one_transactional_audit_row(
    client: TestClient, world, sessions: sessionmaker[Session]
) -> None:
    provider, clinician, admin_a = world["provider_id"], world["clinician"], world["admin_a"]

    _add_role(client, admin_a["token"], provider, clinician["membership"], "ORG_ADMIN")
    _remove_role(client, admin_a["token"], provider, clinician["membership"], "ORG_ADMIN")
    _deactivate(client, admin_a["token"], provider, clinician["membership"])
    _reactivate(client, admin_a["token"], provider, clinician["membership"])

    for event_type in (
        "role_assigned",
        "role_removed",
        "membership_deactivated",
        "membership_reactivated",
    ):
        events = [
            event
            for event in _audit_of_type(sessions, event_type)
            if event.membership_id == uuid.UUID(clinician["membership"])
            # Filtered by role as well as membership: the `world` fixture's own CLINICIAN grant is
            # itself a `role_assigned` row on this membership, so filtering by membership alone
            # counts a row this test did not cause.
            and event.role in ("ORG_ADMIN", None)
        ]
        assert len(events) == 1, f"expected exactly one {event_type} row, got {len(events)}"
        event = events[0]
        # §19's minimum: actor, organization, target, action, role where applicable, timestamp.
        assert event.actor_identity_id == uuid.UUID(admin_a["identity"])
        assert event.organization_id == uuid.UUID(provider)
        assert event.subject_identity_id == uuid.UUID(clinician["identity"])
        assert event.created_at is not None
        if event_type in ("role_assigned", "role_removed"):
            assert event.role == "ORG_ADMIN"
        else:
            assert event.role is None


def test_a_refused_mutation_writes_no_transactional_audit_row(
    client: TestClient, world, sessions: sessionmaker[Session]
) -> None:
    """A refusal is not a mutation. `ExchangeSecurityAuditEvent`'s contract is that a row commits
    with a real change; refusal telemetry belongs to the best-effort file writer."""
    before = len(_db_audit(sessions))

    # Cross-organization, last-administrator, and a forbidden role: all three refused.
    _deactivate(client, world["admin_a"]["token"], world["lab_id"], world["tech"]["membership"])
    _remove_role(
        client,
        world["admin_a"]["token"],
        world["provider_id"],
        world["admin_a"]["membership"],
        "ORG_ADMIN",
    )
    _add_role(
        client,
        world["admin_a"]["token"],
        world["provider_id"],
        world["clinician"]["membership"],
        "PLATFORM_ADMIN",
    )
    assert len(_db_audit(sessions)) == before


def test_a_403_refusal_reaches_the_best_effort_refusal_log(
    client: TestClient, world, audit_path: Path
) -> None:
    before = len(_file_audit(audit_path))
    assert (
        _deactivate(
            client,
            world["clinician"]["token"],
            world["provider_id"],
            world["clinician"]["membership"],
        ).status_code
        == 403
    )
    after = _file_audit(audit_path)
    assert len(after) > before
    assert after[-1]["event_type"] == "authorization_refused"


def test_audit_rolls_back_with_the_mutation_when_the_invariant_refuses(
    client: TestClient, world, sessions: sessionmaker[Session], role_assignments
) -> None:
    """The last-administrator refusal happens *after* the role row was deleted and flushed. Both
    must be gone -- the role restored and no audit row -- or the transactional guarantee is a
    comment rather than a fact."""
    admin_a, provider = world["admin_a"], world["provider_id"]
    before = len(_db_audit(sessions))

    assert (
        _remove_role(
            client, admin_a["token"], provider, admin_a["membership"], "ORG_ADMIN"
        ).status_code
        == 409
    )

    assert OrganizationRole.ORG_ADMIN.value in role_assignments.list_for_membership(
        uuid.UUID(admin_a["membership"])
    ), "the flushed delete must have been rolled back"
    assert len(_db_audit(sessions)) == before


# -- §7 / §18: the staff directory read ----------------------------------------------------------


def test_the_staff_directory_shows_only_this_organization_and_no_credential_material(
    client: TestClient, world
) -> None:
    response = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/memberships",
        headers=_auth(world["admin_a"]["token"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    identities = {member["identity_id"] for member in body}
    assert world["admin_a"]["identity"] in identities
    assert world["clinician"]["identity"] in identities
    assert world["tech"]["identity"] not in identities, (
        "another organization's staff must not appear"
    )
    assert world["releaser"]["identity"] not in identities

    allowed_keys = {"membership_id", "identity_id", "display_name", "email", "status", "roles"}
    for member in body:
        assert set(member) == allowed_keys, "no field beyond the documented staff-access shape"
    # Nothing credential-shaped, and nothing clinical: this is an administrative surface.
    for forbidden in ("password", "token", "secret", "issuer", "subject", "patient"):
        assert forbidden not in response.text.lower()


def test_an_inactive_member_still_appears_in_the_directory(client: TestClient, world) -> None:
    """Losing the record would be worse than showing it: an administrator needs to see that the
    person was here, and reactivation needs something to point at."""
    provider, clinician = world["provider_id"], world["clinician"]
    _deactivate(client, world["admin_a"]["token"], provider, clinician["membership"])
    response = client.get(
        f"/exchange/v1/organizations/{provider}/memberships",
        headers=_auth(world["admin_a"]["token"]),
    )
    row = next(
        member for member in response.json() if member["membership_id"] == clinician["membership"]
    )
    assert row["status"] == "INACTIVE"
    assert row["roles"] == ["CLINICIAN"], "the roles that reactivation would restore"


# -- service-level edges not reachable over HTTP --------------------------------------------------


def test_a_role_name_that_is_not_an_organization_role_is_refused_before_any_lookup(
    sessions: sessionmaker[Session], organizations: OrganizationRepository, memberships
) -> None:
    """`_require_allowed_role` runs before the transaction opens, so a forbidden role never takes
    the organization lock. Asserted at the service level because the HTTP layer cannot show which
    check fired."""
    from daher_adapter.exchange.authorization import AuthenticatedPrincipal
    from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
    from daher_adapter.exchange.staff_access import ForbiddenRole, StaffAccessService

    provider = make_provider(organizations, "Clinique Seule")
    service = StaffAccessService(
        session_factory=sessions,
        organizations=organizations,
        memberships=memberships,
        roles=OrganizationRoleAssignmentRepository(sessions),
        security_audit=ExchangeSecurityAuditRepository(),
    )
    principal = AuthenticatedPrincipal(
        identity_id=uuid.uuid4(), issuer="i", subject="s", platform_admin=True
    )
    with pytest.raises(ForbiddenRole):
        service.add_role(
            principal=principal,
            organization_id=provider.id,
            # A membership id that does not exist: proving the role check fires first, since a
            # missing membership would otherwise raise `StaffAccessTargetNotFound`.
            membership_id=uuid.uuid4(),
            role_name="PLATFORM_ADMIN",
        )


def test_a_role_the_org_type_no_longer_permits_can_still_be_removed(
    client: TestClient, world, sessions: sessionmaker[Session], role_assignments
) -> None:
    """A role row that exists must always be withdrawable through the product.

    **Found by this ticket's own NC-ACCESS-5, and it was a real defect.** That control widened
    `ALLOWED_ROLES_BY_ORG_TYPE` so a PROVIDER could hold `LAB_TECH`, ACCESS-10 then granted one
    (which is why it went red), and reverting the control left a real `LAB_TECH` row at a provider
    that the product **could not remove**: `remove_role` validated against the org-type matrix
    first and answered 422 before it ever looked at the membership.

    That is the P1 this whole ticket exists to close, reintroduced in miniature -- authority that
    exists and cannot be withdrawn. The matrix now governs only what may be *granted*.

    The row is created through the repository rather than the API on purpose: no supported endpoint
    can produce it, which is precisely why the removal path has to cope with it. A migration that
    narrows the matrix, or a role retired from an organization type, would reach the same state.
    """
    provider, admin = world["provider_id"], world["admin_a"]
    membership_id = uuid.UUID(world["clinician"]["membership"])

    # Stand in for a row created under a wider matrix, or by an older release.
    with sessions() as session, session.begin():
        session.add(
            OrganizationRoleAssignment(
                membership_id=membership_id, role=OrganizationRole.LAB_TECH.value
            )
        )
    assert OrganizationRole.LAB_TECH.value in role_assignments.list_for_membership(membership_id)

    removed = _remove_role(client, admin["token"], provider, str(membership_id), "LAB_TECH")
    assert removed.status_code == 200, removed.text
    assert OrganizationRole.LAB_TECH.value not in role_assignments.list_for_membership(
        membership_id
    )


def test_removal_still_refuses_a_value_that_names_no_real_role(client: TestClient, world) -> None:
    """The half of the gate removal keeps. `PLATFORM_ADMIN` and an invented role cannot name a row
    that exists, so refusing them costs nothing and keeps the 422 honest."""
    for forbidden in ("PLATFORM_ADMIN", "SUPER_ADMIN", "not-a-role"):
        response = _remove_role(
            client,
            world["admin_a"]["token"],
            world["provider_id"],
            world["clinician"]["membership"],
            forbidden,
        )
        assert response.status_code == 422, f"{forbidden}: {response.text}"
