"""DHEALTH-BILLING-001: facility billing, end to end over HTTP.

Driven through ``TestClient`` against ``build_exchange_router`` rather than against
``BillingRepository``, for the reason the visit and staff-access suites both state: the claim being
earned is *"an authorized clerk can produce a truthful invoice and take payment"*, and a test
calling the repository would prove the arithmetic while proving nothing about whether anyone can
reach it.

The properties under test are the ones that make billing dangerous rather than merely broken:

* **An unpaid bill can never stop clinical work.** BILL-SAFETY-1 runs a whole order to release and
  review with an outstanding balance sitting on the patient. This is the ticket's P0 invariant and
  the reason NC-BILL-4 is mandatory.
* **An organization may bill only for what it performed.** Clinic A cannot invoice the test Lab
  B ran
  on a routed order, even holding real billing authority and a real order item id (ADR-066).
* **Historical prices do not move.** A price changed today does not change what an invoice said
  yesterday, because the line froze its own amount.
* **Money is exact.** ``Decimal`` throughout, no float anywhere, and a balance that reaches exactly
  zero.
* **Overpayment is refused**, and financial history cannot be edited or deleted.
* **Financial authority is not clinical authority, in both directions.** A clerk reaches no clinical
  endpoint; a clinician, an administrator and a releaser reach no billing endpoint.

All patient, service and price data is synthetic. No real patient data is used in this file.

"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import (
    activate_result_template,
    make_lab,
    make_priced_offering,
    make_provider,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    Invoice,
    InvoiceLine,
    LabOrderItem,
    Payment,
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
def catalog(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> CatalogRepository:
    return CatalogRepository(sessions, organizations)


# -- harness --------------------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _key() -> str:
    return str(uuid.uuid4())


def _idem(token: str) -> dict[str, str]:
    return {**_auth(token), "Idempotency-Key": _key()}


def _bootstrap(client: TestClient, private_key, subject: str, **kwargs) -> tuple[str, str]:
    token = make_token(private_key, subject=subject, **kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"], token


def _attach(
    client: TestClient, admin_token: str, organization_id: str, identity_id: str, roles: list[str]
) -> str:
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


def _file_audit(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line
    ]


@pytest.fixture
def world(client: TestClient, keypair, organizations: OrganizationRepository, catalog):
    """Two clinics, one laboratory, and one persona per authority that must be told apart.

    - Clinique A (PROVIDER): ``admin_a`` (ORG_ADMIN), ``clerk_a`` (BILLING_CLERK only),
      ``clinician`` (CLINICIAN only)
    - Clinique Z (PROVIDER): ``clerk_z`` (BILLING_CLERK) -- a real clerk of the wrong clinic, which
      is what makes the cross-tenant assertions real rather than self-referential
    - Laboratoire B (LAB): ``admin_b`` (ORG_ADMIN), ``clerk_b`` (BILLING_CLERK), ``tech``
      (LAB_TECH), ``releaser`` (LAB_RESULT_RELEASER)
    - ``outsider``: authenticated, member of nothing
    - ``platform_token``: the Keycloak realm role, which must open no billing door either

    Every clerk holds **only** ``BILLING_CLERK`` and every clinician **only** ``CLINICIAN``. That
    separation is the point: it is what lets this suite prove the two authorities do not imply one
    another in either direction.
    """
    private_key, _ = keypair
    platform_token = make_token(private_key, subject="platform", roles=[PLATFORM_ADMIN_ROLE])

    provider = make_provider(organizations, "Clinique A")
    other = make_provider(organizations, "Clinique Z")
    lab = make_lab(organizations, "Laboratoire B")
    entry, offering = make_priced_offering(
        catalog, lab_organization_id=lab.id, code="DH-CBC-BILL", amount=Decimal("3500.00")
    )
    # DH-MVP-ONBOARD-001 made production routing readiness-gated, and an untemplated offering is
    # unroutable for a NATIVE laboratory: DHealth's own result entry runs against a controlled

    # template, so an offering without one could be ordered and never completed. The fixture
    # satisfies the real rule rather than the rule being weakened for the fixture.
    activate_result_template(catalog, offering_id=offering.id)
    # ...and a laboratory that is not accepting orders is likewise unroutable. Both conditions are
    # real production gates, satisfied here rather than bypassed.
    LabDirectoryProfileRepository(catalog._sessions, organizations).upsert(  # noqa: SLF001
        lab_organization_id=lab.id,  # module's own `_enrol_and_staff` helper already established
        public_address="Adresse synthétique",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )

    admin_a_id, admin_a_token = _bootstrap(client, private_key, "adm-a", name="Admin A")
    admin_z_id, admin_z_token = _bootstrap(client, private_key, "adm-z", name="Admin Z")
    admin_b_id, admin_b_token = _bootstrap(client, private_key, "adm-b", name="Admin B")
    clerk_a_id, clerk_a_token = _bootstrap(client, private_key, "clk-a", name="Caissière A")
    clerk_z_id, clerk_z_token = _bootstrap(client, private_key, "clk-z", name="Caissier Z")
    clerk_b_id, clerk_b_token = _bootstrap(client, private_key, "clk-b", name="Caissier B")
    clin_id, clin_token = _bootstrap(client, private_key, "clin", name="Clinicienne C")
    tech_id, tech_token = _bootstrap(client, private_key, "tech", name="Technicien L")
    rel_id, rel_token = _bootstrap(client, private_key, "rel", name="Libérateur R")
    _out_id, outsider_token = _bootstrap(client, private_key, "out", name="Dehors")

    _attach(client, platform_token, str(provider.id), admin_a_id, ["ORG_ADMIN"])
    _attach(client, platform_token, str(other.id), admin_z_id, ["ORG_ADMIN"])
    _attach(client, platform_token, str(lab.id), admin_b_id, ["ORG_ADMIN"])
    _attach(client, admin_a_token, str(provider.id), clerk_a_id, ["BILLING_CLERK"])
    _attach(client, admin_z_token, str(other.id), clerk_z_id, ["BILLING_CLERK"])
    _attach(client, admin_b_token, str(lab.id), clerk_b_id, ["BILLING_CLERK"])
    _attach(client, admin_a_token, str(provider.id), clin_id, ["CLINICIAN"])
    _attach(client, admin_b_token, str(lab.id), tech_id, ["LAB_TECH"])
    _attach(client, admin_b_token, str(lab.id), rel_id, ["LAB_RESULT_RELEASER"])

    return {
        "provider_id": str(provider.id),
        "other_id": str(other.id),
        "lab_id": str(lab.id),
        "catalog_entry_id": str(entry.id),
        "admin_a_token": admin_a_token,
        "admin_z_token": admin_z_token,
        "admin_b_token": admin_b_token,
        "clerk_a_token": clerk_a_token,
        "clerk_a_id": clerk_a_id,
        "clerk_z_token": clerk_z_token,
        "clerk_b_token": clerk_b_token,
        "clin_token": clin_token,
        "tech_token": tech_token,
        "rel_token": rel_token,
        "outsider_token": outsider_token,
        "platform_token": platform_token,
    }


def _patient(client: TestClient, world, name: str = "Patient Synthétique") -> dict:
    response = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/patients",
        headers=_idem(world["clin_token"]),
        json={"display_name": name, "date_of_birth": "1990-04-17"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _service(client: TestClient, world, org: str, admin_token: str, name: str, amount: str) -> dict:
    response = client.post(
        f"/exchange/v1/organizations/{org}/billing/services",
        headers=_auth(admin_token),
        json={"name": name, "amount": amount, "currency": "DJF"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _routed_order(client: TestClient, world, patient: dict, *, accept: bool = True) -> dict:
    """A real order from Clinique A, routed to Laboratoire B, and accepted by it unless asked not
    to. Returns the committed order."""
    provider, lab = world["provider_id"], world["lab_id"]
    intent = client.post(
        f"/exchange/v1/organizations/{provider}/order-intents",
        headers=_idem(world["clin_token"]),
        json={
            "patient_reference_id": patient["primary_patient_reference_id"],
            "catalog_entry_ids": [world["catalog_entry_id"]],
        },
    )
    assert intent.status_code == 201, intent.text
    committed = client.post(
        f"/exchange/v1/organizations/{provider}/order-intents/{intent.json()['id']}/commit",
        headers=_auth(world["clin_token"]),
    )
    assert committed.status_code in (200, 201), committed.text
    order = committed.json()

    # DH-CLINIC-006 made routing readiness-gated: a provider may only assign to a laboratory it has
    # an active configured route to, and only a platform administrator configures one. Billing does
    # not change that and must not work around it -- the route is part of what makes the order a
    # real routed order rather than a fixture shortcut.
    routed = client.put(
        f"/exchange/v1/organizations/{provider}/lab-routes/{lab}",
        headers=_auth(world["platform_token"]),
        json={"active": True},
    )
    assert routed.status_code in (200, 201), routed.text

    assigned = client.post(
        f"/exchange/v1/organizations/{provider}/orders/{order['id']}/assign-lab",
        headers=_auth(world["clin_token"]),
        json={"lab_organization_id": lab},
    )
    assert assigned.status_code in (200, 201), assigned.text

    if accept:
        accepted = client.post(
            f"/exchange/v1/organizations/{lab}/lab-orders/{order['id']}/accept",
            headers=_auth(world["tech_token"]),
        )
        assert accepted.status_code == 200, accepted.text
    return order


def _lab_item_id(sessions: sessionmaker[Session], order_id: str) -> str:
    """The order item's id, read straight from the database.

    Deliberately not read through an API, and that is the point rather than a shortcut: several
    tests below hand this id to an organization that must **not** be able to charge for it, and the
    claim being made is that *knowing the id confers nothing*. Obtaining it by privileged means
    makes the refusal stronger, not weaker -- a real caller might learn an id from a log, a URL or a
    colleague, and ADR-066's rule has to hold anyway.

    The laboratory's own legitimate route to this id is `GET .../billing/orders`, which
    `test_bill34` exercises."""
    with sessions() as session:
        item_id = session.scalar(
            select(LabOrderItem.id).where(LabOrderItem.lab_order_id == uuid.UUID(order_id))
        )
    assert item_id is not None, "the order should have an item"
    return str(item_id)


def _invoice(client: TestClient, world, org: str, token: str, patient_id: str, lines: list[dict]):
    return client.post(
        f"/exchange/v1/organizations/{org}/billing/invoices",
        headers=_idem(token),
        json={"patient_id": patient_id, "lines": lines},
    )


def _pay(client, world, org, token, invoice_id, amount, method="CASH", key=None):
    headers = _auth(token)
    headers["Idempotency-Key"] = key or _key()
    return client.post(
        f"/exchange/v1/organizations/{org}/billing/invoices/{invoice_id}/payments",
        headers=headers,
        json={"amount": amount, "method": method},
    )


# == services and prices ==========================================================================


def test_bill1_a_facility_configures_its_own_services(client, world):
    service = _service(
        client, world, world["provider_id"], world["admin_a_token"], "Consultation", "3000.00"
    )
    assert service["name"] == "Consultation"
    assert Decimal(service["amount"]) == Decimal("3000.00")
    assert service["currency"] == "DJF"
    assert service["active"] is True


def test_bill2_only_an_administrator_may_create_or_price_a_service(client, world):
    """The cashier does not set the price list. A clerk who can quietly change a price before
    taking payment is the oldest problem in facility billing."""
    refused = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/services",
        headers=_auth(world["clerk_a_token"]),
        json={"name": "Consultation", "amount": "1.00", "currency": "DJF"},
    )
    assert refused.status_code == 403

    service = _service(
        client, world, world["provider_id"], world["admin_a_token"], "Consultation", "3000.00"
    )
    repriced = client.put(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/services/{service['id']}/price",
        headers=_auth(world["clerk_a_token"]),
        json={"amount": "1.00", "currency": "DJF"},
    )
    assert repriced.status_code == 403


def test_bill3_a_clerk_may_read_the_price_list_but_a_platform_admin_may_not(client, world):
    """A clerk needs the price list to build an invoice. A platform administrator gets nothing:
    `require_billing_clerk` refuses them, and the ORG_ADMIN fallback must not quietly let them in
    through `require_membership`'s platform bypass (ADR-063's principle)."""
    _service(client, world, world["provider_id"], world["admin_a_token"], "Consultation", "3000.00")

    clerk = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/services",
        headers=_auth(world["clerk_a_token"]),
    )
    assert clerk.status_code == 200
    assert len(clerk.json()) == 1

    admin = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/services",
        headers=_auth(world["admin_a_token"]),
    )
    assert admin.status_code == 200

    platform = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/services",
        headers=_auth(world["platform_token"]),
    )
    assert platform.status_code == 403, platform.text


def test_bill4_a_duplicate_service_name_is_a_conflict(client, world):
    _service(client, world, world["provider_id"], world["admin_a_token"], "Consultation", "3000.00")
    again = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/services",
        headers=_auth(world["admin_a_token"]),
        json={"name": "Consultation", "amount": "4000.00", "currency": "DJF"},
    )
    assert again.status_code == 409


# == the invoice ==================================================================================


def test_bill10_an_invoice_freezes_its_own_prices(client, world):
    """The load-bearing property: a price changed later does not change what was charged."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")

    created = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    )
    assert created.status_code == 201, created.text
    invoice = created.json()
    assert Decimal(invoice["total"]) == Decimal("3000.00")
    assert invoice["number"].startswith("FAC-")
    assert invoice["status"] == "OPEN"
    assert Decimal(invoice["balance"]) == Decimal("3000.00")

    # The facility raises its fee...
    repriced = client.put(
        f"/exchange/v1/organizations/{provider}/billing/services/{service['id']}/price",
        headers=_auth(world["admin_a_token"]),
        json={"amount": "5000.00", "currency": "DJF"},
    )
    assert repriced.status_code == 200
    assert Decimal(repriced.json()["amount"]) == Decimal("5000.00")

    # ...and yesterday's invoice is untouched.
    reread = client.get(
        f"/exchange/v1/organizations/{provider}/billing/invoices/{invoice['id']}",
        headers=_auth(world["clerk_a_token"]),
    )
    assert Decimal(reread.json()["total"]) == Decimal("3000.00")
    assert Decimal(reread.json()["lines"][0]["unit_amount"]) == Decimal("3000.00")


