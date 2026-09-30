"""DHEALTH-LAB-RECEPTION-001: laboratory work intake, over HTTP.

**Most of what this file tests already worked at `3e4b69b`.** DH-EX-006 built the queue, the detail
read, accept and reject; DHEALTH-ACCESS-CAPABILITIES-001 made the gate a capability; DHEALTH-LAB-
BLOCKED-001 added `BLOCKED`. This ticket changed exactly one behaviour — the incoming queue's sort
order — and otherwise exists to **qualify** the intake boundary at the level the product claim
needs, which `test_exchange_lab_workflow.py` does not: that file proves the *domain* (44 tests,
largely against the repository), and the authority and privacy claims a laboratory worker's day
depends on are asserted here, through the edge, against real HTTP status codes.

── What this file is actually for ──

Three claims, none of which a domain test can make:

1. **Only this laboratory's routed work is reachable**, by queue, by detail, by guessed UUID, and by
   a second laboratory holding a real membership somewhere else.
2. **Intake authority is `lab.queue.work` and nothing else** — not a job title, not release
   authority, not billing authority, not the clinic that created the order, not a platform
   administrator.
3. **The intake projection carries no clinic clinical content**, and carries none *structurally*
   rather than by filtering: `LabWorkflowOrderRecord` has no field naming a visit, a note, a review
   or a result, so there is no filter anybody can forget to apply.

── The ordering change, and why it is the one behavioural change ──

`list_for_lab` sorted every view `updated_at DESC`. For the six *review* views that is right. For
`SELECTED` — the only actionable backlog a laboratory holds — it buried the order that had been
waiting longest under everything that arrived after it. It is now `selected_at ASC`, the write-once
routing instant. `test_incoming_queue_is_oldest_routed_first` and its sibling are what pin both
halves.

**No duration is computed anywhere.** Ordering by a recorded instant is not deriving a number from
it; `AGENTS.md` (2026-09-27) and `docs/NATIVE-OPERATING-PRODUCT.md` §38 forbid the second, and
DHEALTH-FRONT-DESK-001 refused the same thing on the clinic side.

All patient data is synthetic. No real patient data is used anywhere in this file.
"""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import LabWorkflowOrderResponse, build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.capabilities import SYSTEM_ROLE_TEMPLATES, Capability
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.lab_workflow import LabWorkflowOrderRecord
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    LabOrderStatus,
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


# -- harness --------------------------------------------------------------------------------------
#
# Deliberately this file's own, not imported from `test_exchange_lab_workflow.py`: importing one
# test module into another couples two files' fixtures and is not a pattern this package uses.


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
    """Grant or deny one capability, exactly as an administrator does through Team & Access."""
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


def _priced_lab(catalog, directory, organizations, *, name: str, entries):
    lab = make_lab(organizations, name=name)
    for index, entry in enumerate(entries):
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("1000") * (index + 1),
            currency="DJF",
            expected_current_price_id=None,
        )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    return lab


def _route_order(
    client, private_key, organizations, catalog, directory, patients, *, name: str, lab=None
):
    """A clinic order routed to ``lab`` (or a fresh one), left in ``SELECTED`` — the intake state.

    Returns ``(provider, lab, order_id, clinician_token)``. The routing is a real patient selection
    through the patient-access grant, not a direct database write, so ``LabSelection.selected_at``
    is the genuine routing instant this ticket sorts on."""
    provider = make_provider(organizations, name=f"Prov-{name}")
    clinician_identity = _bootstrap(client, private_key, f"clin-{name}")
    _attach(client, private_key, provider.id, clinician_identity, f"clin-{name}", ["CLINICIAN"])
    clinician_token = make_token(private_key, subject=f"clin-{name}")

    patient_id = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Patient Synthetique {name}",
    ).id

    entry, _ = catalog.create_entry(code=f"DHLR-{name}", display_name=f"Analyse {name}")
    if lab is None:
        lab = _priced_lab(catalog, directory, organizations, name=f"Lab-{name}", entries=[entry])
    else:
        # Reusing a lab: it must also be able to price this new catalogue entry, or the patient
        # selection below will not offer it.
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("1500"),
            currency="DJF",
            expected_current_price_id=None,
        )

    created = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(entry.id)]},
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    access = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access.status_code == 201, access.text
    access_token = access.json()["access_token"]

    options = client.get("/exchange/v1/patient/order/lab-options", headers=_auth(access_token))
    assert options.status_code == 200, options.text
    matching = [o for o in options.json()["options"] if o["lab"]["id"] == str(lab.id)]
    assert matching, f"lab {lab.id} was not offered: {options.json()}"
    price_ids = [line["current_price_id"] for line in matching[0]["prices"]]

    selection = client.post(
        "/exchange/v1/patient/order/selection",
        headers={**_auth(access_token), "Idempotency-Key": _key()},
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selection.status_code in (200, 201), selection.text
    return provider, lab, order_id, clinician_token


def _lab_worker(client, private_key, lab, name: str, roles: list[str] | None = None):
    identity = _bootstrap(client, private_key, f"worker-{name}")
    membership = _attach(
        client,
        private_key,
        lab.id,
        identity,
        f"worker-{name}",
        roles if roles is not None else ["LAB_TECH"],
    )
    return make_token(private_key, subject=f"worker-{name}"), identity, membership


def _queue(client, token, lab_id, *, status_filter: str | None = "SELECTED"):
    query = f"?status={status_filter}" if status_filter else ""
    return client.get(
        f"/exchange/v1/organizations/{lab_id}/lab-orders{query}", headers=_auth(token)
    )


def _accept(client, token, lab_id, order_id):
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/accept", headers=_auth(token)
    )


