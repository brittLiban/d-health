"""DHEALTH-FRONT-DESK-001/ADR-069: walk-in arrival, the waiting queue, and the clinician handoff.

Driven over HTTP through ``build_exchange_router``, the discipline DHEALTH-CLINIC-VISIT-001's suite
established and this one inherits: the claim being earned is *"a facility worker with the right
capabilities can check a patient in"*, and a test that called ``VisitRepository`` directly would
prove the domain logic while proving nothing about whether that worker can reach it.

── The persona that makes this suite mean something ──

``reception`` holds **no role template at all**. It is an ACTIVE membership with three direct
capability grants -- ``patient.directory.read``, ``patient.register``, ``visit.check_in`` -- and
nothing else. That is the whole architectural claim of ADR-069 expressed as a fixture: if the
workflow needed a ``FRONT_DESK`` role, this persona could not complete a single step, and if the
authority boundaries were role-shaped, this persona would be able to read a clinical note.

``admin_reception`` is the §29 composition: ``ORG_ADMIN`` plus a direct ``visit.check_in`` grant, no
role change. ``clerk_reception`` is §28's: ``BILLING_CLERK`` plus the two patient capabilities plus
``visit.check_in``, which is one employee doing reception and the till.

── What is deliberately NOT tested here, because it must not exist ──

There is no waiting-duration assertion and no ordering-by-urgency assertion, because neither is
built. ``AGENTS.md`` (2026-09-27) and ``docs/NATIVE-OPERATING-PRODUCT.md`` §38 forbid deriving a
waiting time from visit timestamps without the clinical lead's sign-off.
``test_the_waiting_projection_publishes_no_derived_waiting_time`` asserts the *absence*, so the rule
is pinned rather than merely observed.

The real-PostgreSQL concurrency half is
``tests/integration/test_dhealthfrontdesk001_postgres_qualification.py``. Every property this file
cannot observe -- the row lock is a no-op on SQLite, and ``PRAGMA foreign_keys`` is off here -- is
asserted there instead of being claimed here.

All patient data is synthetic. No real patient data is used anywhere in this file.
"""

from __future__ import annotations

import uuid

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import WaitingArrivalResponse, build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.capabilities import (
    CAPABILITY_BY_KEY,
    SYSTEM_ROLE_TEMPLATES,
    Capability,
)
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    OrganizationRole,
    OrganizationType,
    Visit,
    VisitStatus,
)
from daher_adapter.exchange.organizations import OrganizationRepository

RECEPTION_CAPABILITIES = (
    Capability.PATIENT_DIRECTORY_READ,
    Capability.PATIENT_REGISTER,
    Capability.VISIT_CHECK_IN,
)


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def audit_path(tmp_path):
    return tmp_path / "exchange-audit.jsonl"


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, audit_path) -> TestClient:
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


# -- harness --------------------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _key() -> str:
    return str(uuid.uuid4())


def _bootstrap(client: TestClient, private_key, subject: str, **kwargs) -> tuple[str, str]:
    token = make_token(private_key, subject=subject, **kwargs)
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


def _set(client, actor, organization_id, membership_id, capability, effect):
    """Grant or deny one capability, exactly as an administrator does through Team & Access."""
    return client.put(
        f"/exchange/v1/organizations/{organization_id}"
        f"/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(actor),
        json={"effect": effect},
    )


def _clear(client, actor, organization_id, membership_id, capability):
    return client.delete(
        f"/exchange/v1/organizations/{organization_id}"
        f"/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(actor),
    )


def _register_patient(client, token, provider_id, display_name, dob="1994-03-14"):
    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/patients",
        headers={**_auth(token), "Idempotency-Key": _key()},
        json={"display_name": display_name, "date_of_birth": dob},
    )
    assert response.status_code in (200, 201), response.text
    entry = response.json()
    # The directory response names it `patient_id`; every visit call site here wants `id`. The same
    # normalisation DHEALTH-CLINIC-VISIT-001's suite makes, for the same reason.
    return {**entry, "id": entry["patient_id"]}


def _check_in(client, token, provider_id, patient_id, *, key=None, reason=None):
    body: dict = {"patient_id": patient_id}
    if reason is not None:
        body["reason_for_visit"] = reason
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/visits",
        headers={**_auth(token), "Idempotency-Key": key or _key()},
        json=body,
    )


def _waiting(client, token, provider_id):
    return client.get(
        f"/exchange/v1/organizations/{provider_id}/visits/waiting", headers=_auth(token)
    )


def _visits_in_db(sessions, world) -> list[Visit]:
    """Every visit row at this provider, read straight from the database.

    Used instead of the list endpoint for the counting assertions, because several of those
    assertions are about a persona the list endpoint deliberately refuses -- a count taken through a
    403 would pass for the wrong reason, which is the failure shape `AGENTS.md`'s evidence
    discipline names."""
    with sessions() as session:
        return list(
            session.scalars(
                select(Visit).where(
                    Visit.provider_organization_id == uuid.UUID(world["provider_id"])
                )
            ).all()
        )


def _visit_row(sessions, visit_id: str) -> Visit:
    with sessions() as session:
        visit = session.get(Visit, uuid.UUID(visit_id))
        assert visit is not None
        return visit


