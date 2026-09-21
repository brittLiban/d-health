"""DH-MVP-ONBOARD-001: the facility enrollment control plane over HTTP.

EN (enrollment), FA (first administrator), ID (integration discovery) and TS (tenant security).

The through-line: creating an organization must never imply it is integrated, a partner's claim
about their software must never be recorded as something DHealth verified, and a facility
administrator must never be able to reach another facility's onboarding data.
"""

from __future__ import annotations

import uuid

import pytest
from exchange_auth_support import (
    ISSUER,
    PLATFORM_ADMIN_ROLE,
    build_validator,
    generate_keypair,
    make_token,
)
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent, OrganizationRole
from daher_adapter.exchange.organizations import OrganizationRepository


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, tmp_path) -> TestClient:
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


def _platform_token(private_key) -> str:
    return make_token(private_key, subject="platform-admin", roles=[PLATFORM_ADMIN_ROLE])


def _plain_token(private_key, subject: str = "ordinary-user") -> str:
    return make_token(private_key, subject=subject, roles=[])


def _grant_org_admin(sessions, organization_id: uuid.UUID, subject: str) -> uuid.UUID:
    """Make `subject` an ORG_ADMIN of this organization, and return their identity id."""
    identities = ExchangeIdentityRepository(sessions)
    identity, _ = identities.get_or_create(
        # The same issuer the test token validator asserts, so this membership belongs to the
        # identity the API resolves from the bearer token rather than to a lookalike.
        issuer=ISSUER,
        subject=subject,
        display_name=f"Admin {subject}",
        email=f"{subject}@example.test",
    )
    membership, _ = OrganizationMembershipRepository(sessions).create(
        organization_id=organization_id, identity_id=identity.id
    )
    OrganizationRoleAssignmentRepository(sessions).assign(
        membership_id=membership.id, role=OrganizationRole.ORG_ADMIN
    )
    return identity.id


def _audit(sessions, event_type: str) -> list[ExchangeSecurityAuditEvent]:
    with sessions() as session:
        return list(
            session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.event_type == event_type
                )
            ).all()
        )


def _enrol(client, private_key, *, name: str, org_type: str, mode: str):
    return client.post(
        "/exchange/v1/platform/facilities",
        headers=_auth(_platform_token(private_key)),
        json={"name": name, "organization_type": org_type, "participation_mode": mode},
    )


# ── EN: enrollment ────────────────────────────────────────────────────────────────────────────


