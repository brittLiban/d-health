"""DH-EX-002R's required proof: a security mutation and its authoritative
``ExchangeSecurityAuditEvent`` row commit or roll back together, as one database transaction --
never a mutation surviving with no audit row, and never an audit row surviving with no mutation.

Each test injects a failure at a different point inside the transaction opened by
``session_factory.begin()`` in ``exchange/api.py``'s mutation handlers, then queries the database
directly (bypassing the API) to prove nothing partial was left behind. This is the control the
DH-EX-002R ticket explicitly requires: "inject failure after mutation row insert, before audit row
insert", "inject audit insert failure", "inject commit failure" -- each expected to leave zero
mutation and zero audit row.
"""

from __future__ import annotations

import pytest
from exchange_auth_support import (
    ISSUER,
    PLATFORM_ADMIN_ROLE,
    build_validator,
    generate_keypair,
    make_token,
)
from exchange_support import make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    LabOrderStatus,
    LabSelection,
    Organization,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PatientReference,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def client(sessions, keypair, tmp_path) -> TestClient:
    _, public_key = keypair
    validator = build_validator(public_key)
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=validator,
        audit_writer=ExchangeAuditWriter(tmp_path / "exchange-audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
    )
    app = FastAPI()
    app.include_router(router)
    # Failure-injection tests want the real exception, not a swallowed-into-500 response, so the
    # assertion is explicit about *what* failed rather than just *that* something failed.
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _counts(sessions: sessionmaker[Session]) -> dict[str, int]:
    with sessions() as session:
        return {
            "organizations": session.scalar(select(func.count()).select_from(Organization)),
            "memberships": session.scalar(select(func.count()).select_from(OrganizationMembership)),
            "role_assignments": session.scalar(
                select(func.count()).select_from(OrganizationRoleAssignment)
            ),
            "security_audit_events": session.scalar(
                select(func.count()).select_from(ExchangeSecurityAuditEvent)
            ),
            "patient_references": session.scalar(
                select(func.count()).select_from(PatientReference)
            ),
            "lab_selections": session.scalar(select(func.count()).select_from(LabSelection)),
        }


# -- organization creation: audit-insert failure --------------------------------------------------


def test_organization_create_rolls_back_when_audit_insert_fails(
    client: TestClient, keypair, sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _counts(sessions)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    private_key, _ = keypair
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])
    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.post(
            "/exchange/v1/organizations",
            headers=_auth(admin_token),
            json={"name": "Should Not Persist", "organization_type": "PROVIDER"},
        )

    after = _counts(sessions)
    # Only `security_audit_events` differs in expectation (it's what we broke); the load-bearing
    # assertion is `organizations` staying exactly where it started -- the mutation the audit
    # insert failure was supposed to roll back alongside it.
    assert after["organizations"] == before["organizations"]
    assert after["security_audit_events"] == before["security_audit_events"]


# -- organization creation: commit failure ---------------------------------------------------------


def test_organization_create_rolls_back_when_commit_fails(
    client: TestClient, keypair, sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _counts(sessions)

    real_commit = Session.commit

    def _raise(self: Session) -> None:
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(Session, "commit", _raise)

    private_key, _ = keypair
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])
    try:
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            client.post(
                "/exchange/v1/organizations",
                headers=_auth(admin_token),
                json={"name": "Should Not Persist Either", "organization_type": "PROVIDER"},
            )
    finally:
        monkeypatch.setattr(Session, "commit", real_commit)

    after = _counts(sessions)
    assert after["organizations"] == before["organizations"]
    assert after["security_audit_events"] == before["security_audit_events"]


# -- membership creation + role assignment: audit-insert failure mid-sequence ----------------------