def test_bill11_a_renamed_service_does_not_rewrite_an_old_invoice(client, world, sessions):
    """The description is frozen too. A service renamed later must not retroactively change what an
    old invoice says was sold."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()
    assert invoice["lines"][0]["description"] == "Consultation"

    with sessions() as session:
        session.execute(text("UPDATE exchange_clinic_services SET name = 'Consultation générale'"))
        session.commit()

    reread = client.get(
        f"/exchange/v1/organizations/{provider}/billing/invoices/{invoice['id']}",
        headers=_auth(world["clerk_a_token"]),
    )
    assert reread.json()["lines"][0]["description"] == "Consultation"


def test_bill12_invoice_creation_is_idempotent(client, world):
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    body = {
        "patient_id": patient["patient_id"],
        "lines": [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    }
    key = _key()
    headers = {**_auth(world["clerk_a_token"]), "Idempotency-Key": key}

    first = client.post(
        f"/exchange/v1/organizations/{provider}/billing/invoices", headers=headers, json=body
    )
    second = client.post(
        f"/exchange/v1/organizations/{provider}/billing/invoices", headers=headers, json=body
    )
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["number"] == second.json()["number"]

    listed = client.get(
        f"/exchange/v1/organizations/{provider}/billing/invoices",
        headers=_auth(world["clerk_a_token"]),
    )
    assert len(listed.json()) == 1


def test_bill13_invoice_numbers_are_sequential_and_human_readable(client, world):
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    numbers = []
    for _ in range(3):
        response = _invoice(
            client,
            world,
            provider,
            world["clerk_a_token"],
            patient["patient_id"],
            [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
        )
        numbers.append(response.json()["number"])

    assert len({n for n in numbers}) == 3
    # Never a bare UUID on something a patient is handed (§15).
    for number in numbers:
        assert number.startswith("FAC-")
        assert len(number.split("-")) == 3
    assert [int(n.split("-")[2]) for n in numbers] == [1, 2, 3]


def test_bill14_quantity_multiplies_and_the_total_is_exact(client, world):
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Pansement", "1250.50")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 3}],
    ).json()
    # 1250.50 x 3 = 3751.50 exactly. A float would give 3751.4999999999995.
    assert Decimal(invoice["total"]) == Decimal("3751.50")
    assert Decimal(invoice["lines"][0]["line_total"]) == Decimal("3751.50")


def test_bill15_a_service_with_no_price_cannot_be_invoiced(client, world, sessions):
    """Refused rather than billed as zero -- a zero line would claim the service is free."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    with sessions() as session:
        session.execute(text("DELETE FROM exchange_clinic_service_prices"))
        session.commit()

    refused = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    )
    assert refused.status_code == 409