def test_EN1_platform_admin_can_enrol_a_provider(client, keypair, sessions) -> None:
    private_key, _ = keypair
    response = _enrol(
        client, private_key, name="Clinique Al Rahma", org_type="PROVIDER", mode="NATIVE"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organization_name"] == "Clinique Al Rahma"
    assert body["organization_type"] == "PROVIDER"
    assert body["participation_mode"] == "NATIVE"
    # DRAFT, not ready: creating a facility is the beginning of enrollment, never the end of it.
    assert body["onboarding_status"] == "DRAFT"
    assert _audit(sessions, "facility_enrollment_created")


def test_EN2_platform_admin_can_enrol_a_lab(client, keypair) -> None:
    private_key, _ = keypair
    response = _enrol(
        client, private_key, name="Laboratoire Central", org_type="LAB", mode="CONNECT"
    )
    assert response.status_code == 201, response.text
    assert response.json()["participation_mode"] == "CONNECT"


def test_EN3_non_platform_user_cannot_enrol_a_facility(client, keypair) -> None:
    private_key, _ = keypair
    response = client.post(
        "/exchange/v1/platform/facilities",
        headers=_auth(_plain_token(private_key)),
        json={"name": "Clinique Pirate", "organization_type": "PROVIDER", "mode": "NATIVE"},
    )
    assert response.status_code in (403, 422)


def test_EN4_organization_type_is_constrained(client, keypair) -> None:
    private_key, _ = keypair
    response = client.post(
        "/exchange/v1/platform/facilities",
        headers=_auth(_platform_token(private_key)),
        json={
            "name": "Pharmacie",
            "organization_type": "PHARMACY",
            "participation_mode": "NATIVE",
        },
    )
    assert response.status_code == 422


def test_EN5_enrolling_is_idempotent_per_organization(client, keypair, organizations, sessions):
    """A second enrollment for one organization must not exist -- the database refuses it, and the
    repository resolves a replay back to the row that already exists rather than erroring."""
    from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository

    lab = make_lab(organizations, name="DoubleSubmitLab")
    enrollments = FacilityEnrollmentRepository(sessions)
    first, created_first = enrollments.create(organization_id=lab.id, participation_mode="NATIVE")
    second, created_second = enrollments.create(
        organization_id=lab.id, participation_mode="CONNECT"
    )
    assert created_first is False or created_second is False
    assert first.id == second.id
    # And the replay did not silently rewrite how this facility participates.
    assert second.participation_mode == first.participation_mode


def test_EN6_participation_mode_is_explicit(client, keypair) -> None:
    private_key, _ = keypair
    response = client.post(
        "/exchange/v1/platform/facilities",
        headers=_auth(_platform_token(private_key)),
        json={"name": "Clinique Sans Mode", "organization_type": "PROVIDER"},
    )
    assert response.status_code == 422, "participation mode must never be inferred"


def test_EN7_participation_mode_change_is_audited(client, keypair, sessions) -> None:
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Clinique Bascule", org_type="PROVIDER", mode="NATIVE"
    ).json()
    response = client.put(
        f"/exchange/v1/organizations/{created['organization_id']}/enrollment/participation-mode",
        headers=_auth(_platform_token(private_key)),
        json={"participation_mode": "CONNECT"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["participation_mode"] == "CONNECT"
    assert _audit(sessions, "facility_participation_mode_changed")


def test_EN8_connect_cannot_be_declared_ready_for_routing(client, keypair) -> None:
    """§10: the refusal that matters most in this ticket.

    No DHealth connector exists. A CONNECT facility declared routable would mean production orders
    sent into nothing, so no request -- however authorized -- can put it in that state.
    """
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Laboratoire Connecté", org_type="LAB", mode="CONNECT"
    ).json()
    response = client.put(
        f"/exchange/v1/organizations/{created['organization_id']}/enrollment/status",
        headers=_auth(_platform_token(private_key)),
        json={"onboarding_status": "READY_FOR_ROUTING"},
    )
    assert response.status_code == 422, response.text
    assert "connector" in response.text.lower()

    # AWAITING_CONNECTOR is the truthful state for the same facility, and it is permitted.
    ok = client.put(
        f"/exchange/v1/organizations/{created['organization_id']}/enrollment/status",
        headers=_auth(_platform_token(private_key)),
        json={"onboarding_status": "AWAITING_CONNECTOR"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["onboarding_status"] == "AWAITING_CONNECTOR"


# ── ID: integration discovery ────────────────────────────────────────────────────────────────


def test_ID1_connect_facility_accepts_discovery(client, keypair) -> None:
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Laboratoire XYZ", org_type="LAB", mode="CONNECT"
    ).json()
    response = client.put(
        f"/exchange/v1/organizations/{created['organization_id']}/integration-profile",
        headers=_auth(_platform_token(private_key)),
        json={
            "existing_system_name": "XYZ LIS",
            "existing_system_vendor": "ABC Systems",
            "existing_system_version": "8.4",
            "hosting_model": "ON_PREMISE",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["existing_system_name"] == "XYZ LIS"
    assert body["hosting_model"] == "ON_PREMISE"


def test_ID2_native_facility_has_no_external_system_to_describe(client, keypair) -> None:
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Laboratoire Natif", org_type="LAB", mode="NATIVE"
    ).json()
    response = client.put(
        f"/exchange/v1/organizations/{created['organization_id']}/integration-profile",
        headers=_auth(_platform_token(private_key)),
        json={"existing_system_name": "Something"},
    )
    assert response.status_code == 422, "a Native facility has no existing system to discover"


def test_ID3_unknown_is_a_valid_answer(client, keypair) -> None:
    """§16: a facility operator may genuinely not know their vendor or hosting. Refusing to record
    that would push an operator into guessing, which is worse than an honest gap."""
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Clinique Incertaine", org_type="PROVIDER", mode="CONNECT"
    ).json()
    response = client.put(
        f"/exchange/v1/organizations/{created['organization_id']}/integration-profile",
        headers=_auth(_platform_token(private_key)),
        json={"hosting_model": "UNKNOWN", "existing_system_vendor": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["hosting_model"] == "UNKNOWN"

    unknown_interface = client.put(
        f"/exchange/v1/organizations/{created['organization_id']}"
        "/integration-profile/interfaces/UNKNOWN",
        headers=_auth(_platform_token(private_key)),
        json={"verification_state": "REPORTED"},
    )
    assert unknown_interface.status_code == 200, unknown_interface.text


def test_ID4_ID5_reported_is_not_verified_and_neither_is_qualified(client, keypair) -> None:
    """The distinction Connect onboarding rests on.

    A partner saying "we have FHIR" is a claim. Recording it must not make the system say DHealth
    verified anything, and verifying it must not make the system say a connector is qualified.
    """
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Laboratoire FHIR", org_type="LAB", mode="CONNECT"
    ).json()
    org_id = created["organization_id"]

    reported = client.put(
        f"/exchange/v1/organizations/{org_id}/integration-profile/interfaces/FHIR",
        headers=_auth(_platform_token(private_key)),
        json={"verification_state": "REPORTED", "detail": "annoncé par le partenaire"},
    )
    assert reported.status_code == 200, reported.text
    interfaces = {i["interface_type"]: i for i in reported.json()["interfaces"]}
    assert interfaces["FHIR"]["verification_state"] == "REPORTED"
    assert reported.json()["onboarding_status"] != "READY_FOR_ROUTING"

    verified = client.put(
        f"/exchange/v1/organizations/{org_id}/integration-profile/interfaces/FHIR",
        headers=_auth(_platform_token(private_key)),
        json={"verification_state": "VERIFIED"},
    )
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert {i["interface_type"]: i for i in body["interfaces"]}["FHIR"][
        "verification_state"
    ] == "VERIFIED"
    # Verifying an interface changes nothing about routing readiness.
    assert body["onboarding_status"] != "READY_FOR_ROUTING"

    readiness = client.get(
        f"/exchange/v1/organizations/{org_id}/enrollment/readiness",
        headers=_auth(_platform_token(private_key)),
    )
    assert readiness.status_code == 200
    assert readiness.json()["production_routing_allowed"] is False
    codes = {f["code"] for f in readiness.json()["findings"] if not f["satisfied"]}
    assert "connector_qualified" in codes


def test_ID6_interface_verification_is_platform_authorized(client, keypair, sessions) -> None:
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Laboratoire Auto-Verif", org_type="LAB", mode="CONNECT"
    ).json()
    org_id = created["organization_id"]
    _grant_org_admin(sessions, uuid.UUID(org_id), "lab-admin-self-verify")

    response = client.put(
        f"/exchange/v1/organizations/{org_id}/integration-profile/interfaces/FHIR",
        headers=_auth(_plain_token(private_key, "lab-admin-self-verify")),
        json={"verification_state": "VERIFIED"},
    )
    assert response.status_code == 403, "a facility cannot mark its own interface verified"


def test_ID7_no_secret_shaped_field_is_accepted_or_returned(client, keypair) -> None:
    """§54: this ticket records discovery metadata, never credentials. A request trying to stash an
    API key in the profile is rejected outright rather than quietly stored in a notes column."""
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Laboratoire Secret", org_type="LAB", mode="CONNECT"
    ).json()
    response = client.put(
        f"/exchange/v1/organizations/{created['organization_id']}/integration-profile",
        headers=_auth(_platform_token(private_key)),
        json={"api_key": "super-secret-value", "existing_system_name": "XYZ"},
    )
    assert response.status_code == 422, "unknown fields must be refused, not absorbed"

    body = _enrol(
        client, private_key, name="Laboratoire Propre", org_type="LAB", mode="CONNECT"
    ).json()
    for forbidden in ("password", "api_key", "secret", "token", "credential"):
        assert forbidden not in str(body).lower()


def test_ID8_not_safely_integrable_preserves_the_organization(client, keypair) -> None:
    """§64: a truthful dead end is not a deletion. The facility stays enrolled, and the reason it
    cannot be integrated is recorded rather than the record being removed."""
    private_key, _ = keypair
    created = _enrol(
        client, private_key, name="Laboratoire Impossible", org_type="LAB", mode="CONNECT"
    ).json()
    org_id = created["organization_id"]

    response = client.put(
        f"/exchange/v1/organizations/{org_id}/integration-profile",
        headers=_auth(_platform_token(private_key)),
        json={"integration_feasibility": "NOT_SAFELY_INTEGRABLE"},
    )
    assert response.status_code == 200, response.text

    blocked = client.post(
        f"/exchange/v1/organizations/{org_id}/enrollment/blockers",
        headers=_auth(_platform_token(private_key)),
        json={
            "code": "REQUIRED_INTERFACE_UNAVAILABLE",
            "note": "aucune interface supportée par l'éditeur actuel",
        },
    )
    assert blocked.status_code == 201, blocked.text

    still_there = client.get(
        f"/exchange/v1/organizations/{org_id}/enrollment",
        headers=_auth(_platform_token(private_key)),
    )
    assert still_there.status_code == 200
    body = still_there.json()
    assert body["integration_feasibility"] == "NOT_SAFELY_INTEGRABLE"
    assert [b["code"] for b in body["open_blockers"]] == ["REQUIRED_INTERFACE_UNAVAILABLE"]


# ── FA: first administrator ──────────────────────────────────────────────────────────────────


def test_FA1_platform_admin_can_find_an_authenticated_identity(client, keypair, sessions) -> None:
    private_key, _ = keypair
    identities = ExchangeIdentityRepository(sessions)
    identities.get_or_create(
        issuer="https://issuer.example",
        subject="future-admin",
        display_name="Fatouma Robleh",
        email="fatouma@example.test",
    )
    response = client.get(
        "/exchange/v1/platform/identities",
        headers=_auth(_platform_token(private_key)),
        params={"q": "Fatouma"},
    )
    assert response.status_code == 200, response.text
    results = response.json()
    assert [r["display_name"] for r in results] == ["Fatouma Robleh"]
    # Nothing beyond what an operator needs to recognise the person.
    assert set(results[0]) == {"identity_id", "display_name", "email"}


def test_FA2_ordinary_user_cannot_search_identities(client, keypair, sessions) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations=OrganizationRepository(sessions), name="FA2 Clinic")
    _grant_org_admin(sessions, provider.id, "fa2-org-admin")
    response = client.get(
        "/exchange/v1/platform/identities",
        headers=_auth(_plain_token(private_key, "fa2-org-admin")),
        params={"q": "Fatouma"},
    )
    assert response.status_code == 403