def test_membership_create_with_roles_rolls_back_everything_on_late_audit_failure(
    client: TestClient,
    keypair,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure is injected on the *second* `record()` call (the role-assignment audit, not
    the membership-creation audit) -- proving the whole multi-statement transaction unwinds, not
    just the one statement that happened to fail."""
    org = make_provider(organizations)
    private_key, _ = keypair
    org_admin_token = make_token(private_key, subject="org-admin-1")
    me = client.get("/exchange/v1/me", headers=_auth(org_admin_token))
    org_admin_identity_id = me.json()["identity_id"]

    # Bootstrap the org admin's own membership directly via the repository (not through the API,
    # to avoid tripping the very monkeypatch this test is about to install).
    from daher_adapter.exchange.membership import (
        ExchangeIdentityRepository,
        OrganizationMembershipRepository,
        OrganizationRoleAssignmentRepository,
    )
    from daher_adapter.exchange.models import OrganizationRole

    identities_repo = ExchangeIdentityRepository(sessions)
    memberships_repo = OrganizationMembershipRepository(sessions)
    roles_repo = OrganizationRoleAssignmentRepository(sessions)
    identity, _ = identities_repo.get_or_create(issuer=ISSUER, subject="org-admin-1")
    assert str(identity.id) == org_admin_identity_id
    membership, _ = memberships_repo.create(organization_id=org.id, identity_id=identity.id)
    roles_repo.assign(membership_id=membership.id, role=OrganizationRole.ORG_ADMIN)

    target_identity_id = client.get(
        "/exchange/v1/me", headers=_auth(make_token(private_key, subject="target-sub"))
    ).json()["identity_id"]

    before = _counts(sessions)

    call_count = {"n": 0}
    real_record = ExchangeSecurityAuditRepository.record

    def _fail_on_second_call(self, session, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("simulated late audit-insert failure")
        return real_record(self, session, **kwargs)

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _fail_on_second_call)

    with pytest.raises(RuntimeError, match="simulated late audit-insert failure"):
        client.post(
            f"/exchange/v1/organizations/{org.id}/memberships",
            headers=_auth(org_admin_token),
            json={"identity_id": target_identity_id, "roles": ["CLINICIAN"]},
        )

    after = _counts(sessions)
    assert after["memberships"] == before["memberships"], (
        "the membership row from the failed request must not survive -- the whole transaction, "
        "including the earlier membership_created audit insert, must have rolled back too"
    )
    assert after["role_assignments"] == before["role_assignments"]
    assert after["security_audit_events"] == before["security_audit_events"]


# -- DH-EX-009: patient-reference binding: audit-insert failure on a NEW reference -----------------


def test_new_patient_reference_bind_rolls_back_when_audit_insert_fails(
    client: TestClient,
    keypair,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exchange_support import make_provider

    org = make_provider(organizations, name="AuditFailBindOrg")
    private_key, _ = keypair
    clinician_token = make_token(private_key, subject="clinician-audit-fail")
    me = client.get("/exchange/v1/me", headers=_auth(clinician_token))
    clinician_identity_id = me.json()["identity_id"]

    from daher_adapter.exchange.membership import (
        ExchangeIdentityRepository,
        OrganizationMembershipRepository,
        OrganizationRoleAssignmentRepository,
    )
    from daher_adapter.exchange.models import OrganizationRole

    identities_repo = ExchangeIdentityRepository(sessions)
    memberships_repo = OrganizationMembershipRepository(sessions)
    roles_repo = OrganizationRoleAssignmentRepository(sessions)
    identity, _ = identities_repo.get_or_create(issuer=ISSUER, subject="clinician-audit-fail")
    assert str(identity.id) == clinician_identity_id
    membership, _ = memberships_repo.create(organization_id=org.id, identity_id=identity.id)
    roles_repo.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)

    before = _counts(sessions)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.post(
            f"/exchange/v1/organizations/{org.id}/patient-references",
            headers=_auth(clinician_token),
            json={
                "source_system": "dhealth_provider_portal",
                "source_identity_system": "urn:daher-health:provider-portal-patient-id",
                "source_identity_value": "AUDIT-FAIL-1",
            },
        )

    after = _counts(sessions)
    assert after["patient_references"] == before["patient_references"], (
        "a newly-created PatientReference must not survive an audit-insert failure -- the bind "
        "and its authoritative audit event commit or roll back together"
    )
    assert after["security_audit_events"] == before["security_audit_events"]


def test_existing_patient_reference_replay_rolls_back_cleanly_when_audit_insert_fails(
    client: TestClient,
    keypair,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The idempotent-replay path also audits (outcome=`returned_existing`), so it also runs
    inside the same outer transaction -- an audit failure here must roll back cleanly too (no new
    audit row), and, since no mutation was ever attempted for an existing reference, the
    pre-existing row itself must be completely unaffected either way."""
    from exchange_support import make_provider

    org = make_provider(organizations, name="AuditFailReplayOrg")
    private_key, _ = keypair
    clinician_token = make_token(private_key, subject="clinician-audit-fail-2")
    me = client.get("/exchange/v1/me", headers=_auth(clinician_token))
    clinician_identity_id = me.json()["identity_id"]

    from daher_adapter.exchange.membership import (
        ExchangeIdentityRepository,
        OrganizationMembershipRepository,
        OrganizationRoleAssignmentRepository,
    )
    from daher_adapter.exchange.models import OrganizationRole

    identities_repo = ExchangeIdentityRepository(sessions)
    memberships_repo = OrganizationMembershipRepository(sessions)
    roles_repo = OrganizationRoleAssignmentRepository(sessions)
    identity, _ = identities_repo.get_or_create(issuer=ISSUER, subject="clinician-audit-fail-2")
    assert str(identity.id) == clinician_identity_id
    membership, _ = memberships_repo.create(organization_id=org.id, identity_id=identity.id)
    roles_repo.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)

    bind_body = {
        "source_system": "dhealth_provider_portal",
        "source_identity_system": "urn:daher-health:provider-portal-patient-id",
        "source_identity_value": "AUDIT-FAIL-2",
    }
    first = client.post(
        f"/exchange/v1/organizations/{org.id}/patient-references",
        headers=_auth(clinician_token),
        json=bind_body,
    )
    assert first.status_code == 201, first.text

    before = _counts(sessions)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure on replay")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure on replay"):
        client.post(
            f"/exchange/v1/organizations/{org.id}/patient-references",
            headers=_auth(clinician_token),
            json=bind_body,
        )

    after = _counts(sessions)
    assert after["patient_references"] == before["patient_references"] == 1
    assert after["security_audit_events"] == before["security_audit_events"]