def _reject(client, token, lab_id, order_id):
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/reject", headers=_auth(token)
    )


# ── the incoming queue ───────────────────────────────────────────────────────────────────────────


def test_incoming_queue_holds_this_laboratory_s_routed_work(
    client, keypair, organizations, catalog, directory, patients
):
    """The queue is a projection over `SELECTED` — work routed here and not yet answered."""
    private_key, _ = keypair
    _provider, lab, order_id, _clin = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Q1"
    )
    token, _identity, _membership = _lab_worker(client, private_key, lab, "q1")

    response = _queue(client, token, lab.id)
    assert response.status_code == 200, response.text
    assert [row["order_id"] for row in response.json()] == [order_id]
    assert response.json()[0]["status"] == LabOrderStatus.SELECTED.value


def test_incoming_queue_excludes_another_laboratory_s_work(
    client, keypair, organizations, catalog, directory, patients
):
    """Tenant isolation, from the read side. The other laboratory's order is real and routed — it is
    simply not this one's, and `LabSelection` is the only fact that decides."""
    private_key, _ = keypair
    _p1, mine, my_order, _c1 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="QMine"
    )
    _p2, theirs, their_order, _c2 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="QTheirs"
    )
    token, _i, _m = _lab_worker(client, private_key, mine, "qmine")

    rows = _queue(client, token, mine.id).json()
    assert [row["order_id"] for row in rows] == [my_order]
    assert their_order not in [row["order_id"] for row in rows]


def test_incoming_queue_drops_work_once_it_is_answered(
    client, keypair, organizations, catalog, directory, patients
):
    """Accepting or declining removes the item from *incoming* — the queue means "owed an answer",
    not "everything this laboratory has ever been sent"."""
    private_key, _ = keypair
    _p, lab, accepted_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="QAcc"
    )
    _p2, _lab2, declined_id, _c2 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="QDec", lab=lab
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "qanswered")

    assert len(_queue(client, token, lab.id).json()) == 2
    assert _accept(client, token, lab.id, accepted_id).status_code == 200
    assert _reject(client, token, lab.id, declined_id).status_code == 200

    assert _queue(client, token, lab.id).json() == []
    # ...and neither vanished: both are still this laboratory's history.
    assert {
        row["order_id"] for row in _queue(client, token, lab.id, status_filter=None).json()
    } == {
        accepted_id,
        declined_id,
    }


def test_incoming_queue_is_oldest_routed_first(
    client, keypair, organizations, catalog, directory, patients
):
    """**The one behaviour this ticket changed.**

    A backlog is owed work, so the order that has been waiting longest comes first. Before
    DHEALTH-LAB-RECEPTION-001 every view sorted `updated_at DESC`, which buried it under everything
    that arrived after it.

    Routing order is established by creating them in sequence, and `LabSelection.selected_at` is
    written once at selection with no `onupdate`, so nothing later can reshuffle this."""
    private_key, _ = keypair
    _p, lab, first_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Ord1"
    )
    _p2, _l2, second_id, _c2 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Ord2", lab=lab
    )
    _p3, _l3, third_id, _c3 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Ord3", lab=lab
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "ordering")

    rows = _queue(client, token, lab.id).json()
    assert [row["order_id"] for row in rows] == [first_id, second_id, third_id]
    assert rows[0]["selected_at"] <= rows[1]["selected_at"] <= rows[2]["selected_at"]