@pytest.fixture
def world(client: TestClient, keypair, organizations: OrganizationRepository):
    """One provider with every persona ADR-069's proofs need, one rival provider, one laboratory.

    The personas, and why each exists:

    * ``reception`` -- **no role template**, three direct grants. The persona the whole ticket is
      about. If any assertion here needed a role, the architecture would be wrong.
    * ``clinician`` -- ``CLINICIAN``, which since ADR-069 carries ``visit.check_in`` too (§5).
      Proves the split did not take away an authority a clinician already had.
    * ``admin_reception`` -- ``ORG_ADMIN`` + a direct ``visit.check_in`` grant (§29).
    * ``plain_admin`` -- ``ORG_ADMIN`` only. Proves administration does not imply check-in.
    * ``clerk_reception`` -- ``BILLING_CLERK`` + the reception three (§28).
    * ``reception_z`` -- the same reception composition at the *rival* provider, which is what makes
      the cross-tenant assertions real rather than self-referential.
    * ``tech`` -- ``LAB_TECH`` at the laboratory (§34).
    * ``outsider`` -- authenticated, member of nothing. ``platform_token`` -- the realm role, which
      must not open a clinical or arrival door.
    """
    private_key, _ = keypair
    platform_token = make_token(private_key, subject="platform", roles=[PLATFORM_ADMIN_ROLE])

    provider = make_provider(organizations, "Clinique A")
    other = make_provider(organizations, "Clinique Z")
    lab = make_lab(organizations, "Laboratoire B")

    admin_id, admin_token = _bootstrap(client, private_key, "admin-a", name="Admin A")
    admin_z_id, admin_z_token = _bootstrap(client, private_key, "admin-z", name="Admin Z")
    admin_b_id, admin_b_token = _bootstrap(client, private_key, "admin-b", name="Admin B")
    reception_id, reception_token = _bootstrap(client, private_key, "recep", name="Accueil A")
    reception_z_id, reception_z_token = _bootstrap(client, private_key, "recep-z", name="Accueil Z")
    clinician_id, clinician_token = _bootstrap(client, private_key, "clin", name="Clinicienne C")
    covering_id, covering_token = _bootstrap(client, private_key, "cover", name="Docteur Couvrant")
    admin_recep_id, admin_recep_token = _bootstrap(
        client, private_key, "adm-r", name="Admin Accueil"
    )
    plain_admin_id, plain_admin_token = _bootstrap(client, private_key, "adm-p", name="Admin Seul")
    clerk_id, clerk_token = _bootstrap(client, private_key, "clerk", name="Caissiere")
    tech_id, tech_token = _bootstrap(client, private_key, "tech", name="Technicien L")
    _outsider_id, outsider_token = _bootstrap(client, private_key, "out", name="Dehors")

    _attach(client, platform_token, str(provider.id), admin_id, ["ORG_ADMIN"])
    _attach(client, platform_token, str(other.id), admin_z_id, ["ORG_ADMIN"])
    _attach(client, platform_token, str(lab.id), admin_b_id, ["ORG_ADMIN"])

    # The reception member gets **no role**, then three direct grants. This is the fixture that
    # would be impossible if the feature depended on a job title.
    reception_membership = _attach(client, admin_token, str(provider.id), reception_id, [])
    for capability in RECEPTION_CAPABILITIES:
        granted = _set(
            client,
            admin_token,
            str(provider.id),
            reception_membership,
            capability.value,
            "GRANT",
        )
        assert granted.status_code == 200, granted.text

    reception_z_membership = _attach(client, admin_z_token, str(other.id), reception_z_id, [])
    for capability in RECEPTION_CAPABILITIES:
        _set(
            client,
            admin_z_token,
            str(other.id),
            reception_z_membership,
            capability.value,
            "GRANT",
        )

    clinician_membership = _attach(
        client, admin_token, str(provider.id), clinician_id, ["CLINICIAN"]
    )
    _attach(client, admin_token, str(provider.id), covering_id, ["CLINICIAN"])

    admin_recep_membership = _attach(
        client, admin_token, str(provider.id), admin_recep_id, ["ORG_ADMIN"]
    )
    _set(
        client,
        admin_token,
        str(provider.id),
        admin_recep_membership,
        Capability.VISIT_CHECK_IN.value,
        "GRANT",
    )
    _attach(client, admin_token, str(provider.id), plain_admin_id, ["ORG_ADMIN"])

    clerk_membership = _attach(client, admin_token, str(provider.id), clerk_id, ["BILLING_CLERK"])
    for capability in RECEPTION_CAPABILITIES:
        _set(client, admin_token, str(provider.id), clerk_membership, capability.value, "GRANT")

    _attach(client, admin_b_token, str(lab.id), tech_id, ["LAB_TECH"])

    patient = _register_patient(client, reception_token, str(provider.id), "Fatou Hassan")
    patient_z = _register_patient(client, reception_z_token, str(other.id), "Patient Z")

    return {
        "provider_id": str(provider.id),
        "other_provider_id": str(other.id),
        "lab_id": str(lab.id),
        "admin_token": admin_token,
        "admin_z_token": admin_z_token,
        "reception_token": reception_token,
        "reception_id": reception_id,
        "reception_membership": reception_membership,
        "reception_z_token": reception_z_token,
        "clinician_token": clinician_token,
        "clinician_id": clinician_id,
        "clinician_membership": clinician_membership,
        "covering_token": covering_token,
        "admin_recep_token": admin_recep_token,
        "admin_recep_membership": admin_recep_membership,
        "plain_admin_token": plain_admin_token,
        "clerk_token": clerk_token,
        "clerk_membership": clerk_membership,
        "tech_token": tech_token,
        "outsider_token": outsider_token,
        "platform_token": platform_token,
        "patient_id": patient["id"],
        "patient_z_id": patient_z["id"],
    }


# ── the capability itself ────────────────────────────────────────────────────────────────────────


def test_visit_check_in_is_centrally_catalogued_and_provider_only():
    """§4/§67: one vocabulary, and the capability applies to providers, not laboratories.

    The catalog entry is not decoration -- ``capabilities.py`` refuses to import without one, and
    ``grantable_capabilities`` reads ``organization_types`` to decide what an administrator may
    assign. A laboratory runs no outpatient waiting room, so granting this there would grant
    something inert, and the resolver's type filter would silently drop it."""
    definition = CAPABILITY_BY_KEY[Capability.VISIT_CHECK_IN]
    assert definition.organization_types == frozenset({OrganizationType.PROVIDER})
    assert definition.group == "Visites"
    # French, and about the work rather than the job title.
    assert "arriv" in definition.description.lower()