def test_FA6_an_email_string_alone_grants_nothing(client, keypair, sessions) -> None:
    """§21: finding somebody is not attaching them, and an email address is not an authorization.

    The lookup returns a person; their membership list is unchanged until a separate explicit
    mutation creates one.
    """
    private_key, _ = keypair
    identities = ExchangeIdentityRepository(sessions)
    identity, _ = identities.get_or_create(
        issuer="https://issuer.example",
        subject="lookup-only",
        display_name="Ahmed Osman",
        email="ahmed@example.test",
    )
    response = client.get(
        "/exchange/v1/platform/identities",
        headers=_auth(_platform_token(private_key)),
        params={"q": "ahmed@example.test"},
    )
    assert response.status_code == 200
    assert response.json()[0]["identity_id"] == str(identity.id)

    memberships = OrganizationMembershipRepository(sessions).list_for_identity(identity.id)
    assert memberships == [], "a lookup must never create access"


def test_FA_lookup_requires_a_substantial_query(client, keypair) -> None:
    """Bounded so the endpoint cannot be walked as an enumeration of everyone who has signed in."""
    private_key, _ = keypair
    response = client.get(
        "/exchange/v1/platform/identities",
        headers=_auth(_platform_token(private_key)),
        params={"q": "a"},
    )
    assert response.status_code == 422