# == payments and balance =========================================================================


def test_bill20_partial_payments_reduce_the_balance_to_exactly_zero(client, world):
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "8500.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()

    first = _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "5000.00")
    assert first.status_code == 201, first.text
    assert Decimal(first.json()["balance"]) == Decimal("3500.00")
    assert first.json()["status"] == "PARTIALLY_PAID"

    second = _pay(
        client, world, provider, world["clerk_a_token"], invoice["id"], "3500.00", "MOBILE_MONEY"
    )
    assert second.status_code == 201, second.text
    assert Decimal(second.json()["balance"]) == Decimal("0.00")
    assert second.json()["status"] == "PAID"
    assert len(second.json()["payments"]) == 2


def test_bill21_overpayment_is_refused(client, world):
    """No credit, wallet or refund model exists, so accepting the money would create a patient
    balance DHealth cannot represent, report or return."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()

    too_much = _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "3000.01")
    assert too_much.status_code == 409, too_much.text

    _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "2000.00")
    over_the_rest = _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "1000.01")
    assert over_the_rest.status_code == 409


def test_bill22_payment_recording_is_idempotent(client, world):
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()

    key = _key()
    first = _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "1000.00", key=key)
    second = _pay(
        client, world, provider, world["clerk_a_token"], invoice["id"], "1000.00", key=key
    )
    assert (first.status_code, second.status_code) == (201, 200)
    assert len(second.json()["payments"]) == 1
    assert Decimal(second.json()["balance"]) == Decimal("2000.00")


def test_bill23_every_payment_has_a_receipt_reference(client, world):
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()
    _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "1000.00")
    final = _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "2000.00").json()

    # One receipt per payment, not per invoice.
    references = [payment["receipt_reference"] for payment in final["payments"]]
    assert references == [f"{invoice['number']}-R1", f"{invoice['number']}-R2"]


def test_bill24_financial_history_cannot_be_edited_or_deleted(client, world):
    """There is no update and no delete path for a payment, by design. Correction and reversal are
    undecided policy (§26) and this ticket refuses to invent them."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()
    paid = _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "1000.00").json()
    payment_id = paid["payments"][0]["id"]

    base = f"/exchange/v1/organizations/{provider}/billing"
    for method, path in (
        ("delete", f"{base}/invoices/{invoice['id']}/payments/{payment_id}"),
        ("put", f"{base}/invoices/{invoice['id']}/payments/{payment_id}"),
        ("patch", f"{base}/invoices/{invoice['id']}/payments/{payment_id}"),
        ("delete", f"{base}/invoices/{invoice['id']}"),
    ):
        response = getattr(client, method)(path, headers=_auth(world["clerk_a_token"]))
        assert response.status_code in (404, 405), (method, path, response.status_code)


# == ADR-066: an organization bills only what it performed =========================================


def test_bill30_a_laboratory_invoices_the_test_it_performed(client, world, sessions):
    lab, patient = world["lab_id"], _patient(client, world)
    order = _routed_order(client, world, patient)
    item_id = _lab_item_id(sessions, order["id"])

    created = _invoice(
        client,
        world,
        lab,
        world["clerk_b_token"],
        patient["patient_id"],
        [{"source_type": "LAB_ORDER_ITEM", "source_id": item_id, "quantity": 1}],
    )
    assert created.status_code == 201, created.text
    invoice = created.json()
    assert Decimal(invoice["total"]) == Decimal("3500.00")
    assert invoice["organization_id"] == lab
    assert invoice["lines"][0]["description"]


