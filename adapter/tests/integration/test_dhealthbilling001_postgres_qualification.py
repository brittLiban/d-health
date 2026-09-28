"""DHEALTH-BILLING-001: real PostgreSQL qualification of invoices, payments and numbering.

The SQLite unit suite (``tests/test_dhealthbilling001_billing.py``, 44 tests) proves every *rule*
billing has. It cannot prove the properties money actually depends on, for three stated reasons:

1. **SQLAlchemy's SQLite dialect omits ``FOR UPDATE`` entirely.** ``record_payment``'s invoice lock
   and ``_next_invoice_number``'s counter lock are therefore silently no-ops there, and SQLite's
   own writer serialization then makes the unit tests pass for a reason unrelated to the lock being
   taken. That is the "green for the wrong reason" shape this repository has been burned by before,
   and on the overpayment path it is the difference between a refusal and a facility quietly taking
   the same 3 500 DJF twice.
2. **This suite's SQLite engine does not enable ``PRAGMA foreign_keys``** (``tests/conftest.py``),
   so the composite tenant keys and the polymorphic-source foreign keys are asserted *structurally*
   in the unit suite and *behaviourally* only here.
3. **SQLite's typing is permissive about ``NUMERIC``.** The ``CHECK`` constraints that keep an
   amount positive and a quantity sane are only genuinely enforced by a real backend.

What is qualified in this file, and nowhere else -- the four races the ticket names by letter:

- **(B) two concurrent payments against the same remaining balance.** Both cashiers read the same
  balance without the lock and both succeed, and the facility has then recorded more money than the
  invoice is worth with no credit model to hold the difference. Exactly one must win.
- **(D) an invoice becoming fully paid while another payment is in flight.** The same race with a
  partial payment already recorded, which is the version that actually happens at a till.
- **(A) two concurrent invoice creations for the same intended operation.** One idempotency key,
  two clicks: exactly one ``Invoice`` row, both callers served, one of them a replay.
- **(C) a price change while an invoice is being created.** The frozen line must equal the old price
  or the new one and must never be a blend of the two, and must not move afterwards.

And, additionally:

- **Invoice numbering under a real race** -- two unrelated invoices at the same instant take two
  distinct numbers, which is what the counter row's ``FOR UPDATE`` exists for and what
  ``count(*) + 1`` would get wrong.
- **Tenant boundaries in the database itself**: a payment cannot be attached to another
  organization's invoice even if the application check is bypassed, because the foreign key is
  composite on ``(invoice_id, organization_id)``.
- **The currency composite key**: an invoice line cannot disagree with its invoice's currency.
- **The polymorphic source CHECKs**: exactly one typed foreign key is set, and it matches
  ``source_type``.
- **Money CHECK constraints** against a real backend.
- **Transactional audit coupling**: a refused payment leaves no audit row behind, because the audit
  write and the mutation share one transaction.

**The proof is the barrier plus the final state, not an inspection of the lock.** If a lock were
not held, the observable outcome would be two successes and a balance below zero -- which is what
these assertions catch. Nothing here reads ``pg_locks``.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHBILL_PG_*`` env var below is set. Never point
this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHBILL_PG_HOST=127.0.0.1 \\
    DHBILL_PG_PORT=5432 \\
    DHBILL_PG_USER=adapter \\
    DHBILL_PG_PASSWORD=... \\
    DHBILL_PG_DATABASE=dhbill_qual \\
        pytest adapter/tests/integration/test_dhealthbilling001_postgres_qualification.py -v

The target database must already have migrations applied through ``0035_facility_billing``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.

All patient data here is synthetic. No real patient data is used. No money moves: this domain
records that a payment was taken, and contacts no bank, card network or mobile-money provider.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.billing import (
    BillingNotFound,
    BillingRepository,
    LineRequest,
    OverpaymentRefused,
)
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    BillableSourceType,
    ExchangeSecurityAuditEvent,
    Invoice,
    InvoiceLine,
    OrganizationRole,
    Patient,
    Payment,
    PaymentMethod,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository

PG_HOST = os.environ.get("DHBILL_PG_HOST")
PG_PORT = os.environ.get("DHBILL_PG_PORT")
PG_USER = os.environ.get("DHBILL_PG_USER")
PG_PASSWORD = os.environ.get("DHBILL_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHBILL_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHBILL_PG_* env vars) -- never in CI",
)

ISSUER = "https://pg-qualification.invalid/realms/dhealth-exchange"


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    # A pool large enough for every concurrent thread below to hold its own real connection. A
    # thread waiting on the *pool* rather than on the database's row lock would serialize the race
    # this file exists to create, and the test would pass without ever exercising the lock.
    engine = create_engine(url, pool_pre_ping=True, pool_size=12, max_overflow=8)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _repository(sessions: sessionmaker) -> BillingRepository:
    return BillingRepository(
        sessions, OrganizationRepository(sessions), ExchangeSecurityAuditRepository()
    )


def _principal(identity_id: uuid.UUID) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        identity_id=identity_id,
        issuer=ISSUER,
        subject=str(identity_id),
        platform_admin=False,
    )


def _seed(sessions: sessionmaker, *, key_prefix: str, clerks: int = 2):
    """A provider organization, one ORG_ADMIN, ``clerks`` BILLING_CLERKs, and a synthetic patient.

    Built through the same repositories ``exchange/api.py`` uses, so every uniqueness, role-matrix
    and tenant rule a real request is subject to applies here too -- including that
    ``BILLING_CLERK`` is an allowed role at a PROVIDER, which is itself part of what ADR-066 added.

    The patient is inserted directly because ``PatientRepository.create`` also creates a
    ``PatientReference``, which nothing here needs: an invoice is scoped to the ``Patient``.
    """
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)
    roles = OrganizationRoleAssignmentRepository(sessions)

    organization = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")

    def _member(subject: str, role: OrganizationRole) -> uuid.UUID:
        identity, _ = identities.get_or_create(issuer=ISSUER, subject=subject, display_name=subject)
        membership, _ = memberships.create(organization_id=organization.id, identity_id=identity.id)
        roles.assign(membership_id=membership.id, role=role)
        return identity.id

    admin_id = _member(f"{key_prefix}-admin", OrganizationRole.ORG_ADMIN)
    clerk_ids = [
        _member(f"{key_prefix}-clerk-{index}", OrganizationRole.BILLING_CLERK)
        for index in range(clerks)
    ]

    with sessions.begin() as session:
        patient = Patient(
            provider_organization_id=organization.id,
            display_name=f"{key_prefix} patient (synthetic)",
        )
        session.add(patient)
        session.flush()
        patient_id = patient.id

    return organization, admin_id, clerk_ids, patient_id


def _service(sessions, organization_id, admin_id, name: str, amount: str):
    return _repository(sessions).create_service(
        principal=_principal(admin_id),
        organization_id=organization_id,
        name=name,
        amount=Decimal(amount),
    )


def _invoice(sessions, organization_id, clerk_id, patient_id, service_id, quantity: int = 1):
    return (
        _repository(sessions)
        .create_invoice(
            principal=_principal(clerk_id),
            organization_id=organization_id,
            patient_id=patient_id,
            lines=[
                LineRequest(
                    source_type=BillableSourceType.CLINIC_SERVICE,
                    source_id=service_id,
                    quantity=quantity,
                )
            ],
            idempotency_key=f"inv-{uuid.uuid4()}",
        )
        .invoice
    )


def _pay(sessions, organization_id, clerk_id, invoice_id, amount: str, *, key: str | None = None):
    return _repository(sessions).record_payment(
        principal=_principal(clerk_id),
        organization_id=organization_id,
        invoice_id=invoice_id,
        amount=Decimal(amount),
        method=PaymentMethod.CASH,
        reference=None,
        idempotency_key=key or f"pay-{uuid.uuid4()}",
    )


def _run_concurrently(operations):
    """Runs each callable on its own thread, released simultaneously by a barrier.

    Returns a list of ``(index, result_or_exception)``. A barrier maximizes overlap; it cannot
    *guarantee* the transactions interleave at the worst possible instant, which is why every
    assertion below is about the final state rather than about timing.
    """
    barrier = threading.Barrier(len(operations))
    outcomes: list[tuple[int, object]] = []
    lock = threading.Lock()

    def worker(index: int, operation):
        barrier.wait()
        try:
            result = operation()
        except Exception as error:  # noqa: BLE001 -- the exception *is* the observation here
            result = error
        with lock:
            outcomes.append((index, result))

    threads = [
        threading.Thread(target=worker, args=(index, operation))
        for index, operation in enumerate(operations)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a worker deadlocked"
    return sorted(outcomes)


def _paid_total(sessions, invoice_id) -> Decimal:
    """Summed from the rows, never from the API. If the lock failed, the API and the table would
    still agree with each other -- it is the *table* that must not exceed the invoice total."""
    with sessions() as session:
        return Decimal(
            session.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.invoice_id == invoice_id
                )
            )
        )


# == (B) and (D): concurrent payments against one balance =========================================


def test_qual1_two_cashiers_taking_the_last_payment_cannot_both_succeed(sessions):
    """Race B. The single most expensive bug this domain could have.

    Two cashiers, two tills, the same 3 000 DJF invoice, the same instant. Without the invoice's
    ``FOR UPDATE`` both read ``paid = 0``, both conclude 3 000 is owed, and both write a payment:
    the facility has recorded 6 000 DJF against a 3 000 DJF invoice and has no credit model to hold
    the difference. Exactly one must succeed and the other must be told why."""
    prefix = f"qual1-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    invoice = _invoice(sessions, organization.id, clerk_ids[0], patient_id, service.id)
    assert invoice.balance == Decimal("3000.00")

    outcomes = _run_concurrently(
        [
            lambda clerk=clerk: (
                lambda: _pay(sessions, organization.id, clerk, invoice.id, "3000.00")
            )()
            for clerk in clerk_ids
        ]
    )
    results = [result for _, result in outcomes]
    refused = [result for result in results if isinstance(result, OverpaymentRefused)]
    accepted = [result for result in results if not isinstance(result, Exception)]

    assert len(accepted) == 1, f"both payments were accepted: {results}"
    assert len(refused) == 1, f"the loser was not refused as an overpayment: {results}"

    assert _paid_total(sessions, invoice.id) == Decimal("3000.00")
    final = _repository(sessions).get_invoice(organization.id, invoice.id)
    assert final is not None
    assert final.balance == Decimal("0.00")
    assert final.status == "PAID"
    assert len(final.payments) == 1


def test_qual2_a_payment_in_flight_when_the_invoice_becomes_fully_paid(sessions):
    """Race D, which is the version that actually happens at a till.

    1 000 DJF has already been taken against a 3 000 DJF invoice. Two cashiers then each try to
    take the remaining 2 000 at the same instant. One must succeed; the other must be refused
    against the balance *as it stands after the first commits*, not as it stood when they read it.
    """
    prefix = f"qual2-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    invoice = _invoice(sessions, organization.id, clerk_ids[0], patient_id, service.id)
    _pay(sessions, organization.id, clerk_ids[0], invoice.id, "1000.00")

    outcomes = _run_concurrently(
        [
            lambda clerk=clerk: (
                lambda: _pay(sessions, organization.id, clerk, invoice.id, "2000.00")
            )()
            for clerk in clerk_ids
        ]
    )
    results = [result for _, result in outcomes]
    assert sum(1 for r in results if not isinstance(r, Exception)) == 1, results
    assert sum(1 for r in results if isinstance(r, OverpaymentRefused)) == 1, results

    assert _paid_total(sessions, invoice.id) == Decimal("3000.00")
    final = _repository(sessions).get_invoice(organization.id, invoice.id)
    assert final is not None and final.status == "PAID"
    # Sequence numbers stay dense and unique -- a receipt reference derives from them, so a gap or
    # a duplicate would put two different receipts under one number.
    assert sorted(payment.sequence_number for payment in final.payments) == [1, 2]


def test_qual3_two_partial_payments_that_fit_both_succeed(sessions):
    """The control for the two tests above: the lock must serialize, not refuse.

    A guarantee that only ever says no is easy to implement and useless. Two cashiers taking 1 000
    each against a 3 000 invoice are both legitimate, and both must succeed -- so a future "fix"
    that turns concurrent payment into a conflict error reddens here."""
    prefix = f"qual3-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    invoice = _invoice(sessions, organization.id, clerk_ids[0], patient_id, service.id)

    outcomes = _run_concurrently(
        [
            lambda clerk=clerk: (
                lambda: _pay(sessions, organization.id, clerk, invoice.id, "1000.00")
            )()
            for clerk in clerk_ids
        ]
    )
    results = [result for _, result in outcomes]
    assert all(not isinstance(result, Exception) for result in results), results

    assert _paid_total(sessions, invoice.id) == Decimal("2000.00")
    final = _repository(sessions).get_invoice(organization.id, invoice.id)
    assert final is not None
    assert final.balance == Decimal("1000.00")
    assert final.status == "PARTIALLY_PAID"
    assert sorted(payment.sequence_number for payment in final.payments) == [1, 2]


def test_qual4_a_double_submitted_payment_is_recorded_once(sessions):
    """One ``Idempotency-Key``, two threads -- a genuine double-submit, not a sequential replay.

    Both callers must be served and exactly one ``Payment`` row must exist, because the failure
    mode is a patient charged twice for one handover of cash."""
    prefix = f"qual4-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    invoice = _invoice(sessions, organization.id, clerk_ids[0], patient_id, service.id)
    shared_key = f"double-submit-{uuid.uuid4()}"

    outcomes = _run_concurrently(
        [
            lambda: _pay(
                sessions, organization.id, clerk_ids[0], invoice.id, "1500.00", key=shared_key
            )
        ]
        * 2
    )
    results = [result for _, result in outcomes]
    assert all(not isinstance(result, Exception) for result in results), results
    assert sum(1 for result in results if result.created) == 1, "two rows claimed to be created"
    assert len({result.payment.id for result in results}) == 1, "two distinct payments"

    assert _paid_total(sessions, invoice.id) == Decimal("1500.00")
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Payment).where(Payment.invoice_id == invoice.id)
            )
            == 1
        )


# == (A) and invoice numbering ====================================================================


def test_qual5_a_double_submitted_invoice_is_issued_once(sessions):
    """Race A. Two clicks on `Créer la facture`, one key, one invoice."""
    prefix = f"qual5-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    shared_key = f"double-invoice-{uuid.uuid4()}"

    def create():
        return _repository(sessions).create_invoice(
            principal=_principal(clerk_ids[0]),
            organization_id=organization.id,
            patient_id=patient_id,
            lines=[
                LineRequest(
                    source_type=BillableSourceType.CLINIC_SERVICE,
                    source_id=service.id,
                    quantity=1,
                )
            ],
            idempotency_key=shared_key,
        )

    outcomes = _run_concurrently([create, create])
    results = [result for _, result in outcomes]
    assert all(not isinstance(result, Exception) for result in results), results
    assert sum(1 for result in results if result.created) == 1, "two invoices claimed creation"
    assert len({result.invoice.id for result in results}) == 1
    assert len({result.invoice.number for result in results}) == 1

    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Invoice)
                .where(Invoice.organization_id == organization.id)
            )
            == 1
        )


def test_qual6_concurrent_invoices_never_share_a_number(sessions):
    """The counter row's ``FOR UPDATE``, which is what ``count(*) + 1`` would get wrong.

    Six unrelated invoices at the same instant. Every one must be issued, and the six numbers must
    be six distinct numbers -- two patients holding paper that says ``FAC-2026-000004`` is a
    reconciliation problem no amount of later tooling fixes."""
    prefix = f"qual6-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix, clerks=6)
    service = _service(sessions, organization.id, admin_id, "Consultation", "1000.00")

    outcomes = _run_concurrently(
        [
            lambda clerk=clerk: (
                lambda: _invoice(sessions, organization.id, clerk, patient_id, service.id)
            )()
            for clerk in clerk_ids
        ]
    )
    results = [result for _, result in outcomes]
    assert all(not isinstance(result, Exception) for result in results), results

    numbers = [result.number for result in results]
    assert len(set(numbers)) == len(numbers), f"a number was issued twice: {sorted(numbers)}"
    assert all(number.startswith("FAC-") for number in numbers), numbers
    # The counter ends where the invoices do: no allocation was lost or double-consumed.
    with sessions() as session:
        next_value = session.scalar(
            text(
                "SELECT next_value FROM exchange_invoice_number_sequences "
                "WHERE organization_id = :org"
            ),
            {"org": organization.id},
        )
    assert next_value == len(numbers) + 1


# == (C): a price change during invoicing =========================================================


def test_qual7_a_price_change_mid_invoice_never_produces_a_blended_line(sessions):
    """Race C. The line must be frozen at one of the two real prices, never between them.

    Which one wins is genuinely a race and this test does not care -- both answers are correct and
    both are defensible to a patient. What would not be defensible is a line at some third amount,
    a line whose ``line_total`` disagrees with ``quantity * unit_amount``, or a line that changes
    after the invoice was issued. The last of those is asserted by re-reading at the end."""
    prefix = f"qual7-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")

    outcomes = _run_concurrently(
        [
            lambda: _invoice(
                sessions, organization.id, clerk_ids[0], patient_id, service.id, quantity=2
            ),
            lambda: _repository(sessions).change_price(
                principal=_principal(admin_id),
                organization_id=organization.id,
                service_id=service.id,
                amount=Decimal("4500.00"),
            ),
        ]
    )
    results = dict(outcomes)
    assert not isinstance(results[0], Exception), results[0]
    assert not isinstance(results[1], Exception), results[1]

    invoice = results[0]
    (line,) = invoice.lines
    assert line.unit_amount in (Decimal("3000.00"), Decimal("4500.00")), line.unit_amount
    assert line.line_total == line.unit_amount * 2
    assert invoice.total == line.line_total

    frozen_amount, frozen_total = line.unit_amount, invoice.total

    # And a third price change afterwards moves nothing that was already issued.
    _repository(sessions).change_price(
        principal=_principal(admin_id),
        organization_id=organization.id,
        service_id=service.id,
        amount=Decimal("9999.00"),
    )
    reread = _repository(sessions).get_invoice(organization.id, invoice.id)
    assert reread is not None
    assert reread.lines[0].unit_amount == frozen_amount
    assert reread.total == frozen_total


# == tenant boundaries, in the database itself ====================================================


def test_qual8_a_payment_cannot_be_attached_to_another_organizations_invoice(sessions):
    """The application refuses this (``test_billauth9``). Here the database refuses it too.

    ``Payment`` carries ``organization_id`` and its foreign key to ``Invoice`` is composite on
    ``(invoice_id, organization_id)``, so a row claiming to be Clinic B's payment against Clinic A's
    invoice has no parent to point at. This is the layer that survives an application bug, and it
    is unenforced under SQLite because that engine has foreign keys switched off here."""
    prefix = f"qual8-{uuid.uuid4().hex[:8]}"
    org_a, admin_a, clerks_a, patient_a = _seed(sessions, key_prefix=f"{prefix}-a")
    org_b, _admin_b, _clerks_b, _patient_b = _seed(sessions, key_prefix=f"{prefix}-b")
    service = _service(sessions, org_a.id, admin_a, "Consultation", "3000.00")
    invoice = _invoice(sessions, org_a.id, clerks_a[0], patient_a, service.id)

    # The application layer, first.
    with pytest.raises(BillingNotFound):
        _pay(sessions, org_b.id, clerks_a[0], invoice.id, "1000.00")

    # Then the database, with the application bypassed entirely.
    with pytest.raises((IntegrityError, DBAPIError)):
        with sessions.begin() as session:
            session.add(
                Payment(
                    invoice_id=invoice.id,
                    organization_id=org_b.id,
                    sequence_number=1,
                    amount=Decimal("1000.00"),
                    method=PaymentMethod.CASH.value,
                    idempotency_key=f"forced-{uuid.uuid4()}",
                    recorded_by_identity_id=clerks_a[0],
                )
            )

    assert _paid_total(sessions, invoice.id) == Decimal("0")


def test_qual9_an_invoice_line_cannot_disagree_with_its_invoices_currency(sessions):
    """One invoice, one currency, enforced by a composite foreign key rather than by a comment.

    A mixed-currency invoice has no meaningful total, and a total nobody can compute is the point
    at which a balance stops being trustworthy."""
    prefix = f"qual9-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    invoice = _invoice(sessions, organization.id, clerk_ids[0], patient_id, service.id)

    with pytest.raises((IntegrityError, DBAPIError)):
        with sessions.begin() as session:
            session.add(
                InvoiceLine(
                    invoice_id=invoice.id,
                    source_type=BillableSourceType.CLINIC_SERVICE.value,
                    clinic_service_id=service.id,
                    description_snapshot="Consultation en euros",
                    quantity=1,
                    unit_amount=Decimal("25.00"),
                    currency="EUR",
                )
            )


def test_qual10_the_polymorphic_source_check_is_real(sessions):
    """``source_type`` and the typed foreign keys cannot disagree.

    ``docs/NATIVE-OPERATING-PRODUCT.md`` §19 chose two nullable typed columns over a generic
    ``(type, id)`` pair precisely so the database could enforce this. A line claiming to be a
    ``LAB_ORDER_ITEM`` while pointing at a ``ClinicService`` would be a charge whose subject nobody
    can resolve, and the CHECK refuses it."""
    prefix = f"qual10-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    invoice = _invoice(sessions, organization.id, clerk_ids[0], patient_id, service.id)

    for label, kwargs in (
        (
            "type says LAB_ORDER_ITEM but a clinic service is attached",
            {
                "source_type": BillableSourceType.LAB_ORDER_ITEM.value,
                "clinic_service_id": service.id,
            },
        ),
        (
            "type says CLINIC_SERVICE but nothing is attached",
            {"source_type": BillableSourceType.CLINIC_SERVICE.value},
        ),
    ):
        refused = False
        try:
            with sessions.begin() as session:
                session.add(
                    InvoiceLine(
                        invoice_id=invoice.id,
                        description_snapshot="ligne incohérente",
                        quantity=1,
                        unit_amount=Decimal("100.00"),
                        currency="DJF",
                        **kwargs,
                    )
                )
        except (IntegrityError, DBAPIError):
            refused = True
        assert refused, f"the database accepted a line where {label}"


def test_qual11_money_checks_are_enforced_by_the_backend(sessions):
    """Zero and negative money, refused by the schema rather than by the service layer.

    SQLite's permissive ``NUMERIC`` handling means the unit suite asserts these constraints exist;
    only a real backend demonstrates that they bite."""
    prefix = f"qual11-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    invoice = _invoice(sessions, organization.id, clerk_ids[0], patient_id, service.id)

    for amount in (Decimal("0.00"), Decimal("-500.00")):
        with pytest.raises((IntegrityError, DBAPIError)):
            with sessions.begin() as session:
                session.add(
                    Payment(
                        invoice_id=invoice.id,
                        organization_id=organization.id,
                        sequence_number=99,
                        amount=amount,
                        method=PaymentMethod.CASH.value,
                        idempotency_key=f"bad-{uuid.uuid4()}",
                        recorded_by_identity_id=clerk_ids[0],
                    )
                )

    with pytest.raises((IntegrityError, DBAPIError)):
        with sessions.begin() as session:
            session.add(
                InvoiceLine(
                    invoice_id=invoice.id,
                    source_type=BillableSourceType.CLINIC_SERVICE.value,
                    clinic_service_id=service.id,
                    description_snapshot="quantité nulle",
                    quantity=0,
                    unit_amount=Decimal("100.00"),
                    currency="DJF",
                )
            )


# == audit coupling ===============================================================================


def test_qual12_a_refused_payment_leaves_no_audit_row(sessions):
    """The audit write and the mutation share one transaction, so they commit or roll back together.

    A refusal that still wrote ``payment_recorded`` would make the audit trail claim money was
    taken when none was -- worse than no audit at all, because it would be believed. Only a real
    transaction can demonstrate this; SQLite would report the same counts for the wrong reason."""
    prefix = f"qual12-{uuid.uuid4().hex[:8]}"
    organization, admin_id, clerk_ids, patient_id = _seed(sessions, key_prefix=prefix)
    service = _service(sessions, organization.id, admin_id, "Consultation", "3000.00")
    invoice = _invoice(sessions, organization.id, clerk_ids[0], patient_id, service.id)

    def audit_count(event_type: str) -> int:
        with sessions() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(ExchangeSecurityAuditEvent)
                    .where(
                        ExchangeSecurityAuditEvent.resource_id == invoice.id,
                        ExchangeSecurityAuditEvent.event_type == event_type,
                    )
                )
                or 0
            )

    before = audit_count("payment_recorded")
    with pytest.raises(OverpaymentRefused):
        _pay(sessions, organization.id, clerk_ids[0], invoice.id, "9999.00")
    assert audit_count("payment_recorded") == before, "a refused payment was audited as recorded"

    _pay(sessions, organization.id, clerk_ids[0], invoice.id, "3000.00")
    assert audit_count("payment_recorded") == before + 1

    # And no amount ever reaches the audit table -- §41's rule, re-checked against the real column
    # set rather than against the ORM's view of it.
    with sessions() as session:
        rows = session.execute(
            text(
                "SELECT * FROM exchange_security_audit_events "
                "WHERE resource_id = :rid AND event_type = 'payment_recorded'"
            ),
            {"rid": invoice.id},
        ).mappings()
        for row in rows:
            rendered = " ".join(str(value) for value in row.values())
            assert "3000" not in rendered, rendered
