"""DHEALTH-LAB-OFFERINGS-001: "does this laboratory accept this analysis through Hawva",
at the edge.

**Most of what this file tests already worked at `7fe77fb`**, and that is recorded rather than
disguised. DH-EX-003 built `LabOffering`, its `active` boolean, its `(lab_organization_id,
catalog_entry_id)` uniqueness, `OfferingInactiveConflict`, and both audit events.
DH-CLINIC-006 built provider routing and the eligible-labs projection. `selection.py` has
revalidated availability inside its locked assignment mutation since DH-MVP-ONBOARD-001.
`PROJECT_STATE.md`'s entry for this ticket maps all of it field by field.

So this file exists to **qualify** claims that were true and unproven, and to pin two things that
were not true before:

* `set_offering_active` now locks the row it compares -- `test_enabling_twice_writes_one_audit_row`
  and the PostgreSQL suite's `PG-OF-1a` are the pair that hold it.
* a laboratory can reach this configuration at all, through `facility.configure`.

── The five truths this ticket refuses to collapse ──

The success condition is not one question but five, and every one of them has its own test here
because the temptation is to let any one imply the others:

1. **service identity** -- `TestCatalogEntry` owns the code and the name. The offering copies
   neither (`test_the_offering_does_not_duplicate_canonical_service_identity`).
2. **laboratory availability** -- `LabOffering.active`, and nothing else, decides whether NEW
   work may be routed (`test_disabling_removes_the_laboratory_from_eligibility_for_a_new_order`).
3. **organization relationship** -- an active offering is *not* clinic authorization.
   `ProviderLabRoute` is checked separately and both directions are pinned
   (`test_an_offering_is_not_authorization_to_send_work`).
4. **billing price** -- separate table, and an offering needs no price to be active
   (`test_an_offering_is_active_without_any_price`).
5. **specimen requirements** -- not modelled anywhere, and this ticket added nothing
   (`test_the_offering_carries_no_specimen_configuration`).

── What this file does NOT establish ──

Nothing here is evidence of human usability. No laboratory administrator has used the screen. It
establishes nothing about real-world service availability, accreditation, or whether the French
service names are what a Djiboutian laboratory would call them -- every catalogue entry it uses is
synthetic by construction (DH-EX-003/DH-EX-004 refuse a non-synthetic entry).

All data is synthetic. No real patient data is used anywhere in this file.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import activate_result_template, make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import LabOfferingResponse, build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.capabilities import SYSTEM_ROLE_TEMPLATES, Capability
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    LabOffering,
    LabOrderItem,
    OrganizationRole,
)
from daher_adapter.exchange.organizations import OrganizationRepository

TTL_HOURS = 168


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
        patient_access_ttl_hours=TTL_HOURS,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def catalog(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> CatalogRepository:
    return CatalogRepository(sessions, organizations)


@pytest.fixture
def directory(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> LabDirectoryProfileRepository:
    return LabDirectoryProfileRepository(sessions, organizations)


@pytest.fixture
def patients(sessions: sessionmaker[Session]) -> PatientReferenceRepository:
    return PatientReferenceRepository(sessions)


# -- harness ---------------------------------------------------------------------------------------
#
# This file's own, following `test_dhealthlabreception001_intake.py`'s precedent rather than
# importing from a sibling test module.


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _key() -> str:
    return str(uuid.uuid4())


def _bootstrap(client: TestClient, private_key, subject: str, **kwargs) -> str:
    token = make_token(private_key, subject=subject, **kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _attach(client, private_key, org_id, identity_id, subject: str, roles: list[str]) -> str:
    admin = make_token(private_key, subject=f"setup-{subject}", roles=[PLATFORM_ADMIN_ROLE])
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(admin),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["membership_id"]


def _set_capability(client, private_key, org_id, membership_id, capability: str, effect: str):
    admin = make_token(private_key, subject="setup-cap-admin", roles=[PLATFORM_ADMIN_ROLE])
    return client.put(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(admin),
        json={"effect": effect},
    )


def _clear_capability(client, private_key, org_id, membership_id, capability: str):
    admin = make_token(private_key, subject="setup-cap-admin", roles=[PLATFORM_ADMIN_ROLE])
    return client.delete(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(admin),
    )


def _staff(client, private_key, org, name: str, roles: list[str]):
    identity = _bootstrap(client, private_key, f"user-{name}")
    membership = _attach(client, private_key, org.id, identity, f"user-{name}", roles)
    return make_token(private_key, subject=f"user-{name}"), identity, membership


def _lab_with_offering(catalog, directory, organizations, *, name: str, code: str):
    """A laboratory that offers one synthetic catalogue entry, priced, with an active template.

    All four facts are needed for the laboratory to be *eligible*, and they are four separate facts
    on purpose -- several tests below switch exactly one off and assert the right refusal."""
    lab = make_lab(organizations, name=name)
    entry, _ = catalog.create_entry(code=code, display_name=f"Analyse {code}")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    activate_result_template(catalog, offering_id=offering.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    return lab, entry, offering


def _route(client, private_key, provider_id, lab_id, *, active: bool = True):
    admin = make_token(private_key, subject=f"route-{uuid.uuid4()}", roles=[PLATFORM_ADMIN_ROLE])
    response = client.put(
        f"/exchange/v1/organizations/{provider_id}/lab-routes/{lab_id}",
        headers=_auth(admin),
        json={"active": active},
    )
    assert response.status_code in (200, 201), response.text
    return response


def _clinic(client, private_key, organizations, patients, *, name: str):
    provider = make_provider(organizations, name=f"Prov-{name}")
    token, _identity, _membership = _staff(
        client, private_key, provider, f"clin-{name}", ["CLINICIAN"]
    )
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Patient Synthetique {name}",
    )
    return provider, token, patient


def _create_order(client, token, provider_id, patient_id, entry_ids):
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/orders",
        headers={**_auth(token), "Idempotency-Key": _key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(e) for e in entry_ids],
        },
    )


def _eligible(client, token, provider_id, order_id):
    return client.get(
        f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/eligible-labs",
        headers=_auth(token),
    )


def _assign(client, token, provider_id, order_id, lab_id):
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/assign-lab",
        headers=_auth(token),
        json={"lab_organization_id": str(lab_id)},
    )


def _list_offerings(client, token, lab_id):
    return client.get(f"/exchange/v1/organizations/{lab_id}/offerings", headers=_auth(token))


def _create_offering(client, token, lab_id, entry_id, *, local_test_code=None):
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/offerings",
        headers=_auth(token),
        json={"catalog_entry_id": str(entry_id), "local_test_code": local_test_code},
    )


def _set_active(client, token, lab_id, offering_id, active: bool):
    return client.patch(
        f"/exchange/v1/organizations/{lab_id}/offerings/{offering_id}",
        headers=_auth(token),
        json={"active": active},
    )


# ── the five truths, kept apart ──────────────────────────────────────────────────────────────────


def test_the_offering_does_not_duplicate_canonical_service_identity():
    """Truth 1. The offering says *that* a laboratory performs a service, never *what* the service
    is. The name, the code and the version belong to ``TestCatalogEntry``.

    Asserted over the model and the response shape rather than one response body, so a field added
    later fails this automatically. A duplicated name is the specific harm: two rows that can
    disagree about what a service is called, with a clinician reading one and a laboratory the
    other."""
    columns = {column.name for column in LabOffering.__table__.columns}
    assert columns == {
        "id",
        "lab_organization_id",
        "catalog_entry_id",
        "local_test_code",
        "active",
        "created_at",
    }
    for canonical in ("name", "display_name", "code", "version", "description"):
        assert canonical not in columns, (
            f"{canonical!r} belongs to TestCatalogEntry; an offering that copies it creates a "
            f"second answer to 'what is this service called'"
        )
    # `local_test_code` is the laboratory's own alias and is deliberately NOT the canonical code --
    # it is nullable, unconstrained against the catalogue, and nothing reads it as an identity.
    assert LabOffering.__table__.c.local_test_code.nullable

    assert set(LabOfferingResponse.model_fields) == {
        "id",
        "lab_organization_id",
        "catalog_entry_id",
        "local_test_code",
        "active",
    }
    assert LabOfferingResponse.model_config.get("extra") == "forbid"


def test_the_offering_carries_no_specimen_configuration():
    """Truth 5. DHEALTH-SPECIMEN-001 left specimen-type configuration open as **D-3's participant
    half** and this ticket keeps it open.

    An offering is the plausible eventual home for "this analysis needs this kind of sample", which
    is exactly why the absence needs a guard: the next author will find it natural to add one here,
    and that is a clinical decision nobody has made. `TestCatalogEntry` has no specimen field
    either, so a type could not even be derived truthfully."""
    columns = {column.name for column in LabOffering.__table__.columns}
    for forbidden in (
        "specimen_type",
        "specimen",
        "tube",
        "tube_type",
        "container",
        "container_type",
        "volume",
        "volume_ml",
        "anticoagulant",
        "collection_method",
        "anatomical_site",
        "accession_format",
        "barcode",
        "barcode_content",
    ):
        assert forbidden not in columns
    assert not any("specimen" in name for name in columns)
    assert not any("specimen" in name for name in LabOfferingResponse.model_fields)


def test_the_offering_carries_no_price_and_no_result_structure():
    """Truths 4 and part of 1, structurally. Price lives in ``LabPrice``; result structure lives in
    ``LabResultTemplate``. Merging either into availability is what NC-OF-8 and NC-OF-9 attack."""
    columns = {column.name for column in LabOffering.__table__.columns}
    for forbidden in (
        "amount",
        "price",
        "currency",
        "unit",
        "numeric_unit",
        "reference_range",
        "reference_low",
        "reference_high",
        "result_fields",
        "observations",
    ):
        assert forbidden not in columns


# ── availability, and what it does and does not reach ────────────────────────────────────────────


def test_a_laboratory_can_read_and_change_its_own_offerings(
    client, keypair, organizations, catalog, directory
):
    """The gap this ticket closes. `facility.configure` at this laboratory, and nothing else."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="OwnLab", code="OWN-1"
    )
    token, _identity, _membership = _staff(client, private_key, lab, "ownadmin", ["ORG_ADMIN"])

    listed = _list_offerings(client, token, lab.id)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [row["catalog_entry_id"] for row in rows] == [str(entry.id)]
    assert rows[0]["active"] is True

    disabled = _set_active(client, token, lab.id, offering.id, False)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["active"] is False

    assert _list_offerings(client, token, lab.id).json()[0]["active"] is False

    reenabled = _set_active(client, token, lab.id, offering.id, True)
    assert reenabled.status_code == 200, reenabled.text
    assert reenabled.json()["active"] is True