def test_bill31_a_clinic_cannot_invoice_the_test_its_laboratory_ran(
    client, world, sessions, audit_path
):
    """ADR-066, and the case D-2a leaves open. Clinic A holds real billing authority and a real
    order item id, and is still refused -- knowing an order exists is not authority to charge for
    it. The refusal is audited, because a clerk trying to bill somebody else's work is something a
    facility's operator should be able to find later."""
    provider, patient = world["provider_id"], _patient(client, world)
    order = _routed_order(client, world, patient)
    item_id = _lab_item_id(sessions, order["id"])
    before = len(_file_audit(audit_path))

    refused = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "LAB_ORDER_ITEM", "source_id": item_id, "quantity": 1}],
    )
    assert refused.status_code == 403, refused.text
    assert "another organization" in refused.json()["detail"]
    assert len(_file_audit(audit_path)) > before


def test_bill32_a_laboratory_cannot_invoice_before_it_accepts_the_work(client, world, sessions):
    """Routing is not performance. A `LabSelection` exists from the moment the provider assigns the
    order, before the laboratory has accepted it and while it may still reject it -- billing then
    would let a laboratory charge for a test it never ran."""
    lab, patient = world["lab_id"], _patient(client, world)
    order = _routed_order(client, world, patient, accept=False)
    item_id = _lab_item_id(sessions, order["id"])

    refused = _invoice(
        client,
        world,
        lab,
        world["clerk_b_token"],
        patient["patient_id"],
        [{"source_type": "LAB_ORDER_ITEM", "source_id": item_id, "quantity": 1}],
    )
    assert refused.status_code == 403, refused.text
    assert "accepted" in refused.json()["detail"]


def test_bill33_a_clinic_cannot_invoice_another_clinics_service(client, world):
    provider, patient = world["provider_id"], _patient(client, world)
    foreign = _service(
        client, world, world["other_id"], world["admin_z_token"], "Consultation Z", "9999.00"
    )
    refused = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": foreign["id"], "quantity": 1}],
    )
    assert refused.status_code == 403, refused.text


def test_bill34_the_billable_list_shows_only_this_organizations_own_work(client, world):
    """A clinic does not even see the routed tests its laboratory ran, because it may not bill
    them."""
    provider, lab = world["provider_id"], world["lab_id"]
    patient = _patient(client, world)
    _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    _routed_order(client, world, patient)

    clinic_view = client.get(
        f"/exchange/v1/organizations/{provider}/billing/patients/{patient['patient_id']}/billable",
        headers=_auth(world["clerk_a_token"]),
    )
    assert clinic_view.status_code == 200
    kinds = {source["source_type"] for source in clinic_view.json()}
    assert kinds == {"CLINIC_SERVICE"}, clinic_view.json()

    lab_view = client.get(
        f"/exchange/v1/organizations/{lab}/billing/orders",
        headers=_auth(world["clerk_b_token"]),
    )
    assert lab_view.status_code == 200
    assert len(lab_view.json()) == 1
    assert lab_view.json()[0]["items"][0]["source_type"] == "LAB_ORDER_ITEM"


def test_bill35_a_lab_clerk_sees_no_clinical_content_on_the_order(client, world, sessions):
    """§32's data minimization, asserted structurally: the laboratory's billing entry point carries
    identity and test names, and nothing about what the tests found."""
    patient = _patient(client, world)
    _routed_order(client, world, patient)
    orders = client.get(
        f"/exchange/v1/organizations/{world['lab_id']}/billing/orders",
        headers=_auth(world["clerk_b_token"]),
    ).json()

    payload = json.dumps(orders)
    for leaked in ("observation", "result", "reference_range", "note", "diagnos", "specimen"):
        assert leaked not in payload.lower(), leaked
    assert set(orders[0]) == {
        "lab_order_id",
        "patient_id",
        "patient_display_name",
        "status",
        "created_at",
        "items",
    }


# == the billing patient lookup, and why it is not the patient directory =========================


def test_bill36_the_billing_patient_lookup_returns_only_approved_identity_fields(client, world):
    """The projection, pinned against the live response rather than against a docstring.

    Three fields: `id`, `display_name`, `date_of_birth`. The date of birth is *required* here, not
    incidental -- a till that can only see a name bills the wrong person the first time two
    patients share one, which is a wrong-patient error arriving through the financial door.

    Everything else on `Patient` is absent, contact details most of all. Asserted as an exact set
    rather than a list of absences, so a column added to `Patient` later cannot arrive on a
    cashier's screen by being added to a shared serializer -- that is precisely how clinical fields
    leak into non-clinical surfaces, and an exact set is the only assertion that catches it."""
    patient = _patient(client, world, name="Amina Synthétique")
    found = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/patients",
        headers=_auth(world["clerk_a_token"]),
        params={"q": "Amina"},
    )
    assert found.status_code == 200, found.text
    rows = found.json()
    assert len(rows) == 1, rows

    assert set(rows[0]) == {"id", "display_name", "date_of_birth"}
    assert rows[0]["id"] == patient["patient_id"]
    assert rows[0]["display_name"] == "Amina Synthétique"
    assert rows[0]["date_of_birth"] == "1990-04-17"


def test_bill37_no_clinical_content_can_reach_the_billing_patient_lookup(client, world, sessions):
    """The same patient, now carrying a visit, an order, a released result and a clinical note.

    `test_bill36` pins the field set on a patient with no history, which would also pass if the
    endpoint simply had nothing to leak yet. This one gives the patient every clinical artefact
    DHealth can attach to them *first*, and then asserts the lookup is unchanged -- so the
    projection is proven narrow against a patient who has something to disclose."""
    patient = _patient(client, world, name="Bilan Synthétique")
    order = _routed_order(client, world, patient)
    item_id = _lab_item_id(sessions, order["id"])
    base = f"/exchange/v1/organizations/{world['lab_id']}/lab-orders/{order['id']}"
    client.post(f"{base}/record-collection", headers=_auth(world["tech_token"]))
    client.post(f"{base}/start-processing", headers=_auth(world["tech_token"]))
    client.post(
        f"{base}/items/{item_id}/result",
        headers=_auth(world["tech_token"]),
        json={
            "result_type": "NUMERIC",
            "numeric_value": "12.5",
            "numeric_unit": "g/dL",
            "reference_range_low": "12.0",
            "reference_range_high": "16.0",
        },
    )
    inspection = client.get(f"{base}/results", headers=_auth(world["rel_token"]))
    client.post(
        f"{base}/release-results",
        headers=_auth(world["rel_token"]),
        json={"expected_result_fingerprint": inspection.json()["result_fingerprint"]},
    )

    rows = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/patients",
        headers=_auth(world["clerk_a_token"]),
        params={"q": "Bilan"},
    ).json()
    assert len(rows) == 1, rows
    assert set(rows[0]) == {"id", "display_name", "date_of_birth"}

    payload = json.dumps(rows).lower()
    for leaked in (
        "observation",
        "result",
        "reference_range",
        "note",
        "reason",
        "visit",
        "order",
        "diagnos",
        "specimen",
        "contact",
        "phone",
        "12.5",
        "g/dl",
    ):
        assert leaked not in payload, leaked


