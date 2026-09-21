"""DH-EX-002's HTTP-level required test matrix: authentication negatives at the endpoint boundary,
platform-admin gating, org-admin scoping, role-escalation negative controls, the anti-enumeration
403-vs-404 policy, membership-replay/reactivation behavior, and audit-log assertions (an event per
mutation, never a token in the log).

Uses a bare `FastAPI()` app with `build_exchange_router` mounted directly -- not `create_app()` --
so a `StaticKeySource`-backed `TokenValidator` (a locally generated RSA keypair, zero network) can
be injected. `test_exchange_app_wiring.py` separately proves the real `create_app()` wiring itself
(including the fail-closed default when OIDC is unconfigured).
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
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent
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
    validator = build_validator(public_key)
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=validator,
        audit_writer=ExchangeAuditWriter(audit_path),
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


def _audit_events(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line
    ]


def _db_security_audit_events(sessions: sessionmaker[Session]) -> list[dict]:
    """Reads the authoritative, transactional `ExchangeSecurityAuditEvent` table -- distinct from
    `_audit_events` above, which reads the best-effort refusal-telemetry file. DH-EX-002R moved
    organization/membership/role mutation audit rows into this table exclusively."""
    with sessions() as session:
        rows = session.query(ExchangeSecurityAuditEvent).all()
        return [
            {
                "event_type": row.event_type,
                "actor_identity_id": str(row.actor_identity_id),
                "organization_id": str(row.organization_id) if row.organization_id else None,
                "subject_identity_id": (
                    str(row.subject_identity_id) if row.subject_identity_id else None
                ),
                "membership_id": str(row.membership_id) if row.membership_id else None,
                "role": row.role,
                "resource_type": row.resource_type,
                "resource_id": str(row.resource_id) if row.resource_id else None,
                "outcome": row.outcome,
            }
            for row in rows
        ]


def _bootstrap_identity(client: TestClient, private_key, subject: str, **token_kwargs) -> str:
    token = make_token(private_key, subject=subject, **token_kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _create_org(client: TestClient, admin_token: str, *, name: str, org_type: str) -> dict:
    response = client.post(
        "/exchange/v1/organizations",
        headers=_auth(admin_token),
        json={"name": name, "organization_type": org_type},
    )
    assert response.status_code == 201, response.text
    return response.json()


# -- authentication negatives at the endpoint boundary -------------------------------------------


def test_me_without_a_token_is_401(client: TestClient) -> None:
    response = client.get("/exchange/v1/me")
    assert response.status_code == 401


def test_me_with_a_malformed_token_is_401(client: TestClient) -> None:
    response = client.get("/exchange/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_organizations_post_without_a_token_is_401(client: TestClient) -> None:
    response = client.post(
        "/exchange/v1/organizations", json={"name": "X", "organization_type": "PROVIDER"}
    )
    assert response.status_code == 401


def test_protected_routes_do_not_422_on_nested_dependency_resolution(
    client: TestClient, keypair
) -> None:
    """Permanent regression control for a real bug found only by running this suite: with
    `from __future__ import annotations` active in `exchange/api.py`, FastAPI could not resolve
    `Depends(get_principal)` from the nested-closure dependency pattern used throughout this
    router (the dependency functions are defined *inside* `build_exchange_router`, not at module
    scope) -- every protected endpoint silently returned `422 {"detail": "principal required"}`
    instead of running any authentication/authorization logic at all. A valid token must reach
    real application logic (here, a real 200) on every protected route, never a schema-validation
    422 caused by FastAPI failing to wire up the dependency itself."""
    private_key, _ = keypair
    token = make_token(private_key, subject="regression-check-sub")

    response = client.get("/exchange/v1/me", headers=_auth(token))

    assert response.status_code == 200, (
        f"protected route returned {response.status_code}, not 200 -- if this is 422 with "
        f"'principal required' in the body, the nested FastAPI dependency-injection regression "
        f"is back: {response.text}"
    )


# -- identity bootstrap via /me --------------------------------------------------------------------


def test_me_bootstraps_an_identity_exactly_once(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    first_id = _bootstrap_identity(client, private_key, "sub-1")
    second_id = _bootstrap_identity(client, private_key, "sub-1")
    assert first_id == second_id


def test_me_reflects_platform_admin_role(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    token = make_token(private_key, subject="admin-1", roles=[PLATFORM_ADMIN_ROLE])
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["platform_admin"] is True


def test_me_grants_no_memberships_on_bare_identity_creation(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    token = make_token(private_key, subject="sub-1")
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.json()["memberships"] == []


# -- platform-admin gating on organization creation ----------------------------------------------


def test_only_platform_admin_can_create_an_organization(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    non_admin_token = make_token(private_key, subject="sub-1", roles=["clinician"])
    response = client.post(
        "/exchange/v1/organizations",
        headers=_auth(non_admin_token),
        json={"name": "Clinic X", "organization_type": "PROVIDER"},
    )
    assert response.status_code == 403


def test_org_admin_role_alone_cannot_create_an_organization(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    identity_id = _bootstrap_identity(client, private_key, "sub-1")
    _grant_org_admin(client, keypair, organizations, org.id, identity_id, "sub-1")

    admin_token = make_token(private_key, subject="sub-1")
    response = client.post(
        "/exchange/v1/organizations",
        headers=_auth(admin_token),
        json={"name": "Clinic Y", "organization_type": "PROVIDER"},
    )
    assert response.status_code == 403


def test_platform_admin_creates_an_organization(
    client: TestClient, keypair, sessions: sessionmaker[Session]
) -> None:
    private_key, _ = keypair
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])
    org = _create_org(client, admin_token, name="DHealth Demo Lab A", org_type="LAB")
    assert org["organization_type"] == "LAB"
    assert org["status"] == "ACTIVE"

    # Authoritative audit is now the DB table (DH-EX-002R), not the best-effort file.
    events = [
        e for e in _db_security_audit_events(sessions) if e["event_type"] == "organization_created"
    ]
    assert len(events) == 1
    assert events[0]["organization_id"] == org["id"]
    assert events[0]["outcome"] == "created"


def test_invalid_organization_type_is_rejected_before_pydantic_even_bothers(
    client: TestClient, keypair
) -> None:
    private_key, _ = keypair
    admin_token = make_token(private_key, subject="admin-1", roles=[PLATFORM_ADMIN_ROLE])
    response = client.post(
        "/exchange/v1/organizations",
        headers=_auth(admin_token),
        json={"name": "X", "organization_type": "NOT_A_TYPE"},
    )
    assert response.status_code == 422


def test_organization_create_body_forbids_unknown_fields(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    admin_token = make_token(private_key, subject="admin-1", roles=[PLATFORM_ADMIN_ROLE])
    response = client.post(
        "/exchange/v1/organizations",
        headers=_auth(admin_token),
        json={"name": "X", "organization_type": "PROVIDER", "status": "ACTIVE"},
    )
    assert response.status_code == 422


# -- anti-enumeration: org lookup ------------------------------------------------------------------


def test_nonmember_gets_404_not_403_for_an_existing_org(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    token = make_token(private_key, subject="outsider-1")
    response = client.get(f"/exchange/v1/organizations/{org.id}", headers=_auth(token))
    assert response.status_code == 404


def test_nonmember_and_nonexistent_org_are_indistinguishable(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    token = make_token(private_key, subject="outsider-1")

    real_org_response = client.get(f"/exchange/v1/organizations/{org.id}", headers=_auth(token))
    fake_org_response = client.get(
        f"/exchange/v1/organizations/{uuid.uuid4()}", headers=_auth(token)
    )

    assert real_org_response.status_code == fake_org_response.status_code == 404
    assert real_org_response.json() == fake_org_response.json()


def test_member_can_read_their_own_organization(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    identity_id = _bootstrap_identity(client, private_key, "sub-1")
    _attach_member(client, keypair, organizations, org.id, identity_id, roles=[])

    token = make_token(private_key, subject="sub-1")
    response = client.get(f"/exchange/v1/organizations/{org.id}", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["id"] == str(org.id)


# -- membership creation / role-escalation negative controls --------------------------------------


def _grant_org_admin(
    client, keypair, organizations, org_id, target_identity_id, target_subject
) -> None:
    """Bootstraps a platform-admin session purely to seed an ORG_ADMIN for `org_id` -- test setup,
    not itself the behavior under test."""
    private_key, _ = keypair
    platform_admin_token = make_token(
        private_key, subject="setup-platform-admin", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(platform_admin_token),
        json={"identity_id": target_identity_id, "roles": ["ORG_ADMIN"]},
    )
    assert response.status_code in (200, 201), response.text


def _attach_member(client, keypair, organizations, org_id, target_identity_id, roles) -> dict:
    private_key, _ = keypair
    platform_admin_token = make_token(
        private_key, subject="setup-platform-admin", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(platform_admin_token),
        json={"identity_id": target_identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_org_admin_can_attach_a_member_with_a_role(
    client: TestClient,
    keypair,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    member_identity_id = _bootstrap_identity(client, private_key, "member-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": member_identity_id, "roles": ["CLINICIAN"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["roles"] == ["CLINICIAN"]
    assert body["status"] == "ACTIVE"

    # Authoritative audit is now the DB table (DH-EX-002R), not the best-effort file.
    role_events = [
        e for e in _db_security_audit_events(sessions) if e["event_type"] == "role_assigned"
    ]
    assert any(e["role"] == "CLINICIAN" and e["outcome"] == "created" for e in role_events)


def test_membership_creation_is_idempotent_over_http(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    member_identity_id = _bootstrap_identity(client, private_key, "member-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    body = {"identity_id": member_identity_id, "roles": ["CLINICIAN"]}
    first = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships", headers=_auth(admin_token), json=body
    )
    second = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships", headers=_auth(admin_token), json=body
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["membership_id"] == second.json()["membership_id"]


def test_omitting_an_already_held_role_does_not_remove_it(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    """DH-EX-002R Part I: `roles` is ensure/add, never replace -- confirmed by granting two roles,
    then re-POSTing with only one of them requested, and checking the omitted role survives."""
    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    member_identity_id = _bootstrap_identity(client, private_key, "member-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    first = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": member_identity_id, "roles": ["ORG_ADMIN", "CLINICIAN"]},
    )
    assert first.status_code == 201, first.text
    assert sorted(first.json()["roles"]) == ["CLINICIAN", "ORG_ADMIN"]

    second = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": member_identity_id, "roles": ["CLINICIAN"]},
    )
    assert second.status_code == 200, second.text
    assert sorted(second.json()["roles"]) == ["CLINICIAN", "ORG_ADMIN"], (
        "ORG_ADMIN was omitted from this request but must not have been removed -- POST is "
        "ensure/add semantics, never replace"
    )


def test_clinician_cannot_add_a_membership(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    clinician_identity_id = _bootstrap_identity(client, private_key, "clinician-sub")
    _attach_member(client, keypair, organizations, org.id, clinician_identity_id, ["CLINICIAN"])
    other_identity_id = _bootstrap_identity(client, private_key, "other-sub")

    clinician_token = make_token(private_key, subject="clinician-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(clinician_token),
        json={"identity_id": other_identity_id, "roles": ["CLINICIAN"]},
    )
    assert response.status_code == 403


def test_org_admin_can_grant_org_admin_to_a_peer_in_their_own_organization(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    """DH-EX-002R Part J: delegation within one's own organization is approved MVP behavior, not
    escalation -- distinct from granting platform admin (structurally impossible, see the
    Literal-schema test above) or acting in another organization (see the test below)."""
    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    peer_identity_id = _bootstrap_identity(client, private_key, "peer-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": peer_identity_id, "roles": ["ORG_ADMIN"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["roles"] == ["ORG_ADMIN"]

    # The newly-delegated peer's own token now works as an org admin for the same org too.
    peer_token = make_token(private_key, subject="peer-sub")
    peer_list = client.get(
        f"/exchange/v1/organizations/{org.id}/memberships", headers=_auth(peer_token)
    )
    assert peer_list.status_code == 200


def test_org_admin_cannot_manage_a_different_organization(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org_a = make_provider(organizations, name="Clinic A")
    org_b = make_provider(organizations, name="Clinic B")
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org_a.id, admin_identity_id, "admin-sub")
    other_identity_id = _bootstrap_identity(client, private_key, "other-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org_b.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": other_identity_id, "roles": ["CLINICIAN"]},
    )
    assert response.status_code == 404


def test_provider_org_admin_cannot_grant_lab_tech_and_nothing_is_left_behind(
    client: TestClient,
    keypair,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    target_identity_id = _bootstrap_identity(client, private_key, "target-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": target_identity_id, "roles": ["ORG_ADMIN", "LAB_TECH"]},
    )
    assert response.status_code == 422

    # Zero mutation and zero authoritative audit rows for the rejected attempt -- role validity is
    # checked before the mutation transaction is even opened, so nothing was ever added to it.
    events_for_target = [
        e
        for e in _db_security_audit_events(sessions)
        if e["subject_identity_id"] == target_identity_id
    ]
    assert events_for_target == []

    # The mixed-validity role list must not have left a partial grant behind: re-attaching the
    # same identity with a valid role list must still report a fresh membership (201), not a
    # replay (200), proving nothing was committed by the rejected request above.
    retry = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": target_identity_id, "roles": ["CLINICIAN"]},
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["roles"] == ["CLINICIAN"]


def test_lab_org_admin_cannot_grant_clinician(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_lab(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    target_identity_id = _bootstrap_identity(client, private_key, "target-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": target_identity_id, "roles": ["CLINICIAN"]},
    )
    assert response.status_code == 422


def test_platform_admin_role_cannot_be_requested_through_this_endpoint(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    target_identity_id = _bootstrap_identity(client, private_key, "target-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": target_identity_id, "roles": ["PLATFORM_ADMIN"]},
    )
    # Rejected by the closed Pydantic schema itself -- PLATFORM_ADMIN is not a member of the
    # `roles` field's Literal, so this never even reaches application authorization logic.
    assert response.status_code == 422


def test_historical_connector_sys_admin_role_grants_zero_exchange_platform_admin(
    client: TestClient, keypair
) -> None:
    """DH-EX-002R Part D: the OpenMRS/OpenELIS connector realm's `sys-admin` role (see
    `keycloak/realm-config/daher-realm.json`) must not be mistaken for Exchange platform-admin
    authority -- these are two entirely separate role vocabularies in two separate realms."""
    private_key, _ = keypair
    token = make_token(private_key, subject="connector-sys-admin", roles=["sys-admin"])
    me = client.get("/exchange/v1/me", headers=_auth(token))
    assert me.json()["platform_admin"] is False

    response = client.post(
        "/exchange/v1/organizations",
        headers=_auth(token),
        json={"name": "X", "organization_type": "PROVIDER"},
    )
    assert response.status_code == 403


def test_correct_platform_admin_role_string_from_the_wrong_issuer_is_rejected(
    client: TestClient, keypair
) -> None:
    """DH-EX-002R Part D: the platform-admin role claim is trusted only *after* full JWT
    validation (issuer included) -- a token carrying the right role string but signed for a
    different issuer must be rejected outright (401), never evaluated for its role content."""
    private_key, _ = keypair
    token = make_token(
        private_key,
        subject="attacker",
        issuer="https://attacker.invalid/realms/evil",
        roles=[PLATFORM_ADMIN_ROLE],
    )
    response = client.post(
        "/exchange/v1/organizations",
        headers=_auth(token),
        json={"name": "X", "organization_type": "PROVIDER"},
    )
    assert response.status_code == 401


def test_attaching_an_identity_that_has_never_authenticated_is_404(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": str(uuid.uuid4()), "roles": []},
    )
    assert response.status_code == 404


# -- inactive membership: no silent reactivation ---------------------------------------------------


def test_reposting_an_inactive_membership_is_refused_not_silently_reactivated(
    client: TestClient, keypair, organizations: OrganizationRepository, sessions
) -> None:
    from daher_adapter.exchange.membership import OrganizationMembershipRepository

    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    member_identity_id = _bootstrap_identity(client, private_key, "member-sub")
    attached = _attach_member(
        client, keypair, organizations, org.id, member_identity_id, ["CLINICIAN"]
    )

    memberships = OrganizationMembershipRepository(sessions)
    memberships.set_inactive(uuid.UUID(attached["membership_id"]))

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.post(
        f"/exchange/v1/organizations/{org.id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": member_identity_id, "roles": ["CLINICIAN"]},
    )
    assert response.status_code == 409


# -- membership listing: org-scoped, admin-only ----------------------------------------------------


def test_list_memberships_requires_org_admin(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    clinician_identity_id = _bootstrap_identity(client, private_key, "clinician-sub")
    _attach_member(client, keypair, organizations, org.id, clinician_identity_id, ["CLINICIAN"])

    clinician_token = make_token(private_key, subject="clinician-sub")
    response = client.get(
        f"/exchange/v1/organizations/{org.id}/memberships", headers=_auth(clinician_token)
    )
    assert response.status_code == 403


def test_list_memberships_for_a_foreign_org_is_404(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org_a = make_provider(organizations, name="Clinic A")
    org_b = make_provider(organizations, name="Clinic B")
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org_a.id, admin_identity_id, "admin-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.get(
        f"/exchange/v1/organizations/{org_b.id}/memberships", headers=_auth(admin_token)
    )
    assert response.status_code == 404


def test_org_admin_lists_their_own_organizations_memberships(
    client: TestClient, keypair, organizations: OrganizationRepository
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations, operational=False)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    member_identity_id = _bootstrap_identity(client, private_key, "member-sub")
    _attach_member(client, keypair, organizations, org.id, member_identity_id, ["CLINICIAN"])

    admin_token = make_token(private_key, subject="admin-sub")
    response = client.get(
        f"/exchange/v1/organizations/{org.id}/memberships", headers=_auth(admin_token)
    )
    assert response.status_code == 200
    identity_ids = {m["identity_id"] for m in response.json()}
    assert identity_ids == {admin_identity_id, member_identity_id}


# -- audit: never a token, no duplicate "created" on replay ------------------------------------


def test_audit_log_never_contains_a_bearer_token(
    client: TestClient,
    keypair,
    organizations: OrganizationRepository,
    audit_path: Path,
    sessions: sessionmaker[Session],
) -> None:
    private_key, _ = keypair
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])
    _create_org(client, admin_token, name="DHealth Demo Clinic", org_type="PROVIDER")

    raw_audit_text = audit_path.read_text(encoding="utf-8")
    assert admin_token not in raw_audit_text
    assert "Bearer" not in raw_audit_text
    for event in _audit_events(audit_path):
        assert set(event.keys()) <= {
            "timestamp",
            "event_type",
            "lab_order_id",
            "source_organization_id",
            "target_organization_id",
            "patient_reference_id",
            "actor_identity_id",
            "subject_identity_id",
            "organization_id",
            "role",
            "outcome",
        }

    # The authoritative DB table is structurally incapable of holding a token (no free-text
    # column at all -- only UUID FKs and short, CHECK-constrained strings), but assert the actual
    # row content anyway rather than only trusting the schema.
    for db_event in _db_security_audit_events(sessions):
        for value in db_event.values():
            if isinstance(value, str):
                assert admin_token not in value
                assert "Bearer" not in value


def test_membership_replay_does_not_produce_a_second_created_event(
    client: TestClient,
    keypair,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
) -> None:
    private_key, _ = keypair
    org = make_provider(organizations)
    admin_identity_id = _bootstrap_identity(client, private_key, "admin-sub")
    _grant_org_admin(client, keypair, organizations, org.id, admin_identity_id, "admin-sub")
    member_identity_id = _bootstrap_identity(client, private_key, "member-sub")

    admin_token = make_token(private_key, subject="admin-sub")
    body = {"identity_id": member_identity_id, "roles": []}
    client.post(
        f"/exchange/v1/organizations/{org.id}/memberships", headers=_auth(admin_token), json=body
    )
    client.post(
        f"/exchange/v1/organizations/{org.id}/memberships", headers=_auth(admin_token), json=body
    )

    # Authoritative audit is now the DB table (DH-EX-002R), not the best-effort file.
    membership_events = [
        e
        for e in _db_security_audit_events(sessions)
        if e["event_type"] == "membership_created"
        and e["subject_identity_id"] == member_identity_id
    ]
    outcomes = [e["outcome"] for e in membership_events]
    assert outcomes.count("created") == 1
    assert outcomes.count("returned_existing") == 1


def test_authorization_refused_is_audited(
    client: TestClient, keypair, organizations: OrganizationRepository, audit_path: Path
) -> None:
    private_key, _ = keypair
    non_admin_token = make_token(private_key, subject="sub-1")
    client.post(
        "/exchange/v1/organizations",
        headers=_auth(non_admin_token),
        json={"name": "X", "organization_type": "PROVIDER"},
    )
    refused = [e for e in _audit_events(audit_path) if e["event_type"] == "authorization_refused"]
    assert len(refused) == 1
    assert refused[0]["outcome"] == "refused"


# -- no wildcard CORS ---------------------------------------------------------------------------


def test_no_cors_headers_are_present(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    token = make_token(private_key, subject="sub-1")
    response = client.get(
        "/exchange/v1/me", headers={**_auth(token), "Origin": "https://attacker.invalid"}
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
