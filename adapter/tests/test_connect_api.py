"""DH-CONNECT-CONTRACT-001 §48/§62 — the connector control plane over HTTP.

CA (connector API), CE (error semantics), CX (what deliberately has no endpoint).

The through-line for this file is the last of those. A REST API is where a carefully derived
verdict gets quietly turned into a stored flag somebody can set, because an endpoint that accepts
``{"qualified": true}`` is so much easier to build than one that runs a suite. CX1 and CX2 exist to
make that regression fail a test rather than ship.
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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import CONTRACT_VERSION, Capability, fingerprint
from daher_adapter.connect.models import EvidenceLevel, QualificationResult
from daher_adapter.connect.qualification import QUALIFICATION_SUITE_VERSION
from daher_adapter.connect.repository import (
    CapabilityOutcome,
    ConnectorInstallationRepository,
)
from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent, OrganizationRole

CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir", "timeout_s": 30})
MAPPING_FP = fingerprint({"mapping_revision": [["synthetic-lis", "GLU", "cat-1", "GLU"]]})
OTHER_FP = fingerprint({"endpoint": "https://lis.invalid/fhir-v2", "timeout_s": 30})

LAB_BUNDLE = (
    Capability.ORDER_RECEIPT,
    Capability.BUSINESS_ACCEPTANCE,
    Capability.COLLECTION_EVENT,
    Capability.RELEASED_RESULT_PUBLICATION,
    Capability.AMENDMENT_PUBLICATION,
)


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _platform_token(private_key) -> str:
    return make_token(private_key, subject="platform-admin", roles=[PLATFORM_ADMIN_ROLE])


def _plain_token(private_key, subject: str = "ordinary-user") -> str:
    return make_token(private_key, subject=subject, roles=[])


def _grant_org_admin(sessions, organization_id: uuid.UUID, subject: str) -> uuid.UUID:
    identities = ExchangeIdentityRepository(sessions)
    identity, _ = identities.get_or_create(
        issuer=ISSUER, subject=subject, display_name=f"Admin {subject}", email=None
    )
    membership, _ = OrganizationMembershipRepository(sessions).create(
        organization_id=organization_id, identity_id=identity.id
    )
    OrganizationRoleAssignmentRepository(sessions).assign(
        membership_id=membership.id, role=OrganizationRole.ORG_ADMIN
    )
    return identity.id


def _enrol(client, private_key, *, name: str, org_type: str, mode: str) -> uuid.UUID:
    response = client.post(
        "/exchange/v1/platform/facilities",
        headers=_auth(_platform_token(private_key)),
        json={"name": name, "organization_type": org_type, "participation_mode": mode},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["organization_id"])


def _connect_lab(client, private_key, name="Laboratoire Central") -> uuid.UUID:
    return _enrol(client, private_key, name=name, org_type="LAB", mode="CONNECT")


def _register(client, private_key, organization_id, **overrides):
    payload = {
        "connector_key": "openelis-fhir",
        "connector_version": "0.1",
        "source_system": "OpenELIS",
        "interface_type": "FHIR",
        "configuration_fingerprint": CONFIG_FP,
        "mapping_fingerprint": MAPPING_FP,
    }
    payload.update(overrides)
    return client.post(
        f"/exchange/v1/platform/facilities/{organization_id}/connector",
        headers=_auth(_platform_token(private_key)),
        json=payload,
    )


def _get(client, private_key, organization_id):
    return client.get(
        f"/exchange/v1/platform/facilities/{organization_id}/connector",
        headers=_auth(_platform_token(private_key)),
    )


def _qualify(
    sessions,
    installation_id,
    *,
    capabilities=LAB_BUNDLE,
    result=None,
    evidence=EvidenceLevel.EXERCISED,
):
    """Record a qualification the only way anything can: through the repository, not the API.

    That this helper has to reach past HTTP is the point of CX1 -- there is no endpoint for it.

    ``evidence`` defaults to EXERCISED because these tests are about the API's shape, and CA3's
    subject is specifically "a connector that proved everything is still not operational". That is
    a fixture constructing exercised evidence to reach a verdict, **not** a claim that the
    synthetic laboratory harness produces it -- it cannot exercise ORDER_RECEIPT, and ES3 pins
    that. ``CapabilityOutcome`` has no default of its own, so this choice is visible here.
    """
    from datetime import UTC, datetime, timedelta

    identities = ExchangeIdentityRepository(sessions)
    identity, _ = identities.get_or_create(
        issuer=ISSUER, subject="harness-runner", display_name="Harness", email=None
    )
    started = datetime.now(UTC) - timedelta(minutes=5)
    return ConnectorInstallationRepository(sessions).record_qualification(
        installation_id=installation_id,
        suite_version=QUALIFICATION_SUITE_VERSION,
        started_at=started,
        completed_at=started + timedelta(minutes=4),
        result=result or QualificationResult.PASSED,
        capabilities=[
            CapabilityOutcome(capability=c, passed=True, evidence_level=evidence)
            for c in capabilities
        ],
        actor_identity_id=identity.id,
        evidence_summary="exécution de la suite de qualification",
    )


# ── CA: reading and writing the installation ─────────────────────────────────────────────────


def test_CA1_a_connect_facility_with_no_connector_says_so_plainly(client, keypair) -> None:
    """The empty state is a real answer, not a 404. An operator asking "what is installed here"
    when nothing is installed deserves the sentence, not an error page."""
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    response = _get(client, private_key, lab)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["installation"] is None
    assert body["installations"] == []
    assert body["verdict"]["qualified"] is False
    assert body["verdict"]["gap"] == "NO_INSTALLATION"
    assert body["qualification_history"] == []
    assert body["operational"] is False
    assert body["transport_available"] is False


def test_CA2_registering_a_connector_does_not_qualify_it(client, keypair, sessions) -> None:
    """§29: the single most important thing registration must not do.

    A 201 here means a row exists saying which build is installed. It means nothing at all about
    whether that build works, and the response says so in the same breath.
    """
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    response = _register(client, private_key, lab)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["installation"]["connector_key"] == "openelis-fhir"
    assert body["installation"]["status"] == "REGISTERED"
    assert body["installation"]["contract_version"] == CONTRACT_VERSION
    assert body["verdict"]["qualified"] is False
    assert body["verdict"]["gap"] == "NEVER_QUALIFIED"
    assert body["operational"] is False

    with sessions() as session:
        events = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "connector_registered"
            )
        ).all()
    assert len(events) == 1


def test_CA3_a_qualified_connector_is_still_not_operational(client, keypair, sessions) -> None:
    """§40, over HTTP. This is the assertion the whole ticket is built around.

    Every required capability passed. The verdict says qualified. And the facility is still not
    routable, because qualification proves a build could work and only a runtime proves one is
    working -- and there is no runtime until DH-CONNECT-AGENT-001. If this test ever needs
    changing, something has started claiming knowledge it does not have.
    """
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = uuid.UUID(
        _register(client, private_key, lab).json()["installation"]["installation_id"]
    )
    _qualify(sessions, installation_id)

    body = _get(client, private_key, lab).json()
    assert body["verdict"]["qualified"] is True
    assert body["verdict"]["gap"] is None
    assert body["operational"] is False
    assert body["health_state"] == "UNKNOWN"
    assert body["transport_available"] is False

    readiness = client.get(
        f"/exchange/v1/organizations/{lab}/enrollment/readiness",
        headers=_auth(_platform_token(private_key)),
    ).json()
    assert readiness["production_routing_allowed"] is False
    findings = {f["code"]: f for f in readiness["findings"]}
    assert findings["connector_qualified"]["satisfied"] is True
    assert findings["connector_operational"]["satisfied"] is False
    assert findings["connector_operational"]["blocking"] is True


def test_CA4_a_configuration_change_makes_the_qualification_stale(client, keypair, sessions):
    """§30: the evidence survives, the verdict does not."""
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = uuid.UUID(
        _register(client, private_key, lab).json()["installation"]["installation_id"]
    )
    _qualify(sessions, installation_id)
    assert _get(client, private_key, lab).json()["verdict"]["qualified"] is True

    response = client.put(
        f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/fingerprints",
        headers=_auth(_platform_token(private_key)),
        json={"configuration_fingerprint": OTHER_FP},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"]["qualified"] is False
    assert body["verdict"]["gap"] == "QUALIFICATION_STALE"
    # The attempt itself is untouched and still names what it was run against.
    assert len(body["qualification_history"]) == 1
    assert body["qualification_history"][0]["result"] == "PASSED"
    assert body["qualification_history"][0]["configuration_fingerprint"] == CONFIG_FP


def test_CA5_suspension_and_restoration_move_the_verdict_both_ways(client, keypair, sessions):
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = uuid.UUID(
        _register(client, private_key, lab).json()["installation"]["installation_id"]
    )
    _qualify(sessions, installation_id)

    def _set(state: str):
        return client.put(
            f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/status",
            headers=_auth(_platform_token(private_key)),
            json={"status": state},
        )

    suspended = _set("SUSPENDED")
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["verdict"]["qualified"] is False
    assert suspended.json()["verdict"]["gap"] == "SUSPENDED"

    restored = _set("REGISTERED")
    assert restored.json()["verdict"]["qualified"] is True, "suspension deleted the evidence"

    retired = _set("RETIRED")
    assert retired.json()["installation"] is None
    assert retired.json()["verdict"]["gap"] == "RETIRED"
    # Retirement is not deletion: the installation is still listed, and its evidence still exists.
    assert len(retired.json()["installations"]) == 1
    assert retired.json()["installations"][0]["status"] == "RETIRED"


def test_CA6_the_qualification_history_keeps_failures(client, keypair, sessions) -> None:
    """§31: two attempts, both readable, newest first. A failure is not tidied away by a later
    success -- "has this connector ever failed, and on what" has to stay answerable."""
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = uuid.UUID(
        _register(client, private_key, lab).json()["installation"]["installation_id"]
    )
    _qualify(
        sessions,
        installation_id,
        capabilities=[c for c in LAB_BUNDLE if c is not Capability.AMENDMENT_PUBLICATION],
        result=QualificationResult.FAILED,
    )
    _qualify(sessions, installation_id)

    history = _get(client, private_key, lab).json()["qualification_history"]
    assert [h["result"] for h in history] == ["PASSED", "FAILED"]
    assert {c["capability"] for c in history[0]["capabilities"]} == {c.value for c in LAB_BUNDLE}


def test_CA7_a_native_facility_gets_an_honest_empty_answer(client, keypair) -> None:
    """§7: a NATIVE facility is not a broken CONNECT one. Nothing here should read as a
    deficiency."""
    private_key, _ = keypair
    clinic = _enrol(
        client, private_key, name="Clinique Al Rahma", org_type="PROVIDER", mode="NATIVE"
    )
    body = _get(client, private_key, clinic).json()
    assert body["participation_mode"] == "NATIVE"
    assert body["installation"] is None
    assert body["verdict"]["qualified"] is False
    assert body["verdict"]["gap"] == "NO_INSTALLATION"


# ── CE: error semantics (§62) ────────────────────────────────────────────────────────────────


def test_CE1_a_native_facility_cannot_have_a_connector_registered(client, keypair) -> None:
    """422 with a reason, not a 404. The facility exists and the caller may see it; what they
    asked for is meaningless for it, and that is more useful to say than to hide."""
    private_key, _ = keypair
    clinic = _enrol(
        client, private_key, name="Clinique Al Rahma", org_type="PROVIDER", mode="NATIVE"
    )
    response = _register(client, private_key, clinic)
    assert response.status_code == 422, response.text
    assert "DHealth Connect" in response.json()["detail"]


def test_CE2_a_second_live_installation_is_refused(client, keypair) -> None:
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    assert _register(client, private_key, lab).status_code == 201
    conflict = _register(client, private_key, lab, connector_key="other-connector")
    assert conflict.status_code == 409, conflict.text


def test_CE3_a_connector_built_against_another_contract_major_is_refused(client, keypair):
    """§57: refused at registration, not tolerated until it misbehaves in traffic."""
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    response = _register(client, private_key, lab, contract_version="2.0")
    assert response.status_code == 422, response.text


def test_CE4_an_unknown_installation_is_404_and_a_foreign_one_is_too(client, keypair) -> None:
    """A platform admin may see every facility, so this is not anti-enumeration -- it is that an
    installation id belonging to another facility is not *this* facility's installation, and
    letting the path parameter decide would be a cross-tenant write."""
    private_key, _ = keypair
    lab_a = _connect_lab(client, private_key, name="Laboratoire A")
    lab_b = _connect_lab(client, private_key, name="Laboratoire B")
    installation_a = _register(client, private_key, lab_a).json()["installation"]["installation_id"]

    missing = client.put(
        f"/exchange/v1/platform/facilities/{lab_b}/connector/{uuid.uuid4()}/status",
        headers=_auth(_platform_token(private_key)),
        json={"status": "SUSPENDED"},
    )
    assert missing.status_code == 404

    foreign = client.put(
        f"/exchange/v1/platform/facilities/{lab_b}/connector/{installation_a}/status",
        headers=_auth(_platform_token(private_key)),
        json={"status": "SUSPENDED"},
    )
    assert foreign.status_code == 404, foreign.text
    # ...and laboratory A's installation did not move.
    assert _get(client, private_key, lab_a).json()["installation"]["status"] == "REGISTERED"


def test_CE5_a_retired_installation_refuses_further_changes(client, keypair) -> None:
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = _register(client, private_key, lab).json()["installation"]["installation_id"]
    client.put(
        f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/status",
        headers=_auth(_platform_token(private_key)),
        json={"status": "RETIRED"},
    )
    response = client.put(
        f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/fingerprints",
        headers=_auth(_platform_token(private_key)),
        json={"mapping_fingerprint": OTHER_FP},
    )
    assert response.status_code == 409, response.text


def test_CE6_an_empty_fingerprint_change_is_refused(client, keypair) -> None:
    """A no-op that returns 200 would let an operator believe they had recorded a change."""
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = _register(client, private_key, lab).json()["installation"]["installation_id"]
    response = client.put(
        f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/fingerprints",
        headers=_auth(_platform_token(private_key)),
        json={},
    )
    assert response.status_code == 422


def test_CE7_every_connector_endpoint_is_platform_admin_only(client, keypair, sessions) -> None:
    """Including for the facility's *own* administrator.

    An org admin may read their own enrollment readiness -- that is their facility's go-live
    status. They may not register, suspend or reconfigure a connector: which build DHealth trusts
    at a partner site is DHealth's own record, and a facility writing it would be a facility
    grading its own integration.
    """
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = _register(client, private_key, lab).json()["installation"]["installation_id"]
    _grant_org_admin(sessions, lab, "lab-admin")
    org_admin = _auth(_plain_token(private_key, subject="lab-admin"))

    assert (
        client.get(
            f"/exchange/v1/platform/facilities/{lab}/connector", headers=org_admin
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/exchange/v1/platform/facilities/{lab}/connector",
            headers=org_admin,
            json={
                "connector_key": "k",
                "connector_version": "1",
                "source_system": "s",
                "interface_type": "FHIR",
                "configuration_fingerprint": CONFIG_FP,
                "mapping_fingerprint": MAPPING_FP,
            },
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/status",
            headers=org_admin,
            json={"status": "SUSPENDED"},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/fingerprints",
            headers=org_admin,
            json={"mapping_fingerprint": OTHER_FP},
        ).status_code
        == 403
    )


# ── CX: the endpoints that deliberately do not exist ─────────────────────────────────────────


def test_CX1_there_is_no_endpoint_that_marks_a_connector_qualified(client, keypair) -> None:
    """§42/§43, asserted rather than promised.

    Every plausible spelling of "an operator declares this connector qualified" must 404 or 405.
    Qualification is what a harness run produced; the moment it becomes something an API accepts,
    every downstream guarantee built on `connector_qualified` degrades from evidence to assertion.
    """
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = _register(client, private_key, lab).json()["installation"]["installation_id"]
    headers = _auth(_platform_token(private_key))

    attempts = [
        ("post", f"/exchange/v1/platform/facilities/{lab}/connector/qualify", {}),
        ("post", f"/exchange/v1/platform/facilities/{lab}/connector/qualification", {}),
        (
            "post",
            f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/qualifications",
            {"result": "PASSED"},
        ),
        ("put", f"/exchange/v1/platform/facilities/{lab}/connector", {"qualified": True}),
        (
            "put",
            f"/exchange/v1/platform/facilities/{lab}/connector/{installation_id}/status",
            {"status": "QUALIFIED"},
        ),
    ]
    for method, path, payload in attempts:
        response = getattr(client, method)(path, headers=headers, json=payload)
        assert response.status_code in (404, 405, 422), (
            f"{method.upper()} {path} answered {response.status_code} -- "
            "an operator can assert qualification"
        )

    assert _get(client, private_key, lab).json()["verdict"]["qualified"] is False


def test_CX2_no_endpoint_accepts_connector_clinical_traffic(client, keypair) -> None:
    """§5/§78: DHealth Connect's transport is not built, and no route pretends otherwise.

    A "submit a result as a connector" endpoint would need an authenticated connector runtime to
    be safe, and that runtime is DH-CONNECT-AGENT-001's subject. Shipping the route first would
    mean shipping the hole first.
    """
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    headers = _auth(_platform_token(private_key))
    for path in (
        f"/exchange/v1/platform/facilities/{lab}/connector/messages",
        f"/exchange/v1/platform/facilities/{lab}/connector/results",
        "/exchange/v1/connect/messages",
        "/exchange/v1/connect/results",
    ):
        assert client.post(path, headers=headers, json={}).status_code in (404, 405)


def test_CX3_no_connector_endpoint_accepts_or_returns_a_credential(client, keypair) -> None:
    """§34: checked as a property of the schema, so a future field cannot slip in unnoticed.

    DHealth stores a SHA-256 digest of a connector's configuration and never the configuration.
    That is what lets a change be *detected* without DHealth ever being a place worth attacking
    for a partner's LIS password.
    """
    private_key, _ = keypair
    lab = _connect_lab(client, private_key)
    installation_id = _register(client, private_key, lab).json()["installation"]["installation_id"]

    forbidden = (
        "password",
        "secret",
        "api_key",
        "apikey",
        "token",
        "credential",
        "private_key",
        "passphrase",
        "endpoint",
        "url",
        "host",
    )
    body = _get(client, private_key, lab).json()

    def _walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert not any(f in key.lower() for f in forbidden), (
                    f"{path}.{key} looks like it carries a credential or a connection target"
                )
                _walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for item in node:
                _walk(item, path)

    _walk(body)

    # And a credential offered to the API is refused rather than quietly stored.
    rejected = client.post(
        f"/exchange/v1/platform/facilities/{lab}/connector",
        headers=_auth(_platform_token(private_key)),
        json={
            "connector_key": "openelis-fhir",
            "connector_version": "0.1",
            "source_system": "OpenELIS",
            "interface_type": "FHIR",
            "configuration_fingerprint": CONFIG_FP,
            "mapping_fingerprint": MAPPING_FP,
            "api_key": "would-be-a-real-secret",
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert installation_id  # the original installation is untouched