# -- DH-EX-010: provider-assigned lab-routing audit atomicity -------------------------------------


def test_provider_lab_assignment_rolls_back_when_audit_insert_fails(
    client: TestClient,
    keypair,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the same atomicity contract for DH-EX-010's new mutation: a LabSelection created by
    `assign_by_provider` and the order's SELECTED transition must never survive an audit-insert
    failure -- both roll back together with the failed `lab_order_assigned` audit row."""
    from exchange_support import make_lab

    from daher_adapter.exchange.catalog import CatalogRepository
    from daher_adapter.exchange.directory import LabDirectoryProfileRepository
    from daher_adapter.exchange.identity import (
        PatientReferenceRepository,
        PractitionerReferenceRepository,
    )
    from daher_adapter.exchange.membership import (
        ExchangeIdentityRepository,
        OrganizationMembershipRepository,
        OrganizationRoleAssignmentRepository,
    )
    from daher_adapter.exchange.models import OrganizationRole
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
    from daher_adapter.exchange.provider_lab_routes import ProviderLabRouteRepository

    org = make_provider(organizations, name="AuditFailAssignOrg")
    lab = make_lab(organizations, name="AuditFailAssignLab")
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients_repo = PatientReferenceRepository(sessions)
    practitioners_repo = PractitionerReferenceRepository(sessions)
    orders_repo = LabOrderRepository(sessions, organizations, catalog)

    entry, _ = catalog.create_entry(code="DH-AUDIT-FAIL-ASSIGN", display_name="X")
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    patient = patients_repo.get_or_create(
        source_organization_id=org.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="AUDIT-FAIL-ASSIGN-PATIENT",
    )
    practitioner = practitioners_repo.get_or_create(
        source_organization_id=org.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value="AUDIT-FAIL-ASSIGN-DOC",
    )

    private_key, _ = keypair
    clinician_token = make_token(private_key, subject="clinician-audit-fail-assign")
    me = client.get("/exchange/v1/me", headers=_auth(clinician_token))
    clinician_identity_id = me.json()["identity_id"]

    identities_repo = ExchangeIdentityRepository(sessions)
    memberships_repo = OrganizationMembershipRepository(sessions)
    roles_repo = OrganizationRoleAssignmentRepository(sessions)
    identity, _ = identities_repo.get_or_create(
        issuer=ISSUER, subject="clinician-audit-fail-assign"
    )
    assert str(identity.id) == clinician_identity_id
    membership, _ = memberships_repo.create(organization_id=org.id, identity_id=identity.id)
    roles_repo.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)

    order = orders_repo.create(
        idempotency_key="audit-fail-assign",
        provider_organization_id=org.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )

    # DH-CLINIC-006: assign_by_provider now requires an active ProviderLabRoute -- configured
    # directly via the repository, matching this test's own repository-direct setup style, and
    # deliberately before the audit-insert monkeypatch below (this call has nothing to do with the
    # atomicity contract under test).
    ProviderLabRouteRepository(sessions, organizations).upsert(
        provider_organization_id=org.id, lab_organization_id=lab.id, active=True
    )

    before = _counts(sessions)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure on assign-lab")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure on assign-lab"):
        client.post(
            f"/exchange/v1/organizations/{org.id}/orders/{order.order.id}/assign-lab",
            headers=_auth(clinician_token),
            json={"lab_organization_id": str(lab.id)},
        )

    after = _counts(sessions)
    assert after["lab_selections"] == before["lab_selections"], (
        "a newly-created LabSelection must not survive an audit-insert failure -- the assignment "
        "and its authoritative audit event commit or roll back together"
    )
    assert after["security_audit_events"] == before["security_audit_events"]
    with sessions() as session:
        from daher_adapter.exchange.models import LabOrder

        refreshed = session.get(LabOrder, order.order.id)
        assert refreshed.status == LabOrderStatus.AWAITING_LAB_SELECTION.value, (
            "the order's SELECTED transition must also roll back -- it is part of the same "
            "transaction as the LabSelection insert and the failed audit row"
        )