def test_the_review_views_still_sort_newest_activity_first(
    client, keypair, organizations, catalog, directory, patients
):
    """The control for the test above, and the reason the sort is asymmetric rather than reversed
    everywhere: `Terminés`/`Refusés`/`En cours` answer "what changed recently", and reversing them
    would be a regression. Two orders are accepted oldest-first, so the *newest activity* is on the
    order that was routed second."""
    private_key, _ = keypair
    _p, lab, first_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Rev1"
    )
    _p2, _l2, second_id, _c2 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Rev2", lab=lab
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "review")

    assert _accept(client, token, lab.id, first_id).status_code == 200
    assert _accept(client, token, lab.id, second_id).status_code == 200

    rows = _queue(client, token, lab.id, status_filter="ACCEPTED").json()
    # Newest activity first: `second_id` was accepted last.
    assert [row["order_id"] for row in rows] == [second_id, first_id]


# ── privacy ──────────────────────────────────────────────────────────────────────────────────────

#: The complete field list of the intake projection. Changing this set is a privacy decision.
INTAKE_FIELDS = {
    "order_id",
    "status",
    "selected_at",
    "updated_at",
    "patient_display_name",
    "patient",
    "ordering_provider_name",
    "requested_tests",
    "collection_recorded_at",
    "active_block",
}

#: Anything naming clinic clinical content. Asserted against the *record* and the *response* type,
#: and against a live body — see `test_the_intake_projection_carries_no_clinic_clinical_content`.
FORBIDDEN_MARKERS = (
    "visit",
    "reason",
    "note",
    "review",
    "diagnos",
    "observation",
    "result_value",
    "release",
    "invoice",
    "payment",
    "balance",
    "contact",
)


def test_the_intake_projection_carries_no_clinic_clinical_content(
    client, keypair, organizations, catalog, directory, patients
):
    """§B: the laboratory sees what it needs to do the work, and structurally cannot see the rest.

    Three layers, and the **first is the load-bearing one**: `LabWorkflowOrderRecord` has no field
    naming a visit, a note, a review or a result value, so this is not a filter somebody can forget
    to apply — there is nothing to filter. The response model is checked for the same reason, and a
    live body is checked so the route cannot be returning something wider that happens to validate.

    `active_block` is exempt from the marker scan by name only: it is DHEALTH-LAB-BLOCKED-001's own
    operational episode, written by this laboratory about its own work, and its reason vocabulary is
    explicitly operational rather than clinical (`LabOrderBlockReason`'s docstring)."""
    record_fields = {field.name for field in dataclasses.fields(LabWorkflowOrderRecord)}
    response_fields = set(LabWorkflowOrderResponse.model_fields)
    assert response_fields == INTAKE_FIELDS

    for name in record_fields | response_fields:
        if name in ("active_block", "collection_recorded_at"):
            continue
        assert not any(marker in name.lower() for marker in FORBIDDEN_MARKERS), (
            f"{name!r} names clinic clinical content the laboratory must not receive"
        )

    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Priv"
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "priv")

    body = _queue(client, token, lab.id).json()[0]
    assert set(body) == INTAKE_FIELDS
    detail = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(token)
    )
    assert detail.status_code == 200
    assert set(detail.json()) == INTAKE_FIELDS


def test_a_laboratory_cannot_hold_patient_directory_authority_at_all(client, keypair):
    """§B: "a lab worker must not need `patient.directory.read` merely to receive work".

    Stronger than not-needing-it: the capability is provider-only in the catalog, so the resolver's
    organization-type filter means a laboratory membership **cannot hold it** even if somebody
    granted it. And `LAB_TECH` carries exactly one capability, so intake authority is not a bundle
    that quietly includes a directory read."""
    from daher_adapter.exchange.capabilities import CAPABILITY_BY_KEY, grantable_capabilities
    from daher_adapter.exchange.models import OrganizationType

    directory_read = CAPABILITY_BY_KEY[Capability.PATIENT_DIRECTORY_READ]
    assert OrganizationType.LAB not in directory_read.organization_types
    assert Capability.PATIENT_DIRECTORY_READ not in grantable_capabilities(OrganizationType.LAB)
    assert SYSTEM_ROLE_TEMPLATES[OrganizationRole.LAB_TECH] == frozenset(
        {Capability.LAB_QUEUE_WORK}
    )