def test_reactivation_is_explicit_and_never_a_side_effect_of_re_creating(
    client, keypair, organizations, catalog, directory
):
    """Re-POSTing an offering that exists but is inactive is a **409**, not a silent reactivation.

    The distinction matters operationally: "add this service" and "start accepting this service
    again" are different intentions, and a laboratory that stopped accepting an analysis for a
    reason should not have it switched back on by somebody re-adding it."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="Reacti", code="REACT-1"
    )
    token, _i, _m = _staff(client, private_key, lab, "reactadmin", ["ORG_ADMIN"])
    assert _set_active(client, token, lab.id, offering.id, False).status_code == 200

    conflict = _create_offering(client, token, lab.id, entry.id)
    assert conflict.status_code == 409, conflict.text
    assert "reactivate" in conflict.json()["detail"]

    # Still inactive -- the refused call changed nothing.
    assert _list_offerings(client, token, lab.id).json()[0]["active"] is False
    # And the explicit path works.
    assert _set_active(client, token, lab.id, offering.id, True).status_code == 200


def test_an_offering_is_active_without_any_price(
    client, keypair, organizations, catalog, directory
):
    """Truth 4. Availability does not require a price, and nothing gates on payment.

    An offering created with no `LabPrice` at all is `active` and readable. It will not be
    *marketplace*-eligible -- `select()`'s patient price-consent path needs a current price, by
    design -- but that is a different question from whether the laboratory accepts the work, and
    D-2a stays open."""
    private_key, _ = keypair
    lab = make_lab(organizations, name="NoPriceLab")
    entry, _ = catalog.create_entry(code="NOPRICE-1", display_name="Analyse sans tarif")
    token, _i, _m = _staff(client, private_key, lab, "nopriceadmin", ["ORG_ADMIN"])

    created = _create_offering(client, token, lab.id, entry.id)
    assert created.status_code == 201, created.text
    assert created.json()["active"] is True

    rows = _list_offerings(client, token, lab.id).json()
    assert len(rows) == 1
    assert rows[0]["active"] is True


def test_disabling_removes_the_laboratory_from_eligibility_for_a_new_order(
    client, keypair, organizations, catalog, directory, patients
):
    """Truth 2, at the clinic's own edge. The whole success condition in one test.

    The clinic asks which of its authorized laboratories can perform *this order*. With the offering
    active the laboratory is offered; with it disabled the list is empty and the clinic is told so
    truthfully rather than shown a laboratory that would refuse the work."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="ElgLab", code="ELG-1"
    )
    provider, clinician, patient = _clinic(client, private_key, organizations, patients, name="elg")
    _route(client, private_key, provider.id, lab.id)
    lab_admin, _i, _m = _staff(client, private_key, lab, "elgadmin", ["ORG_ADMIN"])

    created = _create_order(client, clinician, provider.id, patient.id, [entry.id])
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    before = _eligible(client, clinician, provider.id, order_id)
    assert before.status_code == 200, before.text
    assert [row["lab_organization_id"] for row in before.json()] == [str(lab.id)]

    assert _set_active(client, lab_admin, lab.id, offering.id, False).status_code == 200

    after = _eligible(client, clinician, provider.id, order_id)
    assert after.status_code == 200, after.text
    assert after.json() == [], "a disabled offering must remove the laboratory from eligibility"