def test_the_clinician_template_keeps_the_arrival_authority_it_already_had():
    """§5: compatibility, not authority expansion.

    Registering an arrival was part of ``visit.document`` before ADR-069. Splitting it out without
    adding it to the template would have silently removed an authority every clinician already used
    -- the exact drift DHEALTH-ACCESS-CAPABILITIES-001 §18 calls a P0."""
    clinician = SYSTEM_ROLE_TEMPLATES[OrganizationRole.CLINICIAN]
    assert Capability.VISIT_CHECK_IN in clinician
    assert Capability.VISIT_DOCUMENT in clinician


def test_no_role_template_bundles_check_in_with_lab_or_billing_authority():
    """§34/§28/§65: there is no shared "staff" super-role, and no ``FRONT_DESK`` template at all.

    Asserted over the templates rather than over one persona's response, because the failure this
    catches is somebody later adding ``visit.check_in`` to a convenience bundle that also carries
    laboratory or financial authority."""
    for role, capabilities in SYSTEM_ROLE_TEMPLATES.items():
        if Capability.VISIT_CHECK_IN not in capabilities:
            continue
        assert role is OrganizationRole.CLINICIAN, (
            f"{role.value} bundles visit.check_in; only CLINICIAN may, for §5 compatibility"
        )
        assert Capability.LAB_QUEUE_WORK not in capabilities
        assert Capability.LAB_RESULT_RELEASE not in capabilities
        assert Capability.BILLING_OPERATE not in capabilities

    # And no template named for a front desk exists -- §6/§65: the capability ships grantable, the
    # role does not ship at all, which is what proves the architecture rather than the bundle.
    assert not any("FRONT" in role.value for role in SYSTEM_ROLE_TEMPLATES)


# ── FD-AUTH: who may check in ────────────────────────────────────────────────────────────────────


def test_fd_auth_1_an_unauthenticated_caller_cannot_check_in(client, world):
    response = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers={"Idempotency-Key": _key()},
        json={"patient_id": world["patient_id"]},
    )
    assert response.status_code == 401, response.text


def test_fd_auth_2_an_active_member_without_the_capability_cannot_check_in(client, world, sessions):
    """``plain_admin`` is an ACTIVE ``ORG_ADMIN`` of this very organization. Belonging is not
    authority (§29)."""
    response = _check_in(
        client, world["plain_admin_token"], world["provider_id"], world["patient_id"]
    )
    assert response.status_code == 403, response.text
    assert not _visits_in_db(sessions, world)


def test_fd_auth_3_a_direct_grant_permits_check_in_with_no_role_change(client, world):
    """§26 steps 1-8, over HTTP: the member holds **no role template** and can run the arrival.

    This is the single most important assertion in the suite. It passes only if the endpoint asks a
    capability question; a role check of any shape makes it fail."""
    response = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == VisitStatus.CHECKED_IN.value
    # ...and the person still holds no role, which is what "without a role change" means.
    access = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}"
        f"/memberships/{world['reception_membership']}/access",
        headers=_auth(world["admin_token"]),
    )
    assert access.json()["roles"] == []


def test_fd_auth_4_same_token_revocation_refuses_the_next_request(client, world):
    """§26 steps 10-12: the capability is removed, the Keycloak session is untouched, and the very
    next request with the **same** token is refused.

    This is what makes the authority live rather than a login-time claim. NC-FD-7 breaks it by
    caching from the token."""
    first = _check_in(client, world["reception_token"], world["provider_id"], world["patient_id"])
    assert first.status_code == 201

    cleared = _clear(
        client,
        world["admin_token"],
        world["provider_id"],
        world["reception_membership"],
        Capability.VISIT_CHECK_IN.value,
    )
    assert cleared.status_code == 200, cleared.text

    # Registered by the clinician, not the administrator: `ORG_ADMIN`'s template carries no
    # `patient.register`, and since ADR-069 narrowed `require_clinician` it no longer passes that
    # gate by holding some other provider capability. The refusal is correct, so the fixture uses a
    # persona that legitimately holds the authority.
    second_patient = _register_patient(
        client, world["clinician_token"], world["provider_id"], "Autre Patient"
    )
    # Same token object, no re-login, no refresh.
    refused = _check_in(
        client, world["reception_token"], world["provider_id"], second_patient["id"]
    )
    assert refused.status_code == 403, refused.text


def test_fd_auth_5_an_explicit_deny_overrides_an_inherited_grant(client, world):
    """§27: the ``CLINICIAN`` role stays assigned, a member-specific DENY is added, and the
    authority disappears. Removing the DENY brings it back. **No feature code changes.**"""
    provider, membership = world["provider_id"], world["clinician_membership"]

    first = _check_in(client, world["clinician_token"], provider, world["patient_id"])
    assert first.status_code == 201, first.text

    denied = _set(
        client,
        world["admin_token"],
        provider,
        membership,
        Capability.VISIT_CHECK_IN.value,
        "DENY",
    )
    assert denied.status_code == 200, denied.text

    other = _register_patient(client, world["clinician_token"], provider, "Patient Deux")
    assert _check_in(client, world["clinician_token"], provider, other["id"]).status_code == 403

    # The role never moved.
    access = client.get(
        f"/exchange/v1/organizations/{provider}/memberships/{membership}/access",
        headers=_auth(world["admin_token"]),
    )
    assert access.json()["roles"] == ["CLINICIAN"]

    restored = _clear(
        client, world["admin_token"], provider, membership, Capability.VISIT_CHECK_IN.value
    )
    assert restored.status_code == 200, restored.text
    assert _check_in(client, world["clinician_token"], provider, other["id"]).status_code == 201


def test_fd_auth_6_clinic_a_cannot_check_in_clinic_b_patient(client, world, sessions):
    """§32: a guessed-but-real patient UUID from another tenant is a 404, not a cross-org visit.

    404 rather than 403 on purpose -- the anti-enumeration rule this package applies everywhere:
    confirming that the id is real would itself be the leak."""
    response = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_z_id"]
    )
    assert response.status_code == 404, response.text
    assert not _visits_in_db(sessions, world)