def test_bill38_the_lookup_is_not_a_backdoor_into_another_organizations_directory(client, world):
    """Clinique Z's clerk, holding real billing authority, searching for Clinique A's patient.

    Both routes are closed. Asking their own organization finds nothing, because the patient is not
    theirs and they have done no work for them. Asking Clinique A is a 404, because they are not a
    member there -- the anti-enumeration shape every tenant boundary in this codebase uses.

    Knowing the patient's UUID buys nothing either: there is no lookup-by-id on this endpoint at
    all, and `test_billauth8` already covers the invoice call refusing that id."""
    patient = _patient(client, world, name="Cible Synthétique")

    own_org = client.get(
        f"/exchange/v1/organizations/{world['other_id']}/billing/patients",
        headers=_auth(world["clerk_z_token"]),
        params={"q": "Cible"},
    )
    assert own_org.status_code == 200, own_org.text
    assert own_org.json() == []

    foreign_org = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/patients",
        headers=_auth(world["clerk_z_token"]),
        params={"q": "Cible"},
    )
    assert foreign_org.status_code == 404, foreign_org.text

    # And the patient really is findable by the clerk who is entitled to find them, so the two
    # assertions above are about authority rather than about a query that matches nothing.
    entitled = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/billing/patients",
        headers=_auth(world["clerk_a_token"]),
        params={"q": "Cible"},
    ).json()
    assert [row["id"] for row in entitled] == [patient["patient_id"]]


def test_bill39_the_lookup_needs_billing_authority_and_is_not_the_clinician_endpoint(client, world):
    """Two claims in one test, because they are two halves of the same decision.

    First: this read is gated on `BILLING_OPERATE`, so a clinician -- who holds every clinical
    authority there is and no billing authority -- is refused. That is the direction people expect
    to be surprised by, and it is what proves the gate is really the billing one rather than an
    incidental side effect of membership.

    Second: the clerk still cannot reach the clinician's own patient directory. The answer to "a
    cashier needs to find a patient" was a second narrow endpoint, never opening the existing one,
    and never granting `CLINICIAN` to a cashier so a search box would work."""
    _patient(client, world, name="Dalia Synthétique")
    url = f"/exchange/v1/organizations/{world['provider_id']}/billing/patients"

    refused = client.get(url, headers=_auth(world["clin_token"]), params={"q": "Dalia"})
    assert refused.status_code == 403, refused.text

    # ...and the platform realm role opens no billing door either (ADR-063).
    platform = client.get(url, headers=_auth(world["platform_token"]), params={"q": "Dalia"})
    assert platform.status_code in (403, 404), platform.text

    # The clinician's directory stays closed to the clerk, which is the other half of the bargain.
    directory = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/patients",
        headers=_auth(world["clerk_a_token"]),
    )
    assert directory.status_code == 403, directory.text

    # A blank query is refused rather than answered with the roster: this finds a person who is
    # present, it does not browse a directory.
    blank = client.get(url, headers=_auth(world["clerk_a_token"]), params={"q": ""})
    assert blank.status_code == 422, blank.text


# == the capability seam ==========================================================================