def test_the_server_rechecks_availability_when_the_order_is_actually_assigned(
    client, keypair, organizations, catalog, directory, patients
):
    """The stale-browser case, and the reason hiding a control is never the boundary.

    The clinic legitimately saw the laboratory as eligible, then the laboratory disabled the
    analysis, and only then did the clinic submit. A browser holding a stale list -- or a hand-typed
    request that never read the list at all -- must be refused by the server, and it is: assignment
    revalidates availability inside its own locked mutation rather than trusting what was true at
    order creation."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="StaleLab", code="STALE-1"
    )
    provider, clinician, patient = _clinic(
        client, private_key, organizations, patients, name="stale"
    )
    _route(client, private_key, provider.id, lab.id)
    lab_admin, _i, _m = _staff(client, private_key, lab, "staleadmin", ["ORG_ADMIN"])

    created = _create_order(client, clinician, provider.id, patient.id, [entry.id])
    assert created.status_code == 201
    order_id = created.json()["id"]
    # The clinic saw it as eligible. This is the stale snapshot a browser would be holding.
    seen = _eligible(client, clinician, provider.id, order_id).json()
    assert [row["lab_organization_id"] for row in seen] == [str(lab.id)]

    assert _set_active(client, lab_admin, lab.id, offering.id, False).status_code == 200

    refused = _assign(client, clinician, provider.id, order_id, lab.id)
    assert refused.status_code == 409, refused.text

    # **And it is refused for the right reason, named specifically.**
    #
    # Found by NC-OF-5, which removed `assign_by_provider`'s own active-offering check and left this
    # test green. Probed rather than assumed: the refusal still arrived, from a *second*,
    # independent defence -- `is_operationally_ready_in_session`. So an order can never be routed
    # for a disabled analysis even if that one line is deleted, which is a stronger safety property
    # than this test was claiming, and is worth knowing.
    #
    # What the specific check protects is **diagnostic precision**, which `selection.py` protects
    # deliberately and says so: *"Running this broader check first would subsume all of them behind
    # one generic refusal and destroy the diagnostic precision those messages carry."* Without it
    # the clinic is told the laboratory "cannot currently receive production work -- its facility
    # enrollment is blocked, or it lacks the staffing DHealth requires", which names neither the
    # analysis nor the cause. A clinician reading that cannot tell a switched-off analysis from a
    # blocked enrollment.
    detail = refused.json()["detail"]
    assert "offering" in detail, (
        f"the refusal must name the missing offering rather than fall back to the generic "
        f"readiness message, which names neither the analysis nor the cause: {detail!r}"
    )
    assert "staffing" not in detail, (
        f"a disabled analysis is not a staffing problem and must not be reported as one: {detail!r}"
    )

    # The order itself is untouched and still assignable once the laboratory accepts the work again.
    assert _set_active(client, lab_admin, lab.id, offering.id, True).status_code == 200
    accepted = _assign(client, clinician, provider.id, order_id, lab.id)
    assert accepted.status_code in (200, 201), accepted.text


def test_an_offering_is_not_authorization_to_send_work(
    client, keypair, organizations, catalog, directory, patients
):
    """Truth 3, in both directions, because collapsing it is the dangerous mistake.

    "Lab X performs CBC" and "Clinic Y may send work to Lab X" are different facts. An active
    offering with no active route is refused; an active route with no active offering is refused.
    Neither implies the other and neither alone is enough."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="AuthzLab", code="AUTHZ-1"
    )
    provider, clinician, patient = _clinic(
        client, private_key, organizations, patients, name="authz"
    )
    lab_admin, _i, _m = _staff(client, private_key, lab, "authzadmin", ["ORG_ADMIN"])

    created = _create_order(client, clinician, provider.id, patient.id, [entry.id])
    order_id = created.json()["id"]

    # Active offering, NO route: not eligible, and a direct assignment is refused.
    assert _eligible(client, clinician, provider.id, order_id).json() == []
    assert _assign(client, clinician, provider.id, order_id, lab.id).status_code == 409

    # Route configured but INACTIVE: still refused.
    _route(client, private_key, provider.id, lab.id, active=False)
    assert _eligible(client, clinician, provider.id, order_id).json() == []
    assert _assign(client, clinician, provider.id, order_id, lab.id).status_code == 409

    # Active route, offering DISABLED: refused for the other reason.
    _route(client, private_key, provider.id, lab.id, active=True)
    assert _set_active(client, lab_admin, lab.id, offering.id, False).status_code == 200
    assert _eligible(client, clinician, provider.id, order_id).json() == []
    assert _assign(client, clinician, provider.id, order_id, lab.id).status_code == 409

    # Both true: only now is the work routable.
    assert _set_active(client, lab_admin, lab.id, offering.id, True).status_code == 200
    assert _assign(client, clinician, provider.id, order_id, lab.id).status_code in (200, 201)