def test_fd_auth_6b_reception_cannot_reach_the_other_organizations_waiting_queue(client, world):
    """§32, the read direction. ``reception`` has a real, identical membership at Clinique A and no
    claim at all on Clinique Z, so Z's queue is a 404."""
    _check_in(client, world["reception_z_token"], world["other_provider_id"], world["patient_z_id"])
    refused = _waiting(client, world["reception_token"], world["other_provider_id"])
    assert refused.status_code == 404, refused.text

    # ...and Z's arrival never appears in A's queue.
    _check_in(client, world["reception_token"], world["provider_id"], world["patient_id"])
    mine = _waiting(client, world["reception_token"], world["provider_id"])
    assert [row["patient_id"] for row in mine.json()] == [world["patient_id"]]


def test_fd_auth_7_check_in_authority_cannot_read_the_clinical_visit_note(client, world):
    """§12/§33 -- **one of the most important tests in the ticket.**

    ``reception`` created this very visit. Creating it does not grant reading it: the visit detail
    endpoint carries the note history and the stated reason, so it is behind ``visit.read``, which
    this persona does not hold. NC-FD-3 breaks the projection; this breaks if the *gate* is
    wrong."""
    created = _check_in(client, world["reception_token"], world["provider_id"], world["patient_id"])
    visit_id = created.json()["id"]

    detail = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}",
        headers=_auth(world["reception_token"]),
    )
    assert detail.status_code == 403, detail.text

    # The visit *list* carries `reason_for_visit` too, so it is refused for the same reason.
    listing = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers=_auth(world["reception_token"]),
    )
    assert listing.status_code == 403, listing.text

    # And the patient's own visit history, which is a clinical read about a person.
    history = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/patients/{world['patient_id']}/visits",
        headers=_auth(world["reception_token"]),
    )
    assert history.status_code == 403, history.text


def test_fd_auth_8_check_in_authority_cannot_start_the_consultation(client, world, sessions):
    """§11/§33: ``CHECKED_IN`` -> ``IN_PROGRESS`` needs ``visit.document``. NC-FD-6 allows it.

    This is the boundary that stops a desk worker becoming the clinician of record."""
    visit_id = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    ).json()["id"]

    refused = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}/take",
        headers=_auth(world["reception_token"]),
    )
    assert refused.status_code == 403, refused.text
    assert _visit_row(sessions, visit_id).status == VisitStatus.CHECKED_IN.value
    assert _visit_row(sessions, visit_id).clinician_identity_id is None


def test_fd_auth_9_check_in_authority_cannot_complete_or_document_a_visit(client, world, sessions):
    """§33: completion and documentation are ``visit.document``, and so is amending the reason.

    The reason specifically: it is the field a desk worker might plausibly be handed, and §12 says
    they may not edit it. Refused, and the visit's own stored reason is unchanged."""
    visit_id = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    ).json()["id"]
    client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}/take",
        headers=_auth(world["clinician_token"]),
    )

    base = f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}"
    completed = client.post(f"{base}/complete", headers=_auth(world["reception_token"]))
    assert completed.status_code == 403, completed.text
    noted = client.post(
        f"{base}/notes", headers=_auth(world["reception_token"]), json={"body": "tension 12/8"}
    )
    assert noted.status_code == 403, noted.text
    amended = client.put(
        f"{base}/reason",
        headers=_auth(world["reception_token"]),
        json={"reason_for_visit": "douleur thoracique"},
    )
    assert amended.status_code == 403, amended.text
    assert _visit_row(sessions, visit_id).reason_for_visit is None


def test_fd_auth_10_check_in_authority_does_not_imply_billing(client, world):
    """§28, both directions, with no billing semantics touched.

    ``reception`` holds the three arrival capabilities and is refused the till. ``clerk_reception``
    holds ``billing.operate`` as well and is not -- one employee, both jobs, no combination role."""
    invoices = f"/exchange/v1/organizations/{world['provider_id']}/billing/invoices"
    assert client.get(invoices, headers=_auth(world["reception_token"])).status_code == 403
    assert client.get(invoices, headers=_auth(world["clerk_token"])).status_code == 200

    # ...and the clerk's reception authority is real, not incidental to holding BILLING_CLERK.
    arrived = _check_in(client, world["clerk_token"], world["provider_id"], world["patient_id"])
    assert arrived.status_code == 201, arrived.text

    # Remove billing.operate: reception keeps working, the till stops. §28's final step.
    _set(
        client,
        world["admin_token"],
        world["provider_id"],
        world["clerk_membership"],
        Capability.BILLING_OPERATE.value,
        "DENY",
    )
    assert client.get(invoices, headers=_auth(world["clerk_token"])).status_code == 403
    assert _waiting(client, world["clerk_token"], world["provider_id"]).status_code == 200


def test_fd_auth_11_check_in_authority_does_not_imply_lab_authority(client, world):
    """§34: no shared staff super-role. Reception has no claim on the laboratory at all, so the
    laboratory's queue is a 404 (anti-enumeration), never an empty list."""
    refused = client.get(
        f"/exchange/v1/organizations/{world['lab_id']}/lab-queue",
        headers=_auth(world["reception_token"]),
    )
    assert refused.status_code == 404, refused.text

    # And the laboratory's own technician cannot check anybody in at the provider.
    assert (
        _check_in(
            client, world["tech_token"], world["provider_id"], world["patient_id"]
        ).status_code
        == 404
    )


def test_fd_auth_11b_check_in_authority_does_not_imply_result_review(client, world):
    """§35: arrival authority is not ``result.review``, and no released value is reachable."""
    refused = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/result-reviews",
        headers=_auth(world["reception_token"]),
    )
    assert refused.status_code == 403, refused.text


def test_fd_auth_11c_check_in_authority_does_not_imply_diagnostic_ordering(client, world):
    """§33: reception cannot raise a diagnostic order.

    Gated on ``order.manage``, which reception does not hold. Before ADR-069 this would have passed
    -- ``require_clinician`` accepted ``patient.directory.read``, which reception legitimately has
    -- which is why narrowing that gate was part of this ticket rather than a tidy-up."""
    refused = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/order-intents",
        headers={**_auth(world["reception_token"]), "Idempotency-Key": _key()},
        # A **well-formed** body on purpose. FastAPI validates the body before the handler runs, so
        # a malformed one would produce a 422 and the test would pass without the gate ever being
        # consulted -- green for a reason unrelated to what it claims to check.
        json={
            "patient_reference_id": str(uuid.uuid4()),
            "catalog_entry_ids": [str(uuid.uuid4())],
        },
    )
    assert refused.status_code == 403, refused.text