# ── TS: tenant security ──────────────────────────────────────────────────────────────────────


def test_TS1_TS4_facility_admin_cannot_read_another_facilitys_enrollment(
    client, keypair, sessions
) -> None:
    private_key, _ = keypair
    mine = _enrol(
        client, private_key, name="Clinique Mienne", org_type="PROVIDER", mode="NATIVE"
    ).json()
    theirs = _enrol(
        client, private_key, name="Clinique Autre", org_type="PROVIDER", mode="NATIVE"
    ).json()
    _grant_org_admin(sessions, uuid.UUID(mine["organization_id"]), "ts1-admin")
    token = _plain_token(private_key, "ts1-admin")

    own = client.get(
        f"/exchange/v1/organizations/{mine['organization_id']}/enrollment", headers=_auth(token)
    )
    assert own.status_code == 200, "an administrator may read their own facility"

    other = client.get(
        f"/exchange/v1/organizations/{theirs['organization_id']}/enrollment", headers=_auth(token)
    )
    # 404 rather than 403, following this API's existing anti-enumeration convention: an outsider
    # is told the organization does not exist, so uuids cannot be walked to learn which facilities
    # are enrolled. Either way the onboarding data is unreachable, which is the property that
    # matters.
    assert other.status_code == 404, "and nobody else's"
    assert "Clinique Autre" not in other.text