# ── history ──────────────────────────────────────────────────────────────────────────────────────


def test_disabling_an_offering_does_not_touch_an_order_already_routed(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """**The deactivation rule, and the most important test in this file.**

    Past work is not current configuration. Disabling an offering affects NEW ordering only: the
    already-routed order keeps its status, keeps its frozen service identity on its own
    ``LabOrderItem``, and continues through its workflow. Nothing is cancelled, nothing is detached,
    no snapshot is rewritten."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="HistLab", code="HIST-1"
    )
    provider, clinician, patient = _clinic(
        client, private_key, organizations, patients, name="hist"
    )
    _route(client, private_key, provider.id, lab.id)
    lab_admin, _i, _m = _staff(client, private_key, lab, "histadmin", ["ORG_ADMIN"])
    lab_tech, _ti, _tm = _staff(client, private_key, lab, "histtech", ["LAB_TECH"])

    created = _create_order(client, clinician, provider.id, patient.id, [entry.id])
    order_id = created.json()["id"]
    assert _assign(client, clinician, provider.id, order_id, lab.id).status_code in (200, 201)

    def order_state():
        response = client.get(
            f"/exchange/v1/organizations/{provider.id}/orders/{order_id}", headers=_auth(clinician)
        )
        assert response.status_code == 200, response.text
        return response.json()

    before = order_state()

    # Now disable the analysis this committed order is for.
    assert _set_active(client, lab_admin, lab.id, offering.id, False).status_code == 200

    after = order_state()
    assert after["status"] == before["status"], "a disabled offering must not change order status"
    assert after["status"] not in ("CANCELLED", "REJECTED")

    # The item's frozen service identity is byte-for-byte what it was.
    with sessions() as session:
        items = session.scalars(
            select(LabOrderItem).where(LabOrderItem.lab_order_id == uuid.UUID(order_id))
        ).all()
    assert len(items) == 1
    assert items[0].catalog_entry_id == entry.id
    assert items[0].catalog_code_snapshot == "HIST-1"
    assert items[0].catalog_display_name_snapshot == "Analyse HIST-1"

    # And the laboratory can still work it: the whole point of "existing orders remain valid".
    accepted = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ACCEPTED"


def test_the_laboratorys_own_queue_still_shows_work_for_a_disabled_analysis(
    client, keypair, organizations, catalog, directory, patients
):
    """The same rule from the laboratory's side: disabling an analysis must not hide work it already
    holds. A queue that dropped the order would be the same harm as cancelling it, reached by a
    different route."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="QueueLab", code="QUEUE-1"
    )
    provider, clinician, patient = _clinic(
        client, private_key, organizations, patients, name="queue"
    )
    _route(client, private_key, provider.id, lab.id)
    lab_admin, _i, _m = _staff(client, private_key, lab, "queueadmin", ["ORG_ADMIN"])
    lab_tech, _ti, _tm = _staff(client, private_key, lab, "queuetech", ["LAB_TECH"])

    created = _create_order(client, clinician, provider.id, patient.id, [entry.id])
    order_id = created.json()["id"]
    assert _assign(client, clinician, provider.id, order_id, lab.id).status_code in (200, 201)

    assert _set_active(client, lab_admin, lab.id, offering.id, False).status_code == 200

    queue = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders?status=SELECTED", headers=_auth(lab_tech)
    )
    assert queue.status_code == 200, queue.text
    assert order_id in [row["order_id"] for row in queue.json()]