def test_fd_auth_12_org_admin_alone_behaves_by_capability_not_job_title(client, world):
    """§29: ``ORG_ADMIN`` does **not** receive check-in authority automatically; ``ORG_ADMIN``
    plus a direct grant does, with no role change.

    The two personas differ by exactly one override row, which is the point: administration and
    reception are independent jobs, and "an admin can always check in" is nowhere in the code."""
    assert (
        _check_in(
            client, world["plain_admin_token"], world["provider_id"], world["patient_id"]
        ).status_code
        == 403
    )

    assert (
        _check_in(
            client, world["admin_recep_token"], world["provider_id"], world["patient_id"]
        ).status_code
        == 201
    )

    access = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}"
        f"/memberships/{world['admin_recep_membership']}/access",
        headers=_auth(world["admin_token"]),
    )
    assert access.json()["roles"] == ["ORG_ADMIN"]


def test_a_platform_administrator_cannot_check_a_patient_in(client, world):
    """ADR-063, applied to the new endpoint: a network administrator is not facility staff.

    Worth its own test because the gate helper takes a flag that could default the other way, and
    the arrival endpoint is the newest place that decision could be got wrong."""
    refused = _check_in(client, world["platform_token"], world["provider_id"], world["patient_id"])
    assert refused.status_code == 403, refused.text
    assert _waiting(client, world["platform_token"], world["provider_id"]).status_code == 403


# ── FD: the workflow ─────────────────────────────────────────────────────────────────────────────


def test_fd_1_an_existing_patient_is_checked_in_to_exactly_one_visit(client, world, sessions):
    """§13/§67: identify, confirm, done -- and one durable visit."""
    response = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == VisitStatus.CHECKED_IN.value
    assert body["clinician_identity_id"] is None, "the desk is not the treating clinician"
    assert body["reason_for_visit"] is None, "nothing clinical was collected at reception"
    assert body["started_at"] is None
    assert len(_visits_in_db(sessions, world)) == 1


def test_fd_2_a_newly_registered_patient_flows_straight_into_check_in(client, world):
    """§14: search, not found, register, check in -- **no second search**, law 2 (no double entry).

    Expressed as the API contract that makes it possible: registration returns the patient id, and
    check-in takes exactly that id with no re-lookup. If registration returned only a confirmation,
    the interface would have no way to avoid the second search."""
    registered = _register_patient(
        client, world["reception_token"], world["provider_id"], "Nouveau Patient", dob="2001-07-02"
    )
    arrived = _check_in(client, world["reception_token"], world["provider_id"], registered["id"])
    assert arrived.status_code == 201, arrived.text
    assert arrived.json()["patient_id"] == registered["id"]


def test_registration_is_separately_permissioned_from_check_in(client, world):
    """§8: a member may check in existing patients and be unable to create new records.

    ``patient.register`` is denied individually; the directory and arrival authorities are
    untouched, and both still work."""
    _set(
        client,
        world["admin_token"],
        world["provider_id"],
        world["reception_membership"],
        Capability.PATIENT_REGISTER.value,
        "DENY",
    )
    refused = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/patients",
        headers={**_auth(world["reception_token"]), "Idempotency-Key": _key()},
        json={"display_name": "Refuse Moi"},
    )
    assert refused.status_code == 403, refused.text

    # Search still works, and so does checking in somebody who already exists.
    search = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/patients/search",
        headers=_auth(world["reception_token"]),
        json={"query": "Fatou"},
    )
    assert search.status_code == 200, search.text
    assert (
        _check_in(
            client, world["reception_token"], world["provider_id"], world["patient_id"]
        ).status_code
        == 201
    )


def test_fd_3_and_4_a_checked_in_patient_appears_in_the_queue_and_survives_a_reload(client, world):
    created = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    ).json()

    first = _waiting(client, world["reception_token"], world["provider_id"])
    assert first.status_code == 200, first.text
    assert [row["visit_id"] for row in first.json()] == [created["id"]]

    # FD-4: a reload is a second GET, and it must return the same durable visit.
    second = _waiting(client, world["reception_token"], world["provider_id"])
    assert [row["visit_id"] for row in second.json()] == [created["id"]]


def test_fd_5_the_oldest_arrival_is_first(client, world):
    """§16: oldest first, by the recorded arrival time.

    The opposite of the clinician worklist's newest-first ordering, and deliberately: a waiting room
    answers "who has been here longest". **Not a triage order** -- this is the order people walked
    in, and nothing about the patient influences it."""
    first_patient = _register_patient(client, world["reception_token"], world["provider_id"], "Un")
    second_patient = _register_patient(
        client, world["reception_token"], world["provider_id"], "Deux"
    )

    a = _check_in(client, world["reception_token"], world["provider_id"], first_patient["id"])
    b = _check_in(client, world["reception_token"], world["provider_id"], second_patient["id"])

    queue = _waiting(client, world["reception_token"], world["provider_id"]).json()
    assert [row["visit_id"] for row in queue] == [a.json()["id"], b.json()["id"]]
    assert queue[0]["checked_in_at"] <= queue[1]["checked_in_at"]