def test_bill60_billing_asks_for_a_capability_never_for_a_role(client, world):
    """`BILLING_CLERK` is a convenient bundle, not a gate.

    The direction DHealth is going is capability-based authorization: a Front Desk role,
    `ORG_ADMIN`,
    or one named individual may later be granted billing permission independently. This ticket does
    not build that system -- it is `DHEALTH-ACCESS-CAPABILITIES-001` -- but it must not encode
    `role == BILLING_CLERK` anywhere billing would have to be rewritten.

    So: every billing endpoint goes through one capability gate, and `exchange/capabilities.py` is
    the only file in the billing surface that names the role at all. This test is what makes that a
    guarantee rather than a present-tense observation."""
    import ast
    from pathlib import Path as _Path

    import daher_adapter.exchange.billing as billing_module
    from daher_adapter.exchange.capabilities import (
        CAPABILITY_ROLE_GRANTS,
        OrganizationCapability,
        capability_is_held,
    )

    # The service layer does not know the role exists -- checked against the parsed tree rather
    # than the text, so prose explaining the capability model is fine and a code reference is not.
    billing_tree = ast.parse(_Path(billing_module.__file__).read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(billing_tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(billing_tree) if isinstance(node, ast.Attribute)
    }
    constants = {
        node.value
        for node in ast.walk(billing_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    # Docstrings are Constants too, so exclude them: only *expression* strings count as code.
    #
    # `clean=False` is load-bearing. The default runs `inspect.cleandoc`, which dedents, so the
    # cleaned text never equalled the raw `Constant` value for any indented multi-line docstring
    # and this exclusion silently excluded nothing. It went unnoticed for as long as no docstring
    # in the module happened to contain `BILLING_CLERK`; `search_patients` explaining why a cashier
    # is not granted `CLINICIAN` is the first one that does.
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(billing_tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
    }
    code_strings = {value for value in constants if value not in docstrings}
    assert "BILLING_CLERK" not in names
    assert "OrganizationRole" not in names
    assert not any("BILLING_CLERK" in value for value in code_strings)

    # The seam resolves today's answer, and is the only place that does.
    assert capability_is_held(OrganizationCapability.BILLING_OPERATE, {"BILLING_CLERK"}) is True
    assert capability_is_held(OrganizationCapability.BILLING_OPERATE, {"ORG_ADMIN"}) is False
    assert (
        capability_is_held(OrganizationCapability.BILLING_CONFIGURE_PRICES, {"ORG_ADMIN"}) is True
    )
    assert set(CAPABILITY_ROLE_GRANTS) == {
        OrganizationCapability.BILLING_OPERATE,
        OrganizationCapability.BILLING_CONFIGURE_PRICES,
    }

    # And the authorization layer offers no role-named billing gate to reach for.
    from daher_adapter.exchange.authorization import AuthorizationService

    assert not hasattr(AuthorizationService, "require_billing_clerk")
    assert hasattr(AuthorizationService, "require_capability")

    # Every billing endpoint in the router names a capability. Parsed, not grepped: a comment
    # mentioning the role must not pass, and a call that slipped through to a role gate must not.
    api_source = _Path(
        __import__("daher_adapter.exchange.api", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")
    tree = ast.parse(api_source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_require_billing_capability" in called
    assert "_require_billing_clerk" not in called


def test_bill61_a_second_role_could_be_granted_billing_without_touching_billing(client, world):
    """The property the seam exists for, demonstrated rather than asserted.

    Granting `BILLING_OPERATE` to `ORG_ADMIN` is a one-line change to the mapping. Nothing in the
    service, the endpoints or this suite's behavioural tests changes -- which is what "without
    rewriting Billing" means concretely. Monkey-patched here and restored, because the *product*
    answer (should an administrator hold the till?) is `DHEALTH-ACCESS-CAPABILITIES-001`'s to make,
    not this test's."""
    from daher_adapter.exchange import capabilities as capabilities_module

    provider = world["provider_id"]
    before = client.get(
        f"/exchange/v1/organizations/{provider}/billing/invoices",
        headers=_auth(world["admin_a_token"]),
    )
    assert before.status_code == 403, "an administrator is not a cashier by default"

    original = capabilities_module.CAPABILITY_ROLE_GRANTS[
        capabilities_module.OrganizationCapability.BILLING_OPERATE
    ]
    capabilities_module.CAPABILITY_ROLE_GRANTS[
        capabilities_module.OrganizationCapability.BILLING_OPERATE
    ] = frozenset({*original, capabilities_module.OrganizationRole.ORG_ADMIN})
    try:
        after = client.get(
            f"/exchange/v1/organizations/{provider}/billing/invoices",
            headers=_auth(world["admin_a_token"]),
        )
        assert after.status_code == 200, after.text
    finally:
        capabilities_module.CAPABILITY_ROLE_GRANTS[
            capabilities_module.OrganizationCapability.BILLING_OPERATE
        ] = original

    restored = client.get(
        f"/exchange/v1/organizations/{provider}/billing/invoices",
        headers=_auth(world["admin_a_token"]),
    )
    assert restored.status_code == 403, "the mapping must have been restored"


# == authorization: BILL-AUTH-1..8 ================================================================


def test_billauth1_unauthenticated_is_refused(client, world):
    response = client.get(f"/exchange/v1/organizations/{world['provider_id']}/billing/invoices")
    assert response.status_code == 401


def test_billauth2_an_authenticated_user_without_billing_authority_is_refused(client, world):
    """Every one of these holds a real, active membership somewhere. None of them is a cashier."""
    provider = world["provider_id"]
    for who in ("clin_token", "admin_a_token"):
        response = client.get(
            f"/exchange/v1/organizations/{provider}/billing/invoices", headers=_auth(world[who])
        )
        assert response.status_code == 403, (who, response.status_code)

    outsider = client.get(
        f"/exchange/v1/organizations/{provider}/billing/invoices",
        headers=_auth(world["outsider_token"]),
    )
    assert outsider.status_code == 404


def test_billauth3_a_clerk_cannot_read_another_clinics_invoices(client, world):
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()

    # Clinique Z's clerk, asking Clinique Z for Clinique A's invoice id.
    foreign = client.get(
        f"/exchange/v1/organizations/{world['other_id']}/billing/invoices/{invoice['id']}",
        headers=_auth(world["clerk_z_token"]),
    )
    assert foreign.status_code == 404

    # ...and asking Clinique A directly.
    direct = client.get(
        f"/exchange/v1/organizations/{provider}/billing/invoices/{invoice['id']}",
        headers=_auth(world["clerk_z_token"]),
    )
    assert direct.status_code == 404


def test_billauth4_and_5_clinic_and_lab_invoices_do_not_bleed_into_each_other(
    client, world, sessions
):
    """Both directions, on the *same* patient and the *same* order -- which is the case that would
    leak if ownership were inferred from the clinical relationship rather than from the invoice."""
    provider, lab = world["provider_id"], world["lab_id"]
    patient = _patient(client, world)
    order = _routed_order(client, world, patient)
    item_id = _lab_item_id(sessions, order["id"])
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")

    clinic_invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()
    lab_invoice = _invoice(
        client,
        world,
        lab,
        world["clerk_b_token"],
        patient["patient_id"],
        [{"source_type": "LAB_ORDER_ITEM", "source_id": item_id, "quantity": 1}],
    ).json()
    assert clinic_invoice["id"] != lab_invoice["id"]

    # BILL-AUTH-4: the clinic cannot read the lab's invoice, though it originated the order.
    assert (
        client.get(
            f"/exchange/v1/organizations/{lab}/billing/invoices/{lab_invoice['id']}",
            headers=_auth(world["clerk_a_token"]),
        ).status_code
        == 404
    )
    # BILL-AUTH-5: the lab cannot read the clinic's, though it received the order.
    assert (
        client.get(
            f"/exchange/v1/organizations/{provider}/billing/invoices/{clinic_invoice['id']}",
            headers=_auth(world["clerk_b_token"]),
        ).status_code
        == 404
    )
    # Each sees exactly one invoice: its own.
    for org, token, expected in (
        (provider, world["clerk_a_token"], clinic_invoice["id"]),
        (lab, world["clerk_b_token"], lab_invoice["id"]),
    ):
        listed = client.get(
            f"/exchange/v1/organizations/{org}/billing/invoices", headers=_auth(token)
        ).json()
        assert [row["id"] for row in listed] == [expected]


def test_billauth6_no_authority_is_inherited_in_either_direction(client, world):
    """The decision this ticket is built on: `BILLING_CLERK` is additive and never implied, and it
    implies nothing clinical either."""
    provider, lab = world["provider_id"], world["lab_id"]
    patient = _patient(client, world)

    # A clerk reaches no clinical surface.
    for path in (
        f"/exchange/v1/organizations/{provider}/patients",
        f"/exchange/v1/organizations/{provider}/visits",
        f"/exchange/v1/organizations/{provider}/orders",
    ):
        response = client.get(path, headers=_auth(world["clerk_a_token"]))
        assert response.status_code == 403, (path, response.status_code)

    # A lab clerk reaches no laboratory surface.
    lab_orders = client.get(
        f"/exchange/v1/organizations/{lab}/lab-orders", headers=_auth(world["clerk_b_token"])
    )
    assert lab_orders.status_code == 403

    # And no clinical or administrative role is a cashier.
    for who in ("clin_token", "admin_a_token"):
        response = client.get(
            f"/exchange/v1/organizations/{provider}/billing/invoices", headers=_auth(world[who])
        )
        assert response.status_code == 403, who
    assert (
        client.get(
            f"/exchange/v1/organizations/{lab}/billing/invoices",
            headers=_auth(world["rel_token"]),
        ).status_code
        == 403
    )
    assert patient["patient_id"]


def test_billauth7_an_invented_organization_fails_closed(client, world):
    response = client.get(
        f"/exchange/v1/organizations/{uuid.uuid4()}/billing/invoices",
        headers=_auth(world["clerk_a_token"]),
    )
    assert response.status_code == 404


def test_billauth8_a_clerk_cannot_invoice_another_organizations_patient(client, world):
    """Clinique Z's clerk, billing Clinique Z, for Clinique A's patient. Refused: Clinique Z has
    neither registered them nor done any work for them."""
    patient = _patient(client, world)
    foreign_service = _service(
        client, world, world["other_id"], world["admin_z_token"], "Consultation Z", "1000.00"
    )
    refused = _invoice(
        client,
        world,
        world["other_id"],
        world["clerk_z_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": foreign_service["id"], "quantity": 1}],
    )
    assert refused.status_code == 404, refused.text


def test_billauth9_a_clerk_cannot_pay_another_organizations_invoice(client, world):
    """Reading someone else's invoice was already covered; *paying* it was not.

    This test exists because NC-BILL-1 found it missing. Deleting `record_payment`'s ownership
    check left all 43 other tests green, which meant the one line standing between a clerk and
    another facility's ledger was unguarded: knowing an invoice id was enough to write a payment
    against a facility you have nothing to do with, moving its balance and its status.

    `get_invoice` was never at risk the same way -- it filters by organization inside the SQL
    `WHERE`, so there is no check to forget. `record_payment` has to load the row first in order to
    lock it, which is precisely why it needs an explicit check and why that check needs a test."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()

    # Clinique Z's clerk, paying through their *own* organization, against Clinique A's invoice id.
    # They are a legitimate cashier and the capability gate passes; the invoice is simply not
    # theirs, and 404 says so without confirming it exists.
    through_own_org = _pay(
        client, world, world["other_id"], world["clerk_z_token"], invoice["id"], "3000.00"
    )
    assert through_own_org.status_code == 404, through_own_org.text

    # ...and through Clinique A's organization, where they hold nothing at all.
    through_owner = _pay(client, world, provider, world["clerk_z_token"], invoice["id"], "3000.00")
    assert through_owner.status_code == 404, through_owner.text

    # The ledger is untouched. A refusal that still wrote the row would be the worse bug.
    after = client.get(
        f"/exchange/v1/organizations/{provider}/billing/invoices/{invoice['id']}",
        headers=_auth(world["clerk_a_token"]),
    ).json()
    assert Decimal(after["balance"]) == Decimal("3000.00")
    assert after["status"] == "OPEN"
    assert after["payments"] == []


# == BILL-SAFETY-1: billing never gates clinical work =============================================


def test_billsafety1_an_unpaid_invoice_never_blocks_release_or_review(client, world, sessions):
    """The ticket's P0 invariant, as a full clinical flow with money outstanding the whole time.

    If this ever fails, a patient's financial state has become clinical release authority."""
    provider, lab = world["provider_id"], world["lab_id"]
    patient = _patient(client, world)
    order = _routed_order(client, world, patient)
    item_id = _lab_item_id(sessions, order["id"])

    # A real, unpaid bill exists for this exact test.
    invoice = _invoice(
        client,
        world,
        lab,
        world["clerk_b_token"],
        patient["patient_id"],
        [{"source_type": "LAB_ORDER_ITEM", "source_id": item_id, "quantity": 1}],
    ).json()
    assert Decimal(invoice["balance"]) > Decimal("0")
    assert invoice["status"] == "OPEN"

    base = f"/exchange/v1/organizations/{lab}/lab-orders/{order['id']}"
    collected = client.post(f"{base}/record-collection", headers=_auth(world["tech_token"]))
    assert collected.status_code == 201, collected.text
    started = client.post(f"{base}/start-processing", headers=_auth(world["tech_token"]))
    assert started.status_code == 200, started.text

    entered = client.post(
        f"{base}/items/{item_id}/result",
        headers=_auth(world["tech_token"]),
        json={
            "result_type": "NUMERIC",
            "numeric_value": "12.5",
            "numeric_unit": "g/dL",
            "reference_range_low": "12.0",
            "reference_range_high": "16.0",
        },
    )
    assert entered.status_code == 201, entered.text

    # The release itself, with the balance still outstanding. The releaser inspects first and
    # releases against the fingerprint they saw -- the real ADR-048 path, not a shortcut.
    inspection = client.get(f"{base}/results", headers=_auth(world["rel_token"]))
    assert inspection.status_code == 200, inspection.text
    released = client.post(
        f"{base}/release-results",
        headers=_auth(world["rel_token"]),
        json={"expected_result_fingerprint": inspection.json()["result_fingerprint"]},
    )
    assert released.status_code == 201, released.text

    # And the clinician can review it, still unpaid. DoctorReview is the last clinical act in the
    # chain and the one a financial gate would be most tempting to attach to -- "do not release the
    # result to the doctor until the bill is settled". It succeeds with the balance outstanding.
    review = client.post(
        f"/exchange/v1/organizations/{provider}/orders/{order['id']}/review/no-follow-up-required",
        headers=_auth(world["clin_token"]),
    )
    assert review.status_code in (200, 201), review.text

    still_owed = client.get(
        f"/exchange/v1/organizations/{lab}/billing/invoices/{invoice['id']}",
        headers=_auth(world["clerk_b_token"]),
    ).json()
    assert Decimal(still_owed["balance"]) == Decimal("3500.00")
    assert still_owed["status"] == "OPEN"


def test_billsafety2_billing_is_structurally_unable_to_see_clinical_release(client, world):
    """The import graph, asserted rather than described.

    A behavioural test proves billing does not gate release *today*. This proves the two domains
    cannot even observe each other, so a future edit cannot quietly couple them without changing an
    import -- which this test then fails on."""
    import ast

    import daher_adapter.exchange.billing as billing_module

    def imported_modules(module) -> set[str]:
        """Real imports, from the parsed tree -- never a substring search. A docstring that *names*
        `lab_workflow.py` to explain why it is not imported must not fail this test; an actual
        `import` of it must."""
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
        return found

    billing_imports = imported_modules(billing_module)
    for forbidden in (
        "daher_adapter.exchange.results",
        "daher_adapter.exchange.lab_workflow",
        "daher_adapter.exchange.visits",
        "daher_adapter.exchange.selection",
    ):
        assert forbidden not in billing_imports, f"billing imports {forbidden}"

    # And nothing clinical imports billing, so the coupling cannot be introduced from either side.
    for module_name in (
        "daher_adapter.exchange.results",
        "daher_adapter.exchange.lab_workflow",
        "daher_adapter.exchange.visits",
    ):
        module = __import__(module_name, fromlist=["__file__"])
        assert "daher_adapter.exchange.billing" not in imported_modules(module), module_name
        # The clinical modules do not even name a financial concept, which is the stronger claim:
        # a future author cannot reach for a balance there without first adding an import this
        # test then fails on.
        source_of = Path(module.__file__).read_text(encoding="utf-8")
        for financial in ("Invoice", "Payment(", "invoice", "balance"):
            assert financial not in source_of, f"{module_name} mentions {financial}"


def test_billsafety3_an_unpaid_invoice_never_blocks_a_visit(client, world):
    """§34: financial status is informational to financial workflows only."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    )

    visit = client.post(
        f"/exchange/v1/organizations/{provider}/visits",
        headers=_idem(world["clin_token"]),
        json={"patient_id": patient["patient_id"], "reason_for_visit": "Contrôle"},
    )
    assert visit.status_code == 201, visit.text
    visit_id = visit.json()["id"]

    for action in ("take", "complete"):
        response = client.post(
            f"/exchange/v1/organizations/{provider}/visits/{visit_id}/{action}",
            headers=_auth(world["clin_token"]),
        )
        assert response.status_code == 200, (action, response.text)


# == audit and data minimization ==================================================================


def test_bill40_every_financial_mutation_is_audited(client, world, sessions):
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    client.put(
        f"/exchange/v1/organizations/{provider}/billing/services/{service['id']}/price",
        headers=_auth(world["admin_a_token"]),
        json={"amount": "3200.00", "currency": "DJF"},
    )
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()
    _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "1000.00")

    financial = [
        event
        for event in _db_audit(sessions)
        if event.event_type
        in {
            "clinic_service_created",
            "clinic_service_price_changed",
            "invoice_created",
            "payment_recorded",
        }
    ]
    assert [event.event_type for event in financial] == [
        "clinic_service_created",
        "clinic_service_price_changed",
        "invoice_created",
        "payment_recorded",
    ]
    for event in financial:
        assert str(event.organization_id) == provider
        assert event.resource_type in {"invoice", "clinic_service"}
    # The clerk's own acts are attributed to the clerk, the administrator's to the administrator.
    assert str(financial[-1].actor_identity_id) == world["clerk_a_id"]


def test_bill41_no_amount_reaches_the_audit_table(client, world, sessions):
    """The authoritative figure lives on the immutable Invoice/Payment row. A second copy in the
    audit table could diverge from it, and there is no column that could hold one."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "7777.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()
    _pay(client, world, provider, world["clerk_a_token"], invoice["id"], "7777.00")

    for event in _db_audit(sessions):
        for column in event.__table__.columns:
            assert "7777" not in str(getattr(event, column.name))


def test_bill42_the_invoice_response_carries_no_clinical_content(client, world, sessions):
    """§18's billing-privacy rule, asserted on the shape rather than trusted. A line may name the
    test that was performed, because that is the thing being charged for; it may not carry what the
    test found."""
    lab = world["lab_id"]
    patient = _patient(client, world)
    order = _routed_order(client, world, patient)
    item_id = _lab_item_id(sessions, order["id"])
    invoice = _invoice(
        client,
        world,
        lab,
        world["clerk_b_token"],
        patient["patient_id"],
        [{"source_type": "LAB_ORDER_ITEM", "source_id": item_id, "quantity": 1}],
    ).json()

    assert set(invoice) == {
        "id",
        "organization_id",
        "organization_name",
        "patient_id",
        "patient_display_name",
        "number",
        "currency",
        "issued_at",
        "lines",
        "payments",
        "total",
        "paid",
        "balance",
        "status",
    }
    payload = json.dumps(invoice).lower()
    for leaked in ("observation", "reference_range", "value_numeric", "diagnos", "note"):
        assert leaked not in payload, leaked


# == the database layer ===========================================================================


def test_bill50_money_columns_are_exact_not_floating_point(client, world):
    """`Numeric`, never `Float`. Binary floating point cannot represent 0.1, so a balance that
    should be exactly zero would not be."""
    from sqlalchemy import Numeric

    for table, column in (
        (InvoiceLine, "unit_amount"),
        (Payment, "amount"),
    ):
        assert isinstance(table.__table__.c[column].type, Numeric)
        assert table.__table__.c[column].type.scale == 2


def test_bill51_no_status_or_balance_column_exists(client, world):
    """Law 8: balance is derived, never stored -- and so is status, which is a pure function of the
    total and the payments. A stored copy is a second source of truth that drifts."""
    columns = set(Invoice.__table__.columns.keys())
    for absent in ("status", "balance", "total", "paid", "amount_due", "voided_at"):
        assert absent not in columns, absent


def test_bill52_the_payment_tenant_key_is_composite(client, world):
    """A payment cannot be attached to another organization's invoice, and the database is what
    refuses it rather than the service layer."""
    from sqlalchemy import ForeignKeyConstraint

    composite = [
        constraint
        for constraint in Payment.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint) and len(constraint.elements) == 2
    ]
    assert len(composite) == 1
    assert composite[0].name == "fk_exchange_payments_invoice_same_org"
    assert {element.parent.name for element in composite[0].elements} == {
        "invoice_id",
        "organization_id",
    }


def test_bill53_an_invoice_line_cannot_disagree_with_its_invoices_currency(client, world):
    from sqlalchemy import ForeignKeyConstraint

    composite = [
        constraint
        for constraint in InvoiceLine.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint) and len(constraint.elements) == 2
    ]
    assert len(composite) == 1
    assert {element.parent.name for element in composite[0].elements} == {
        "invoice_id",
        "currency",
    }


def test_bill54_the_polymorphic_source_has_real_referential_integrity(client, world):
    """§8 asked for a safer pattern than an untyped key, and this is it: two typed foreign keys and
    a CHECK that exactly one matches `source_type`."""
    checks = {
        constraint.name
        for constraint in InvoiceLine.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert "ck_exchange_invoice_lines_clinic_source_matches" in checks
    assert "ck_exchange_invoice_lines_lab_source_matches" in checks
    assert "ck_exchange_invoice_lines_source_type" in checks
    assert InvoiceLine.__table__.c["clinic_service_id"].nullable is True
    assert InvoiceLine.__table__.c["lab_order_item_id"].nullable is True


def test_bill55_negative_and_zero_money_is_refused_by_the_schema(client, world):
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in InvoiceLine.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert "quantity > 0" in checks["ck_exchange_invoice_lines_quantity_positive"]
    assert "unit_amount >= 0" in checks["ck_exchange_invoice_lines_unit_amount_non_negative"]

    payment_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Payment.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert "amount > 0" in payment_checks["ck_exchange_payments_amount_positive"]


def test_bill56_a_negative_payment_is_refused_at_the_api(client, world):
    """No negative-line refunds as a shortcut (§14): this ticket has no refund model, and a negative
    payment would silently invent one."""
    provider = world["provider_id"]
    patient = _patient(client, world)
    service = _service(client, world, provider, world["admin_a_token"], "Consultation", "3000.00")
    invoice = _invoice(
        client,
        world,
        provider,
        world["clerk_a_token"],
        patient["patient_id"],
        [{"source_type": "CLINIC_SERVICE", "source_id": service["id"], "quantity": 1}],
    ).json()

    for amount in ("-100.00", "0.00"):
        refused = _pay(client, world, provider, world["clerk_a_token"], invoice["id"], amount)
        assert refused.status_code == 422, (amount, refused.status_code)