# ── authorization ────────────────────────────────────────────────────────────────────────────────


def test_configuration_authority_is_facility_configure_and_no_new_capability_was_added():
    """`facility.configure` already meant this before the ticket, in words a facility administrator
    already reads. A `lab.offerings.manage` would be the speculative seam ADR-067 §3A forbids."""
    assert Capability.FACILITY_CONFIGURE in SYSTEM_ROLE_TEMPLATES[OrganizationRole.ORG_ADMIN]
    assert not any("offering" in capability.value.lower() for capability in Capability), (
        "no offering-specific capability may exist"
    )
    # And the whole `facility.*` namespace stays a single member, so a later `facility.offerings.*`
    # fails this test rather than passing review.
    facility_namespace = {c.value for c in Capability if c.value.startswith("facility.")}
    assert facility_namespace == {"facility.configure"}


def test_a_direct_grant_of_facility_configure_is_enough(
    client, keypair, organizations, catalog, directory
):
    """Capabilities, not job titles. A member with no administrative role at all, given
    `facility.configure` directly, can read and change offerings."""
    private_key, _ = keypair
    lab, _entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="GrantLab", code="GRANT-1"
    )
    # LAB_TECH carries `lab.queue.work` and nothing else -- the grant below is the whole authority.
    token, _identity, membership = _staff(client, private_key, lab, "grant", ["LAB_TECH"])
    assert _list_offerings(client, token, lab.id).status_code == 403

    granted = _set_capability(
        client, private_key, lab.id, membership, Capability.FACILITY_CONFIGURE.value, "GRANT"
    )
    assert granted.status_code in (200, 201), granted.text

    assert _list_offerings(client, token, lab.id).status_code == 200
    assert _set_active(client, token, lab.id, offering.id, False).status_code == 200


def test_an_explicit_deny_beats_the_role_template(
    client, keypair, organizations, catalog, directory
):
    """A DENY always wins, including over `ORG_ADMIN`'s template grant, and it must close the read
    and the mutation together rather than one of the two."""
    private_key, _ = keypair
    lab, _entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="DenyLab", code="DENY-1"
    )
    token, _identity, membership = _staff(client, private_key, lab, "deny", ["ORG_ADMIN"])
    assert _list_offerings(client, token, lab.id).status_code == 200

    denied = _set_capability(
        client, private_key, lab.id, membership, Capability.FACILITY_CONFIGURE.value, "DENY"
    )
    assert denied.status_code in (200, 201), denied.text

    assert _list_offerings(client, token, lab.id).status_code == 403
    assert _set_active(client, token, lab.id, offering.id, False).status_code == 403