def test_fd_6_to_8_the_clinician_starts_the_same_durable_visit_and_it_leaves_the_queue(
    client, world, sessions
):
    """§37/§38: **the same visit id** through the handoff, and the treating clinician is
    server-derived.

    The one assertion that would catch a "check-in record -> new clinician visit" design: the id the
    desk created is the id the clinician started, and no second visit row exists."""
    created = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    ).json()
    visit_id = created["id"]

    # The clinician sees who is waiting without holding the desk's capability by role -- CLINICIAN
    # carries `visit.check_in` for §5 compatibility, and `visit.document` opens this queue anyway.
    queue = _waiting(client, world["clinician_token"], world["provider_id"]).json()
    assert [row["visit_id"] for row in queue] == [visit_id]

    started = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}/take",
        headers=_auth(world["clinician_token"]),
    )
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["id"] == visit_id, "the visit identity must survive the handoff"
    assert body["status"] == VisitStatus.IN_PROGRESS.value
    # Server-derived: nothing in the request named a clinician, and the acting membership's identity
    # is what was recorded.
    assert body["clinician_identity_id"] == world["clinician_id"]
    assert body["started_at"] is not None

    # FD-8: gone from *waiting*, still visible to the desk as taken (§23), and only one row exists.
    queue_after = _waiting(client, world["reception_token"], world["provider_id"]).json()
    assert [row["status"] for row in queue_after] == [VisitStatus.IN_PROGRESS.value]
    assert len(_visits_in_db(sessions, world)) == 1


def test_a_check_in_request_may_not_name_the_treating_clinician(client, world, sessions):
    """§10/§11/NC-FD-4: the endpoint accepts no clinician identity from the browser.

    ``extra="forbid"`` makes the attempt a 422 rather than a silently dropped field, which is the
    difference between a refusal and a field somebody later decides to honour."""
    refused = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers={**_auth(world["reception_token"]), "Idempotency-Key": _key()},
        json={
            "patient_id": world["patient_id"],
            "clinician_identity_id": world["clinician_id"],
        },
    )
    assert refused.status_code == 422, refused.text
    assert not _visits_in_db(sessions, world)


def test_fd_9_to_12_the_existing_clinical_workflow_still_works_on_a_desk_checked_in_visit(
    client, world
):
    """§39/§56: the visit the desk created carries the whole existing clinical workflow.

    Note, completion, history and the same-id invariant, on a visit whose creator held no clinical
    authority at all. If the handoff produced a second-class row, this is what would show it."""
    provider = world["provider_id"]
    visit_id = _check_in(client, world["reception_token"], provider, world["patient_id"]).json()[
        "id"
    ]
    base = f"/exchange/v1/organizations/{provider}/visits/{visit_id}"
    client.post(f"{base}/take", headers=_auth(world["clinician_token"]))

    # FD-9: the clinical note workflow.
    note = client.post(
        f"{base}/notes", headers=_auth(world["clinician_token"]), json={"body": "Examen normal."}
    )
    assert note.status_code == 201, note.text
    assert note.json()["revision_number"] == 1

    # The clinician may also record the reason the desk deliberately did not collect.
    amended = client.put(
        f"{base}/reason",
        headers=_auth(world["clinician_token"]),
        json={"reason_for_visit": "controle de routine"},
    )
    assert amended.status_code == 200, amended.text
    assert amended.json()["reason_for_visit"] == "controle de routine"

    # FD-10: completion.
    completed = client.post(f"{base}/complete", headers=_auth(world["clinician_token"]))
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == VisitStatus.COMPLETED.value

    # FD-11: the patient's visit history holds the same id, not a copy.
    history = client.get(
        f"/exchange/v1/organizations/{provider}/patients/{world['patient_id']}/visits",
        headers=_auth(world["clinician_token"]),
    )
    assert [row["id"] for row in history.json()] == [visit_id]

    # A completed visit is out of the waiting projection entirely.
    assert _waiting(client, world["reception_token"], provider).json() == []


def test_fd_13_an_arrival_can_be_cancelled_before_clinical_contact(client, world):
    """§21: ``CHECKED_IN`` -> ``CANCELLED``, by arrival authority, with no clinician ever recorded.

    A patient who does not wait is an ordinary desk correction, and the row that records it keeps a
    NULL clinician forever, which is the honest record of a consultation that never happened."""
    visit_id = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    ).json()["id"]

    cancelled = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}/cancel",
        headers=_auth(world["reception_token"]),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == VisitStatus.CANCELLED.value
    assert cancelled.json()["clinician_identity_id"] is None
    assert _waiting(client, world["reception_token"], world["provider_id"]).json() == []


def test_fd_14_arrival_authority_cannot_cancel_a_started_consultation(client, world, sessions):
    """§21: once clinical contact begins, the desk does not rewrite the encounter.

    A 409 and not a 403, deliberately: the transition itself does not exist (``IN_PROGRESS`` ->
    ``CANCELLED`` is absent from ``LEGAL_TRANSITIONS``), so a clinician is refused identically. §12
    of the operating specification: a started visit is completed-with-a-note, never cancelled."""
    visit_id = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    ).json()["id"]
    base = f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}"
    client.post(f"{base}/take", headers=_auth(world["clinician_token"]))

    refused = client.post(f"{base}/cancel", headers=_auth(world["reception_token"]))
    assert refused.status_code == 409, refused.text
    assert _visit_row(sessions, visit_id).status == VisitStatus.IN_PROGRESS.value

    # And a clinician is refused the same transition, so this is a state rule not an authority one.
    assert client.post(f"{base}/cancel", headers=_auth(world["clinician_token"])).status_code == 409


def test_fd_15_no_appointment_or_no_show_state_was_introduced(client, world):
    """§41/§67: still four states, and neither calendar state became reachable.

    Asserted against the enum and against the API's own accepted filter values, because a state the
    enum forbids but a query parameter accepts is a half-built feature."""
    assert {status.value for status in VisitStatus} == {
        "CHECKED_IN",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
    }
    for forbidden in ("PLANNED", "NO_SHOW"):
        response = client.get(
            f"/exchange/v1/organizations/{world['provider_id']}/visits?status={forbidden}",
            headers=_auth(world["clinician_token"]),
        )
        assert response.status_code == 422, f"{forbidden} must not be an accepted visit status"


# ── §18/§19: one active visit, and idempotency ───────────────────────────────────────────────────


def test_a_replayed_idempotency_key_yields_one_visit_not_two(client, world, sessions):
    """§19: double click, retry after an uncertain response -- one visit, and 200 rather than 201.

    The status code carries the distinction the interface needs: 201 means "this created the
    arrival", 200 means "the arrival already existed and here it is"."""
    key = _key()
    first = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"], key=key
    )
    second = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"], key=key
    )
    assert first.status_code == 201
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert len(_visits_in_db(sessions, world)) == 1