def test_TS2_TS3_facility_admin_cannot_change_another_facilitys_participation(
    client, keypair, sessions
) -> None:
    private_key, _ = keypair
    provider = _enrol(
        client, private_key, name="Clinique TS2", org_type="PROVIDER", mode="NATIVE"
    ).json()
    lab = _enrol(client, private_key, name="Labo TS2", org_type="LAB", mode="NATIVE").json()
    _grant_org_admin(sessions, uuid.UUID(provider["organization_id"]), "ts2-provider-admin")
    token = _plain_token(private_key, "ts2-provider-admin")

    response = client.put(
        f"/exchange/v1/organizations/{lab['organization_id']}/enrollment/participation-mode",
        headers=_auth(token),
        json={"participation_mode": "CONNECT"},
    )
    assert response.status_code == 403, "a provider admin must not administer a lab"


def test_TS6_facility_admin_cannot_configure_a_cross_organization_route(
    client, keypair, sessions
) -> None:
    private_key, _ = keypair
    provider = _enrol(
        client, private_key, name="Clinique TS6", org_type="PROVIDER", mode="NATIVE"
    ).json()
    lab = _enrol(client, private_key, name="Labo TS6", org_type="LAB", mode="NATIVE").json()
    _grant_org_admin(sessions, uuid.UUID(provider["organization_id"]), "ts6-admin")

    response = client.put(
        f"/exchange/v1/organizations/{provider['organization_id']}"
        f"/lab-routes/{lab['organization_id']}",
        headers=_auth(_plain_token(private_key, "ts6-admin")),
        json={"active": True},
    )
    assert response.status_code == 403, "routing spans two organizations and stays platform-level"


# ── RT: the platform's view of configured routes ─────────────────────────────────────────────


def test_RT1_platform_route_listing_includes_inactive_routes(client, keypair, sessions) -> None:
    """A platform operator configuring a relationship must see the ones switched off.

    The provider-scoped listing returns active routes only -- a clinician's directory is where
    they may send work today. This one answers a different question: which relationships exist.
    """
    private_key, _ = keypair
    provider = _enrol(
        client, private_key, name="Clinique RT1", org_type="PROVIDER", mode="NATIVE"
    ).json()
    lab = _enrol(client, private_key, name="Labo RT1", org_type="LAB", mode="NATIVE").json()

    configured = client.put(
        f"/exchange/v1/organizations/{provider['organization_id']}"
        f"/lab-routes/{lab['organization_id']}",
        headers=_auth(_platform_token(private_key)),
        json={"active": False},
    )
    assert configured.status_code in (200, 201), configured.text

    listed = client.get(
        f"/exchange/v1/platform/facilities/{provider['organization_id']}/lab-routes",
        headers=_auth(_platform_token(private_key)),
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [(r["lab_organization_id"], r["active"]) for r in rows] == [
        (lab["organization_id"], False)
    ]
    assert rows[0]["lab_organization_name"] == "Labo RT1"


def test_RT2_a_facility_admin_cannot_read_the_platform_route_listing(
    client, keypair, sessions
) -> None:
    # This read spans two organizations. Holding ORG_ADMIN at the provider is not authority over
    # the network, and the platform gate is what says so -- not the absence of a button.
    private_key, _ = keypair
    provider = _enrol(
        client, private_key, name="Clinique RT2", org_type="PROVIDER", mode="NATIVE"
    ).json()
    _grant_org_admin(sessions, uuid.UUID(provider["organization_id"]), "rt2-admin")

    refused = client.get(
        f"/exchange/v1/platform/facilities/{provider['organization_id']}/lab-routes",
        headers=_auth(_plain_token(private_key, "rt2-admin")),
    )
    assert refused.status_code == 403, refused.text


def test_RT3_an_unknown_organization_is_404_not_an_empty_list(client, keypair) -> None:
    # An empty list would say "this provider has no routes", which is a true-sounding answer to a
    # question about an organization that does not exist.
    private_key, _ = keypair
    missing = client.get(
        f"/exchange/v1/platform/facilities/{uuid.uuid4()}/lab-routes",
        headers=_auth(_platform_token(private_key)),
    )
    assert missing.status_code == 404, missing.text