def test_revoking_the_grant_takes_effect_on_the_same_token(
    client, keypair, organizations, catalog, directory
):
    """No re-login. The same bearer token that configured a moment ago is refused once the
    capability is cleared -- authority is evaluated per request, never cached in the token."""
    private_key, _ = keypair
    lab, _entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="RevokeLab", code="REVOKE-1"
    )
    token, _identity, membership = _staff(client, private_key, lab, "revoke", ["LAB_TECH"])
    assert _set_capability(
        client, private_key, lab.id, membership, Capability.FACILITY_CONFIGURE.value, "GRANT"
    ).status_code in (200, 201)
    assert _set_active(client, token, lab.id, offering.id, False).status_code == 200

    cleared = _clear_capability(
        client, private_key, lab.id, membership, Capability.FACILITY_CONFIGURE.value
    )
    assert cleared.status_code in (200, 204), cleared.text

    assert _list_offerings(client, token, lab.id).status_code == 403
    assert _set_active(client, token, lab.id, offering.id, True).status_code == 403


@pytest.mark.parametrize(
    ("role", "label"),
    [
        ("LAB_TECH", "lab.queue.work"),
        ("LAB_RESULT_RELEASER", "lab.result.release"),
        ("BILLING_CLERK", "billing.operate"),
    ],
)
def test_other_laboratory_authorities_cannot_configure_offerings(
    client, keypair, organizations, catalog, directory, role, label
):
    """Separation, one role per run so a failure names which one leaked.

    Working the queue, releasing a result and taking payment are three real jobs at a laboratory and
    none of them is deciding what the laboratory accepts. `billing.operate` in particular is worth
    pinning: a cashier is the person most likely to be handed "just one more admin thing"."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name=f"Sep{role}", code=f"SEP-{role[:4]}"
    )
    token, _i, _m = _staff(client, private_key, lab, f"sep{role.lower()}", [role])

    assert _list_offerings(client, token, lab.id).status_code == 403, label
    assert _set_active(client, token, lab.id, offering.id, False).status_code == 403, label
    assert _create_offering(client, token, lab.id, entry.id).status_code == 403, label


def test_billing_price_authority_alone_cannot_configure_offerings(
    client, keypair, organizations, catalog, directory
):
    """The sharpest separation on this screen, because the two panels sit side by side.

    `billing.configure_prices` sets what the facility charges. It does not decide what the facility
    accepts. Someone holding only the price authority sees the price list and must be refused the
    offerings -- which is exactly why `BillingPage` gates the two panels independently rather
    than on one 'may configure' boolean."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="PriceOnlyLab", code="PRICEONLY-1"
    )
    token, _identity, membership = _staff(client, private_key, lab, "priceonly", ["LAB_TECH"])
    assert _set_capability(
        client,
        private_key,
        lab.id,
        membership,
        Capability.BILLING_CONFIGURE_PRICES.value,
        "GRANT",
    ).status_code in (200, 201)

    assert _list_offerings(client, token, lab.id).status_code == 403
    assert _set_active(client, token, lab.id, offering.id, False).status_code == 403
    assert _create_offering(client, token, lab.id, entry.id).status_code == 403