def test_a_guessed_order_id_from_another_laboratory_is_a_404(
    client, keypair, organizations, catalog, directory, patients
):
    """§B: a real, valid UUID belonging to another laboratory's routed order.

    404 and not 403, matching this package's anti-enumeration convention everywhere else:
    confirming the id is real would itself be the leak. `get_for_lab` proves ownership through
    `LabSelection`, not through `LabOrder.id`, which is what makes this a 404 rather than a body
    somebody else can read."""
    private_key, _ = keypair
    _p1, mine, _my_order, _c1 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="GuessMine"
    )
    _p2, _theirs, their_order, _c2 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="GuessTheirs"
    )
    token, _i, _m = _lab_worker(client, private_key, mine, "guess")

    detail = client.get(
        f"/exchange/v1/organizations/{mine.id}/lab-orders/{their_order}", headers=_auth(token)
    )
    assert detail.status_code == 404, detail.text
    # ...and the mutations agree, so a guess cannot act either.
    assert _accept(client, token, mine.id, their_order).status_code == 404
    assert _reject(client, token, mine.id, their_order).status_code == 404


# ── authorization ────────────────────────────────────────────────────────────────────────────────


def test_a_direct_lab_queue_work_grant_permits_intake_with_no_role(
    client, keypair, organizations, catalog, directory, patients
):
    """§C: the member holds **no role template** and three words of authority.

    The architectural claim reduced to a fixture: if any gate asked a job title, this member could
    not read the queue, let alone accept."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Grant"
    )
    token, _identity, membership = _lab_worker(client, private_key, lab, "grant", roles=[])

    assert _queue(client, token, lab.id).status_code == 403
    granted = _set_capability(
        client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value, "GRANT"
    )
    assert granted.status_code == 200, granted.text

    assert _queue(client, token, lab.id).status_code == 200
    assert _accept(client, token, lab.id, order_id).status_code == 200


def test_an_explicit_deny_beats_the_role_template(
    client, keypair, organizations, catalog, directory, patients
):
    """§C: the `LAB_TECH` role stays assigned and the authority disappears; clearing it returns."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Deny"
    )
    token, _identity, membership = _lab_worker(client, private_key, lab, "deny")

    assert _queue(client, token, lab.id).status_code == 200
    _set_capability(
        client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value, "DENY"
    )

    assert _queue(client, token, lab.id).status_code == 403
    assert _accept(client, token, lab.id, order_id).status_code == 403

    _clear_capability(client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value)
    assert _queue(client, token, lab.id).status_code == 200


def test_revocation_takes_effect_on_the_next_request_with_the_same_token(
    client, keypair, organizations, catalog, directory, patients
):
    """§C: authority is read per request, not carried in the token. No re-login anywhere here."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Revoke"
    )
    token, _identity, membership = _lab_worker(client, private_key, lab, "revoke", roles=[])
    _set_capability(
        client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value, "GRANT"
    )
    assert _queue(client, token, lab.id).status_code == 200

    _clear_capability(client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value)
    # Same token object, no refresh.
    assert _queue(client, token, lab.id).status_code == 403
    assert _accept(client, token, lab.id, order_id).status_code == 403


def test_release_authority_alone_does_not_grant_intake(
    client, keypair, organizations, catalog, directory, patients
):
    """§C/ADR-044: entry and release are separate authorities and neither implies the other.

    A releaser can publish results and cannot take work in."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Rel"
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "rel", roles=["LAB_RESULT_RELEASER"])

    assert _queue(client, token, lab.id).status_code == 403
    assert _accept(client, token, lab.id, order_id).status_code == 403
    assert _reject(client, token, lab.id, order_id).status_code == 403