def test_a_second_arrival_for_a_waiting_patient_is_refused_rather_than_replayed(
    client, world, sessions
):
    """§18: a **different** key for a patient already waiting is a 409, not a silent replay.

    The distinction that matters operationally: two receptionists, or one who did not see that a
    colleague had already checked this patient in. Returning the existing visit would tell the
    second
    worker their action succeeded. It did not, and a waiting list that says otherwise stops being
    believed."""
    first = _check_in(client, world["reception_token"], world["provider_id"], world["patient_id"])
    assert first.status_code == 201

    second = _check_in(client, world["reception_token"], world["provider_id"], world["patient_id"])
    assert second.status_code == 409, second.text
    assert len(_visits_in_db(sessions, world)) == 1


def test_the_invariant_covers_a_started_visit_too(client, world):
    """§18: "active" is ``CHECKED_IN`` **or** ``IN_PROGRESS``.

    A patient already with the clinician must not be re-checked-in at the desk -- that is how the
    same person ends up in two encounters and one of them is abandoned."""
    visit_id = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"]
    ).json()["id"]
    client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}/take",
        headers=_auth(world["clinician_token"]),
    )

    refused = _check_in(client, world["reception_token"], world["provider_id"], world["patient_id"])
    assert refused.status_code == 409, refused.text


def test_a_completed_or_cancelled_visit_never_blocks_a_return_visit(client, world, sessions):
    """§18: historical visits are unlimited. A patient seen this morning may come back this
    afternoon, and a patient whose arrival was cancelled may arrive again immediately.

    This is what makes the index *partial* rather than a plain unique constraint, and it is the case
    a too-eager invariant would break -- silently, and only for returning patients."""
    provider = world["provider_id"]
    patient = world["patient_id"]

    first = _check_in(client, world["reception_token"], provider, patient).json()["id"]
    client.post(
        f"/exchange/v1/organizations/{provider}/visits/{first}/cancel",
        headers=_auth(world["reception_token"]),
    )
    second = _check_in(client, world["reception_token"], provider, patient)
    assert second.status_code == 201, second.text

    base = f"/exchange/v1/organizations/{provider}/visits/{second.json()['id']}"
    client.post(f"{base}/take", headers=_auth(world["clinician_token"]))
    client.post(f"{base}/complete", headers=_auth(world["clinician_token"]))

    third = _check_in(client, world["reception_token"], provider, patient)
    assert third.status_code == 201, third.text
    assert len(_visits_in_db(sessions, world)) == 3


def test_two_different_patients_may_wait_at_once(client, world):
    """The control for the test above: the invariant is per patient, not per organization.

    Without this, an invariant accidentally written over ``provider_organization_id`` alone would
    pass every other test in this file while allowing exactly one patient in the building."""
    other = _register_patient(client, world["reception_token"], world["provider_id"], "Deuxieme")
    assert (
        _check_in(
            client, world["reception_token"], world["provider_id"], world["patient_id"]
        ).status_code
        == 201
    )
    assert (
        _check_in(client, world["reception_token"], world["provider_id"], other["id"]).status_code
        == 201
    )
    assert len(_waiting(client, world["reception_token"], world["provider_id"]).json()) == 2


def test_the_same_patient_may_wait_at_two_different_providers(client, world):
    """Tenant scoping of the invariant. Two facilities are two independent waiting rooms, and one
    facility's arrival must not be able to block another's."""
    # A patient registered independently at each provider -- ADR-051: there is no shared identity.
    here = _register_patient(client, world["reception_token"], world["provider_id"], "Meme Nom")
    there = _register_patient(
        client, world["reception_z_token"], world["other_provider_id"], "Meme Nom"
    )
    assert (
        _check_in(client, world["reception_token"], world["provider_id"], here["id"]).status_code
        == 201
    )
    assert (
        _check_in(
            client, world["reception_z_token"], world["other_provider_id"], there["id"]
        ).status_code
        == 201
    )


def test_a_blank_reason_is_refused_rather_than_stored_as_absent(client, world, sessions):
    """ "Nobody was asked" and "somebody submitted an empty field" are different facts.

    Only the first is worth recording as NULL. A whitespace-only string reaches the service
    (Pydantic accepts it -- ``min_length`` counts characters) and is refused there with a 422."""
    blank = _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"], reason="   "
    )
    assert blank.status_code == 422, blank.text
    assert not _visits_in_db(sessions, world)


# ── §57: the data-minimization contract ──────────────────────────────────────────────────────────

#: The complete, deliberately enumerated field list of the arrival projection. Changing this set is
#: a privacy decision, not a refactor, which is why the names are asserted exactly.
WAITING_FIELDS = {
    "visit_id",
    "patient_id",
    "patient_display_name",
    "patient_date_of_birth",
    "status",
    "checked_in_at",
}

#: Everything a reception caller must never receive from this endpoint. Named individually so a
#: reader can see what was considered, and so a future field cannot be added by accident.
FORBIDDEN_WAITING_FIELDS = {
    "reason_for_visit",
    "clinical_note",
    "note",
    "notes",
    "body",
    "clinician_identity_id",
    "clinician_display_name",
    "observations",
    "results",
    "result_values",
    "release_versions",
    "doctor_review",
    "review",
    "diagnosis",
    "started_at",
    "completed_at",
    "cancelled_at",
    "orders",
}


def test_fd_57_the_waiting_projection_exposes_exactly_the_enumerated_fields(client, world):
    """§57: asserted against the **response body**, not against what the frontend renders.

    Both the model and a live response are checked. The model check pins the contract; the live
    check proves the route actually returns that model rather than something wider that happens to
    validate. NC-FD-3 makes the route return the full visit and reddens this."""
    assert set(WaitingArrivalResponse.model_fields) == WAITING_FIELDS

    _check_in(
        client, world["reception_token"], world["provider_id"], world["patient_id"], reason=None
    )
    rows = _waiting(client, world["reception_token"], world["provider_id"]).json()
    assert rows, "the fixture must produce a waiting row for this to mean anything"
    for row in rows:
        assert set(row) == WAITING_FIELDS
        assert not (set(row) & FORBIDDEN_WAITING_FIELDS)