def test_a_clinic_cannot_configure_the_destination_laboratory(
    client, keypair, organizations, catalog, directory, patients
):
    """The clinic sends work to this laboratory and has no membership there. 404, not 403 -- the
    anti-enumeration convention every tenant-scoped route in this package uses, so a non-member
    cannot learn that an organization id is real."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="DestLab", code="DEST-1"
    )
    provider, clinician, _patient = _clinic(
        client, private_key, organizations, patients, name="dest"
    )
    _route(client, private_key, provider.id, lab.id)

    assert _list_offerings(client, clinician, lab.id).status_code == 404
    assert _set_active(client, clinician, lab.id, offering.id, False).status_code == 404
    assert _create_offering(client, clinician, lab.id, entry.id).status_code == 404


def test_a_rival_laboratory_cannot_read_or_change_another_laboratorys_configuration(
    client, keypair, organizations, catalog, directory
):
    """A second laboratory with a real `ORG_ADMIN` membership of its own, probing every way it can.

    Including the case a naive tenant check misses: the rival's *own* laboratory in the path
    with the *victim's* offering id. That must 404 on the offering, not succeed because the
    membership check passed."""
    private_key, _ = keypair
    victim, v_entry, v_offering = _lab_with_offering(
        catalog, directory, organizations, name="VictimLab", code="VICTIM-1"
    )
    rival, _r_entry, _r_offering = _lab_with_offering(
        catalog, directory, organizations, name="RivalLab", code="RIVAL-1"
    )
    rival_admin, _i, _m = _staff(client, private_key, rival, "rivaladmin", ["ORG_ADMIN"])

    # The victim's organization in the path: refused on membership.
    victim_read = _list_offerings(client, rival_admin, victim.id)
    assert victim_read.status_code == 404
    assert _set_active(client, rival_admin, victim.id, v_offering.id, False).status_code == 404
    assert _create_offering(client, rival_admin, victim.id, v_entry.id).status_code == 404

    # The rival's OWN organization in the path, the victim's offering id in the body path segment.
    # The membership check passes here, so only the offering-belongs-to-this-lab check can
    # refuse it.
    crossed = _set_active(client, rival_admin, rival.id, v_offering.id, False)
    assert crossed.status_code == 404, crossed.text

    # Indistinguishable from an offering that does not exist at all.
    unknown = _set_active(client, rival_admin, rival.id, uuid.uuid4(), False)
    assert unknown.status_code == 404
    assert unknown.json() == crossed.json()

    # And the victim's offering is untouched by every one of the above.
    victim_admin, _vi, _vm = _staff(client, private_key, victim, "victimadmin", ["ORG_ADMIN"])
    assert _list_offerings(client, victim_admin, victim.id).json()[0]["active"] is True


def test_a_platform_administrator_configures_a_facility_and_that_is_existing_architecture(
    client, keypair, organizations, catalog, directory
):
    """Recorded rather than changed. `_require_facility_configure`'s own docstring says a platform
    administrator configures a facility's catalogue today and the wrapper preserves it.

    This ticket does **not** widen that and does not narrow it either -- narrowing platform
    authority is its own decision with its own blast radius, and the ticket's instruction was to
    follow existing capability architecture. The test exists so the behaviour is visible and a
    future change to it is deliberate."""
    private_key, _ = keypair
    lab, _entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="PlatLab", code="PLAT-1"
    )
    admin = make_token(private_key, subject="lonely-platform-admin", roles=[PLATFORM_ADMIN_ROLE])

    assert _list_offerings(client, admin, lab.id).status_code == 200
    assert _set_active(client, admin, lab.id, offering.id, False).status_code == 200


# ── audit ────────────────────────────────────────────────────────────────────────────────────────


def test_enabling_and_disabling_are_audited_with_actor_and_outcome(
    client, keypair, organizations, catalog, directory, sessions
):
    """Operational configuration is accountable: who turned this analysis off, and when.

    `outcome` carries the distinction between a real change and a no-op, which is what makes the
    audit trail answerable rather than merely present."""
    private_key, _ = keypair
    lab, entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="AuditLab", code="AUDIT-1"
    )
    token, identity, _m = _staff(client, private_key, lab, "auditadmin", ["ORG_ADMIN"])

    def events(event_type: str):
        with sessions() as session:
            return session.scalars(
                select(ExchangeSecurityAuditEvent)
                .where(
                    ExchangeSecurityAuditEvent.event_type == event_type,
                    ExchangeSecurityAuditEvent.resource_id == offering.id,
                )
                .order_by(ExchangeSecurityAuditEvent.created_at.asc())
            ).all()

    assert _set_active(client, token, lab.id, offering.id, False).status_code == 200
    changes = events("lab_offering_status_changed")
    assert len(changes) == 1
    assert str(changes[0].actor_identity_id) == identity
    assert changes[0].organization_id == lab.id
    assert changes[0].resource_type == "lab_offering"
    assert changes[0].outcome == "updated"
    assert changes[0].created_at is not None

    # The audit row names the configuration, never the clinical subject: this is facility
    # configuration and no patient is involved in it.
    assert changes[0].membership_id is None
    assert changes[0].subject_identity_id is None

    # A new offering is audited too, against the catalogue entry it points at.
    other, _ = catalog.create_entry(code="AUDIT-2", display_name="Analyse AUDIT-2")
    created = _create_offering(client, token, lab.id, other.id)
    assert created.status_code == 201
    with sessions() as session:
        creations = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "lab_offering_created",
                ExchangeSecurityAuditEvent.resource_id == uuid.UUID(created.json()["id"]),
            )
        ).all()
    assert len(creations) == 1
    assert creations[0].outcome == "created"
    assert str(creations[0].actor_identity_id) == identity


def test_enabling_twice_writes_one_audit_row(
    client, keypair, organizations, catalog, directory, sessions
):
    """**The defect this ticket fixed, pinned at the level a reader can check.**

    A repeated disable is a no-op and must be audited as one. `set_offering_active` reports
    ``"unchanged"`` and `api.py` writes that into the audit row, so a second identical request does
    not become a second claim that somebody switched the analysis off.

    Sequentially this was already true before the fix. What was *not* true, and is what
    `PG-OF-1a` proves, is that it holds when two administrators do it at the same instant -- the
    comparison was made against an unlocked read, so both could decide they were the one making the
    change."""
    private_key, _ = keypair
    lab, _entry, offering = _lab_with_offering(
        catalog, directory, organizations, name="OnceLab", code="ONCE-1"
    )
    token, _identity, _m = _staff(client, private_key, lab, "onceadmin", ["ORG_ADMIN"])

    assert _set_active(client, token, lab.id, offering.id, False).status_code == 200
    for _ in range(3):
        repeat = _set_active(client, token, lab.id, offering.id, False)
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["active"] is False

    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "lab_offering_status_changed",
                ExchangeSecurityAuditEvent.resource_id == offering.id,
            )
        ).all()
    outcomes = [row.outcome for row in rows]
    assert outcomes.count("updated") == 1, (
        f"one state change must produce exactly one 'updated' audit row, got {outcomes}"
    )
    assert outcomes.count("unchanged") == 3


# ── the clinic-facing projection ─────────────────────────────────────────────────────────────────


def test_the_clinic_never_receives_laboratory_configuration(
    client, keypair, organizations, catalog, directory, patients
):
    """What a clinic may know is "which of my authorized laboratories can perform this order", and
    the eligible-labs projection is exactly that -- not a catalogue, not a configuration dump, and
    not a price list.

    An offering id, an `active` flag, a `local_test_code` or another laboratory's existence
    appearing here would each be a different kind of leak, so the assertion is over the whole
    serialized response rather than a field list."""
    private_key, _ = keypair
    lab, entry, _offering = _lab_with_offering(
        catalog, directory, organizations, name="ProjLab", code="PROJ-1"
    )
    provider, clinician, patient = _clinic(
        client, private_key, organizations, patients, name="proj"
    )
    _route(client, private_key, provider.id, lab.id)

    created = _create_order(client, clinician, provider.id, patient.id, [entry.id])
    order_id = created.json()["id"]
    response = _eligible(client, clinician, provider.id, order_id)
    assert response.status_code == 200

    serialized = response.text.lower()
    for forbidden in ("offering", "local_test_code", "active", "amount", "price", "currency"):
        assert forbidden not in serialized, (
            f"{forbidden!r} is laboratory configuration or price and must not reach the clinic's "
            f"eligibility projection"
        )

    # A clinic has no route to laboratory configuration at all, which is the stronger statement.
    assert _list_offerings(client, clinician, lab.id).status_code == 404


def test_the_offerings_read_is_filtered_by_laboratory_in_the_repository_itself(
    organizations, catalog, directory, sessions
):
    """**Added because NC-OF-1 did not redden**, which was the control doing its job.

    The HTTP route refuses a foreign laboratory on the *membership* check before the repository is
    ever reached, so deleting the repository's own ownership filter changed no HTTP outcome and the
    rival-laboratory test stayed green. That filter is defence in depth behind the gate -- and
    defence in depth that nothing tests is defence in depth that can be deleted by accident.

    So this asserts it directly, at the repository, where it is the only thing standing between a
    caller and every laboratory's configuration. Two laboratories, each offering its own analysis:
    each read returns exactly its own row."""
    lab_a, entry_a, offering_a = _lab_with_offering(
        catalog, directory, organizations, name="FilterA", code="FILTER-A"
    )
    lab_b, entry_b, offering_b = _lab_with_offering(
        catalog, directory, organizations, name="FilterB", code="FILTER-B"
    )

    a_rows = catalog.list_offerings_for_lab(lab_a.id)
    b_rows = catalog.list_offerings_for_lab(lab_b.id)

    assert [row.id for row in a_rows] == [offering_a.id]
    assert [row.id for row in b_rows] == [offering_b.id]
    assert all(row.lab_organization_id == lab_a.id for row in a_rows)
    assert all(row.lab_organization_id == lab_b.id for row in b_rows)
    # The sharpest form: neither read may contain the other's catalogue entry.
    assert entry_b.id not in {row.catalog_entry_id for row in a_rows}
    assert entry_a.id not in {row.catalog_entry_id for row in b_rows}

    # And a laboratory with nothing configured reads empty rather than everything.
    empty_lab = make_lab(organizations, name="FilterEmpty")
    assert catalog.list_offerings_for_lab(empty_lab.id) == []


def test_the_model_declares_one_offering_per_laboratory_and_service():
    """**Added because NC-OF-4 did not redden**, and the reason is worth recording.

    `PG-OF-5` asserts the constraint against the live PostgreSQL schema, which alembic created. A
    mutation to the SQLAlchemy model therefore changes nothing that test can see -- the database
    still has the constraint. The model and the migration are two separate statements of the same
    rule and both need a guard.

    This is the model's half. It reddens if `UniqueConstraint` becomes a plain `Index`, which is
    exactly the shape a well-meaning "this was slowing inserts down" change would take."""
    unique_pairs = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in LabOffering.__table__.constraints
        if type(constraint).__name__ == "UniqueConstraint"
    }
    assert ("catalog_entry_id", "lab_organization_id") in unique_pairs, (
        "the model must declare one offering per (laboratory, service) -- without it two rows can "
        "disagree about whether the laboratory accepts the analysis"
    )
    # Scoped to the laboratory, never global: the same canonical analysis is offered by many
    # laboratories and each one's availability is its own fact.
    assert ("catalog_entry_id",) not in unique_pairs
    assert not LabOffering.__table__.c.catalog_entry_id.unique