def test_billing_authority_alone_does_not_grant_intake(
    client, keypair, organizations, catalog, directory, patients
):
    """§C/ADR-066: billing is independent, in both directions. A cashier at this laboratory cannot
    take work in, and nothing about intake requires `billing.operate`."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Bill"
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "bill", roles=["BILLING_CLERK"])

    assert _queue(client, token, lab.id).status_code == 403
    assert _accept(client, token, lab.id, order_id).status_code == 403


def test_the_clinic_that_created_the_order_cannot_act_as_the_destination_laboratory(
    client, keypair, organizations, catalog, directory, patients
):
    """§C: creating the work does not confer the authority to receive it.

    404 rather than 403 — the clinician has no relationship at all to the laboratory organization,
    which is this package's anti-enumeration convention."""
    private_key, _ = keypair
    _provider, lab, order_id, clinician_token = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Creator"
    )

    assert _queue(client, clinician_token, lab.id).status_code == 404
    assert _accept(client, clinician_token, lab.id, order_id).status_code == 404
    assert _reject(client, clinician_token, lab.id, order_id).status_code == 404


def test_another_laboratory_cannot_act_on_this_laboratory_s_order(
    client, keypair, organizations, catalog, directory, patients
):
    """§C: a real laboratory worker, with a real `LAB_TECH` membership — somewhere else.

    Both spellings are refused: naming their own organization in the path (the order is not theirs,
    404 by assignment) and naming the destination laboratory (they are not a member, 404 by
    anti-enumeration)."""
    private_key, _ = keypair
    _p1, mine, my_order, _c1 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="XMine"
    )
    _p2, theirs, _their_order, _c2 = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="XTheirs"
    )
    intruder, _i, _m = _lab_worker(client, private_key, theirs, "intruder")

    assert _accept(client, intruder, theirs.id, my_order).status_code == 404
    assert _accept(client, intruder, mine.id, my_order).status_code == 404
    assert _queue(client, intruder, mine.id).status_code == 404


def test_a_platform_administrator_does_not_receive_laboratory_work(
    client, keypair, organizations, catalog, directory, patients
):
    """§C/ADR-063: a network administrator is not laboratory staff.

    `require_lab_tech` deliberately admits no platform bypass, unlike the administrative gates."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Plat"
    )
    platform = make_token(private_key, subject="platform-intake", roles=[PLATFORM_ADMIN_ROLE])

    assert _queue(client, platform, lab.id).status_code == 403
    assert _accept(client, platform, lab.id, order_id).status_code == 403


def test_intake_is_governed_by_lab_queue_work_and_no_new_capability_was_added(client):
    """§17: this ticket added **no capability**. `lab.queue.work` already expressed the authority.

    Pinned as a test because "we did not add one" is the kind of claim that quietly stops being true
    — and because the catalog is the one place a fifth laboratory capability would appear."""
    lab_capabilities = {
        capability for capability in Capability if capability.value.startswith("lab.")
    }
    assert lab_capabilities == {Capability.LAB_QUEUE_WORK, Capability.LAB_RESULT_RELEASE}
    assert not any("reception" in c.value or "intake" in c.value for c in Capability)


# ── accept and decline ───────────────────────────────────────────────────────────────────────────


def test_accepting_moves_the_same_order_and_writes_one_audit_row(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """§25: the same `LabOrder` continues — no copy, no reception object — and the audit answers
    which laboratory, who, when, and against what."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Acc"
    )
    token, identity, _m = _lab_worker(client, private_key, lab, "acc")

    accepted = _accept(client, token, lab.id, order_id)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["order_id"] == order_id, "the same work item, not a copy"
    assert accepted.json()["status"] == LabOrderStatus.ACCEPTED.value

    with sessions() as session:
        rows = list(
            session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.resource_id == uuid.UUID(order_id),
                    ExchangeSecurityAuditEvent.event_type == "lab_order_accepted",
                )
            ).all()
        )
    assert len(rows) == 1
    assert str(rows[0].actor_identity_id) == identity
    assert str(rows[0].organization_id) == str(lab.id)
    assert rows[0].resource_type == "lab_order"