def test_fd_57b_a_stated_reason_never_reaches_the_waiting_projection(client, world, sessions):
    """The assertion that would pass vacuously without this fixture: a visit **with** a stated
    reason must still not publish it.

    The clinician records a reason on the visit, and the desk's view of the same visit is checked
    for the literal text. Without this, every §57 assertion above would hold merely because no
    reason had ever been stored."""
    provider = world["provider_id"]
    visit_id = _check_in(client, world["reception_token"], provider, world["patient_id"]).json()[
        "id"
    ]
    secret = "motif-confidentiel-xyz"
    client.put(
        f"/exchange/v1/organizations/{provider}/visits/{visit_id}/reason",
        headers=_auth(world["clinician_token"]),
        json={"reason_for_visit": secret},
    )
    # It really is stored, so the absence below is a filter and not an empty database.
    assert _visit_row(sessions, visit_id).reason_for_visit == secret

    response = _waiting(client, world["reception_token"], provider)
    assert secret not in response.text, "the stated reason reached the arrival projection"
    assert set(response.json()[0]) == WAITING_FIELDS


def test_the_waiting_projection_publishes_no_derived_waiting_time(client, world):
    """**A recorded decision pinned as a test.**

    ``AGENTS.md`` (2026-09-27, DHEALTH-CLINIC-VISIT-001) and
    ``docs/NATIVE-OPERATING-PRODUCT.md`` §38 both forbid deriving a waiting time, priority or triage
    order from visit timestamps without the clinical lead's sign-off: a number a clinic would act on
    is an operational signal DHealth must not produce, even when arithmetic produced it. This
    ticket's own §16 asked for one, and it is **not built** -- recorded as a blocker in
    ``PROJECT_STATE.md`` instead.

    This test asserts the absence so a later contributor who reads §16 and "helpfully" adds the
    field is stopped by a failing test rather than by review."""
    _check_in(client, world["reception_token"], world["provider_id"], world["patient_id"])
    rows = _waiting(client, world["reception_token"], world["provider_id"]).json()
    markers = (
        "wait",
        "duration",
        "elapsed",
        "minutes",
        "attente",
        "priority",
        "urgen",
        "triage",
        "acuity",
    )
    for row in rows:
        for field in row:
            assert not any(marker in field.lower() for marker in markers), (
                f"{field!r} looks like a derived waiting/priority signal"
            )


# ── §36: audit ───────────────────────────────────────────────────────────────────────────────────


def test_fd_36_the_audit_trail_establishes_who_checked_in_and_who_began_the_consultation(
    client, world, sessions
):
    """§36: reusing ``visit_created``/``visit_started`` rather than inventing a new event.

    ``visit_created``'s ``actor_identity_id`` **is** "who checked the patient in" -- truthfully, and
    for every historical row too, which is why no ``checked_in_by`` column was added. The
    receptionist appears as the actor and never as the clinician; the clinician appears on
    ``visit_started``.

    No new event type, so 0034's audit CHECK constraint is untouched (§36: do not duplicate audit
    events when an existing one already truthfully represents check-in)."""
    provider = world["provider_id"]
    visit_id = _check_in(client, world["reception_token"], provider, world["patient_id"]).json()[
        "id"
    ]
    client.post(
        f"/exchange/v1/organizations/{provider}/visits/{visit_id}/take",
        headers=_auth(world["clinician_token"]),
    )

    with sessions() as session:
        events = list(
            session.scalars(
                select(ExchangeSecurityAuditEvent)
                .where(ExchangeSecurityAuditEvent.resource_id == uuid.UUID(visit_id))
                .order_by(ExchangeSecurityAuditEvent.created_at)
            ).all()
        )

    by_type = {event.event_type: event for event in events}
    assert "visit_created" in by_type and "visit_started" in by_type
    created = by_type["visit_created"]
    assert str(created.actor_identity_id) == world["reception_id"], "who checked the patient in"
    assert str(created.organization_id) == provider
    assert created.resource_type == "visit"
    assert str(by_type["visit_started"].actor_identity_id) == world["clinician_id"]

    # The receptionist is the *actor*, never the clinician of record -- the row says so.
    assert str(_visit_row(sessions, visit_id).clinician_identity_id) == world["clinician_id"]


def test_no_audit_row_carries_the_stated_reason_or_any_clinical_text(client, world, sessions):
    """§36: "Do not log patient clinical content in generic audit metadata."

    The reason is the one clinical-adjacent field the arrival path touches, so it is the one worth
    asserting against every column of every audit row this workflow writes."""
    provider = world["provider_id"]
    secret = "motif-jamais-audite-abc"
    visit_id = _check_in(
        client, world["reception_token"], provider, world["patient_id"], reason=secret
    ).json()["id"]
    client.post(
        f"/exchange/v1/organizations/{provider}/visits/{visit_id}/take",
        headers=_auth(world["clinician_token"]),
    )

    with sessions() as session:
        rows = session.scalars(select(ExchangeSecurityAuditEvent)).all()
    assert rows
    for row in rows:
        rendered = " ".join(str(getattr(row, column.name)) for column in row.__table__.columns)
        assert secret not in rendered


def test_a_cancelled_arrival_records_who_cancelled_it(client, world, sessions):
    """§36: "who cancelled an arrival, if cancelled"."""
    provider = world["provider_id"]
    visit_id = _check_in(client, world["reception_token"], provider, world["patient_id"]).json()[
        "id"
    ]
    client.post(
        f"/exchange/v1/organizations/{provider}/visits/{visit_id}/cancel",
        headers=_auth(world["reception_token"]),
    )

    with sessions() as session:
        cancelled = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "visit_cancelled"
            )
        ).all()
    assert [str(row.actor_identity_id) for row in cancelled] == [world["reception_id"]]