def test_a_retried_acceptance_neither_transitions_twice_nor_audits_twice(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """§27: a lost-response retry is the ordinary case, not an error.

    The second call returns the current state with a 200 and writes nothing — the mechanism
    DH-EX-006 already built, pinned here because this ticket's claim depends on it."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Retry"
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "retry")

    first = _accept(client, token, lab.id, order_id)
    second = _accept(client, token, lab.id, order_id)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["status"] == LabOrderStatus.ACCEPTED.value

    with sessions() as session:
        count = len(
            list(
                session.scalars(
                    select(ExchangeSecurityAuditEvent).where(
                        ExchangeSecurityAuditEvent.resource_id == uuid.UUID(order_id),
                        ExchangeSecurityAuditEvent.event_type == "lab_order_accepted",
                    )
                ).all()
            )
        )
    assert count == 1, "a replay must not produce a second authoritative audit row"


def test_declining_is_terminal_and_does_not_return_to_incoming(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """§26: a decline is recorded, auditable, and does not quietly become incoming work again.

    **The existing decline carries no reason, and this ticket did not add one.** `reject()` is a
    pure `SELECTED -> REJECTED` transition: there is no reason column, no taxonomy, and no
    clinic-visible reason field. Inventing one here would be the "obviously should" instinct
    `AGENTS.md` names — and the repository already has **D-11** open precisely because its *other*
    laboratory reason vocabulary (block reasons) has not been validated by anyone who works in a
    laboratory. Recorded as a gap in `PROJECT_STATE.md` rather than filled."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Dec"
    )
    token, identity, _m = _lab_worker(client, private_key, lab, "dec")

    declined = _reject(client, token, lab.id, order_id)
    assert declined.status_code == 200, declined.text
    assert declined.json()["status"] == LabOrderStatus.REJECTED.value
    assert _queue(client, token, lab.id).json() == [], "a decline must leave the incoming queue"

    # It is still there as history, and accepting it afterwards is a conflict, not a silent revival.
    assert _accept(client, token, lab.id, order_id).status_code == 409

    with sessions() as session:
        rows = list(
            session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.resource_id == uuid.UUID(order_id),
                    ExchangeSecurityAuditEvent.event_type == "lab_order_rejected",
                )
            ).all()
        )
    assert len(rows) == 1
    assert str(rows[0].actor_identity_id) == identity


def test_the_decline_endpoint_accepts_no_body_and_records_no_reason(
    client, keypair, organizations, catalog, directory, patients
):
    """The negative half of the finding above, pinned so a later contributor who assumes a reason
    exists is corrected by a test rather than by reading the whole module.

    If a reason mechanism is ever added it must be a deliberate decision with a laboratory
    participant, and this assertion is what will fail first."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="NoReason"
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "noreason")

    # A body is simply ignored: the route declares no request model at all.
    declined = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/reject",
        headers=_auth(token),
        json={"reason": "SPECIMEN_UNUSABLE", "note": "invented"},
    )
    assert declined.status_code == 200, declined.text
    assert set(declined.json()) == INTAKE_FIELDS
    assert "reason" not in declined.json()


def test_an_invalid_intake_transition_is_a_409_not_a_500(
    client, keypair, organizations, catalog, directory, patients
):
    """§A: accepting work that has already advanced is a conflict about state, deterministically."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Inv"
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "inv")

    assert _accept(client, token, lab.id, order_id).status_code == 200
    # Already ACCEPTED: rejecting is now illegal, and says so rather than silently no-op'ing.
    assert _reject(client, token, lab.id, order_id).status_code == 409


def test_accepted_work_continues_into_the_existing_collection_and_processing_flow(
    client, keypair, organizations, catalog, directory, patients
):
    """§33: intake hands the **same** order to the workflow that already existed.

    Collection then `start_processing`, in that order, because DH-EX-011 requires a recorded
    milestone before a new `ACCEPTED -> IN_PROGRESS` transition. Nothing in this ticket changed
    either rule; this proves intake did not quietly bypass them."""
    private_key, _ = keypair
    _p, lab, order_id, _c = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="Down"
    )
    token, _i, _m = _lab_worker(client, private_key, lab, "down")

    assert _accept(client, token, lab.id, order_id).status_code == 200

    # Start-processing before a collection milestone is refused -- the existing invariant.
    early = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(token),
    )
    assert early.status_code == 409, early.text

    collected = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers={**_auth(token), "Idempotency-Key": _key()},
    )
    assert collected.status_code in (200, 201), collected.text

    started = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(token),
    )
    assert started.status_code == 200, started.text
    assert started.json()["order_id"] == order_id, "still the same order"
    assert started.json()["status"] == LabOrderStatus.IN_PROGRESS.value
