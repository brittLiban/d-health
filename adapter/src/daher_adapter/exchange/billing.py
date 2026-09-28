"""DHEALTH-BILLING-001: facility billing -- services, invoices, payments, balances, receipts.

A facility could run a consultation, order a test, release a result and review it, and still had to
reach for a paper ledger or a second application to answer *what does this cost, what was paid, what
is owed, and what receipt did we give them*. `docs/NATIVE-OPERATING-PRODUCT.md` §18 named that P0-3
for both the clinic and the laboratory. This module is the narrowest thing that answers it.

── The rule that shapes everything here: service-performance ownership ──

**An organization may invoice only for services it actually performed** (ADR-066). A clinic bills
the consultation it gave and the in-house services it ran; a laboratory bills the tests it ran. When
Clinic A routes an order to Lab B, Clinic A bills its consultation and **Lab B bills the tests** --
Clinic A may not put Lab B's work on an invoice.

That is enforced here, server-side, and it is deliberately *not* satisfied by authorization alone:
holding `BILLING_OPERATE` at an organization proves you may bill for that organization, and proves
nothing about whether that organization did the work. `_require_performed_here` is the check that
closes the gap, and knowing a lab order item's id is not authority to charge for it.

This is the narrow MVP rule, not the commercial answer. **D-2a** -- whether a routed order is
ultimately paid by the patient to the lab, by the clinic to the lab, or by the patient to the clinic
which then settles -- remains open, and nothing here pre-empts it. There is no settlement, no
accounts payable between facilities, no commission, no referral fee, no markup and no pass-through.

── Money ──

`Decimal` and `Numeric(12, 2)` everywhere, never a float: binary floating point cannot represent
0.1, so sums drift and a balance that should be exactly zero is not. One currency per invoice,
enforced by a composite foreign key rather than by code. No conversion, no rates, no
foreign-exchange engine.

── Balance is derived, always ──

§18's Law 8. `balance = total - sum(payments)`, computed on read, never stored. A stored balance is
a second source of truth that drifts from the payment rows; so is a stored `status`, which is why
`OPEN`/`PARTIALLY_PAID`/`PAID` is likewise computed and no such column exists.

── Immutability, and the gaps that are named rather than filled ──

An invoice's lines freeze their own price, description, quantity and currency at creation. A payment
is an append-only fact: there is no update path, no delete path, and no reversal. Correction and
voiding are policy questions §26 of the specification left undecided, so this ticket refuses to
invent them (ADR-066) -- the interface says a mis-keyed payment cannot yet be corrected rather than
offering a control that quietly rewrites financial history.

── What this module must never do, structurally ──

It imports nothing from `results.py`, `lab_workflow.py` or `visits.py`, and nothing in those modules
imports this one. A result release, a visit transition, an order's progress and a doctor's review
are all unable to observe an invoice or a balance, and a payment can change none of them. That
independence is the product's hard rule (§27, D-2b's recorded default) and is asserted by a test
that reads the import graph, not merely by this paragraph.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.models import (
    BillableSourceType,
    ClinicService,
    ClinicServicePrice,
    Invoice,
    InvoiceLine,
    InvoiceNumberSequence,
    LabOrder,
    LabOrderItem,
    LabOrderStatus,
    LabSelection,
    Patient,
    PatientReference,
    Payment,
    PaymentMethod,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import (
    ExchangeSecurityAuditRepository,
    SecurityEventType,
    SecurityOutcome,
)
from daher_adapter.models import utc_now


class BillingNotFound(RuntimeError):
    """No such invoice, service or payment -- or it exists and belongs to another organization.

    **One exception for both cases, deliberately.** Distinguishing them would confirm that another
    facility's invoice number is real, which is precisely the cross-tenant disclosure §25 forbids.
    Maps to HTTP 404."""


class BillingPatientNotFound(RuntimeError):
    """No such patient, or this organization has no basis to bill them. Maps to HTTP 404."""


class ServiceNotPerformedHere(RuntimeError):
    """ADR-066's service-performance rule refused this line: the invoicing organization did not
    perform the service being charged for.

    The case this exists for is Clinic A trying to bill for a test Lab B ran on a routed order.
    Clinic A holds `BILLING_OPERATE` at Clinic A, so authorization passes; what refuses it is that
    the
    work was not theirs. Maps to HTTP 403 rather than 404: the caller is a legitimate billing user
    of a real organization and the refusal is about the *service*, not about whether they may bill
    at all."""


class InvoiceHasNoLines(ValueError):
    """An invoice with no lines is not a bill. Maps to HTTP 422."""


class BillingCurrencyMismatch(ValueError):
    """Two lines, two currencies, one invoice. Refused rather than converted -- there is no
    foreign-exchange engine here and an invoice whose lines disagree has no meaningful total. Maps
    to HTTP 422."""


class ServiceNameTaken(RuntimeError):
    """This facility already lists a service by that name.

    A 409 rather than a 422, matching every other duplicate in this codebase: the request is
    well-formed and the caller is permitted, and it is the facility's existing state that refuses
    it. Names are unique per organization so a clerk picking a line from a list is never choosing
    between two identical-looking entries."""


class ServicePriceMissing(RuntimeError):
    """The facility has not configured a current price for this service, so there is nothing
    truthful to charge. Refused rather than billed as zero: a zero line would silently claim the
    service is free. Maps to HTTP 409."""


class OverpaymentRefused(RuntimeError):
    """This payment would take the invoice past its total.

    Refused because this ticket has **no credit, wallet or refund model** -- accepting the money
    would create a patient credit that nothing in DHealth can represent, report or return. Evaluated
    under a row lock, so two simultaneous payments cannot both see the same remaining balance and
    both succeed. Maps to HTTP 409."""


class BillingIdempotencyConflict(RuntimeError):
    """This `(organization, idempotency_key)` pair was already used for a materially different
    invoice or payment. Refused rather than returning the stored one, exactly as `LabOrderIntent`
    does: handing back a different invoice because a client reused a key is the cross-wiring the
    check exists to prevent. Maps to HTTP 409."""


@dataclass(frozen=True, slots=True)
class ClinicServiceRecord:
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    active: bool
    amount: Decimal | None
    currency: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class InvoiceLineRecord:
    id: uuid.UUID
    source_type: str
    clinic_service_id: uuid.UUID | None
    lab_order_item_id: uuid.UUID | None
    description: str
    quantity: int
    unit_amount: Decimal
    currency: str
    line_total: Decimal


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    id: uuid.UUID
    invoice_id: uuid.UUID
    sequence_number: int
    amount: Decimal
    method: str
    reference: str | None
    recorded_by_identity_id: uuid.UUID
    recorded_at: datetime
    #: `FAC-2026-000123-R2`. Derived from the invoice number and this payment's ordinal, so every
    #: receipt has a stable human-readable identity without a second counter table.
    receipt_reference: str


@dataclass(frozen=True, slots=True)
class InvoiceRecord:
    """One invoice, with everything derived that Law 8 says must not be stored."""

    id: uuid.UUID
    organization_id: uuid.UUID
    patient_id: uuid.UUID
    patient_display_name: str | None
    number: str
    currency: str
    issued_at: datetime
    created_by_identity_id: uuid.UUID
    lines: tuple[InvoiceLineRecord, ...]
    payments: tuple[PaymentRecord, ...]
    total: Decimal
    paid: Decimal
    balance: Decimal
    #: `OPEN` / `PARTIALLY_PAID` / `PAID`, computed from the two figures above. Never a column.
    status: str


@dataclass(frozen=True, slots=True)
class BillingPatientRecord:
    """The minimum safe identity a cashier needs to be sure who they are charging.

    **Three fields, and the smallness is the design.** A name alone bills the wrong person when two
    patients share one; a date of birth is the minimum second fact that tells them apart and the one
    a clerk can confirm with the person in front of them. Everything else a `Patient` row holds --
    contact details above all -- is absent because no billing workflow in this release has
    demonstrated a need for it, and "it was already on the model" is not a need.

    Deliberately **not** a reuse of the clinician-facing patient projection. A shared serializer is
    how a clinical field eventually arrives on a cashier's screen: somebody adds a column to the one
    the doctor sees, and it appears here too, silently. This type cannot grow that way, and
    `test_bill36` pins its exact field set against the HTTP response."""

    id: uuid.UUID
    display_name: str | None
    date_of_birth: date | None


@dataclass(frozen=True, slots=True)
class BillableSourceRecord:
    """One thing this organization could charge this patient for.

    The *suggestion* half of §28's rule that a clinical event must never silently create a payable
    invoice. A clerk is shown what is chargeable and confirms it; nothing here bills anything."""

    source_type: str
    source_id: uuid.UUID
    description: str
    amount: Decimal | None
    currency: str | None
    already_invoiced: bool


@dataclass(frozen=True, slots=True)
class BillableOrderRecord:
    """One order a laboratory took, reduced to exactly what billing it requires.

    The laboratory's entry point, because a lab clerk has no patient directory to start from. It
    carries
    the patient's display identity and the names of the tests -- nothing about what the tests found,
    what state the specimen is in, or what any clinician wrote."""

    lab_order_id: uuid.UUID
    patient_id: uuid.UUID
    patient_display_name: str | None
    status: str
    created_at: datetime
    items: tuple[BillableSourceRecord, ...]


@dataclass(frozen=True, slots=True)
class CreateInvoiceResult:
    invoice: InvoiceRecord
    created: bool


@dataclass(frozen=True, slots=True)
class RecordPaymentResult:
    payment: PaymentRecord
    invoice: InvoiceRecord
    created: bool


@dataclass(frozen=True, slots=True)
class LineRequest:
    """One requested charge, before it becomes a frozen line.

    Carries the *source*, never an amount: the price comes from the facility's own configuration at
    invoicing time. A caller-supplied amount would let a browser decide what a patient is
    charged."""

    source_type: BillableSourceType
    source_id: uuid.UUID
    quantity: int = 1


#: The order states in which a laboratory has actually taken the work, and may therefore charge for
#: it. Assignment alone (`SELECTED`) is not enough -- see `_require_performed_here`. `REJECTED` and
#: `CANCELLED` are excluded because the test will not be performed at all.
BILLABLE_ORDER_STATUSES: frozenset[str] = frozenset(
    {
        LabOrderStatus.ACCEPTED.value,
        LabOrderStatus.IN_PROGRESS.value,
        LabOrderStatus.COMPLETED.value,
    }
)


def _money(value: Decimal) -> Decimal:
    """Two decimal places, half-up, matching the column. Every arithmetic result passes through
    here so a total and a balance can never disagree about rounding."""
    return Decimal(value).quantize(Decimal("0.01"))


def _status_for(total: Decimal, paid: Decimal) -> str:
    if paid <= Decimal("0"):
        return "OPEN"
    if paid >= total:
        return "PAID"
    return "PARTIALLY_PAID"


class BillingRepository:
    """Every financial mutation, each one transaction, each audited in that same transaction."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        organizations: OrganizationRepository,
        security_audit: ExchangeSecurityAuditRepository,
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations
        self._audit = security_audit

    # -- the facility's own services -------------------------------------------------------------

    def create_service(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        name: str,
        amount: Decimal,
        currency: str = "DJF",
    ) -> ClinicServiceRecord:
        """Add one service this facility performs and charges for, with its first price."""
        label = name.strip()
        if not label:
            raise ValueError("a service name is required")
        if amount < 0:
            raise ValueError("a price may not be negative")

        with self._sessions.begin() as session:
            service = ClinicService(organization_id=organization_id, name=label, active=True)
            session.add(service)
            try:
                session.flush()
            except IntegrityError as error:
                raise ServiceNameTaken(f"a service named {label!r} already exists here") from error
            price = ClinicServicePrice(
                clinic_service_id=service.id, amount=_money(amount), currency=currency
            )
            session.add(price)
            session.flush()
            self._record(
                session,
                event_type="clinic_service_created",
                principal=principal,
                organization_id=organization_id,
                resource_type="clinic_service",
                resource_id=service.id,
                outcome="created",
            )
            return _to_service_record(service, price)

    def change_price(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        service_id: uuid.UUID,
        amount: Decimal,
        currency: str = "DJF",
    ) -> ClinicServiceRecord:
        """Supersede the current price rather than mutating it, exactly as `LabPrice` does.

        **No invoice changes.** Lines froze their own amount at creation; this only affects what the
        *next* invoice will charge. NC-BILL-3 exists to prove that separation is real."""
        if amount < 0:
            raise ValueError("a price may not be negative")

        with self._sessions.begin() as session:
            service = self._service_in_session(session, organization_id, service_id)
            current = session.scalar(
                select(ClinicServicePrice).where(
                    ClinicServicePrice.clinic_service_id == service.id,
                    ClinicServicePrice.superseded_at.is_(None),
                )
            )
            now = utc_now()
            if current is not None:
                current.superseded_at = now
                session.flush()
            price = ClinicServicePrice(
                clinic_service_id=service.id,
                amount=_money(amount),
                currency=currency,
                effective_at=now,
            )
            session.add(price)
            session.flush()
            self._record(
                session,
                event_type="clinic_service_price_changed",
                principal=principal,
                organization_id=organization_id,
                resource_type="clinic_service",
                resource_id=service.id,
                outcome="updated",
            )
            return _to_service_record(service, price)

    def list_services(self, organization_id: uuid.UUID) -> list[ClinicServiceRecord]:
        with self._sessions() as session:
            rows = session.execute(
                select(ClinicService, ClinicServicePrice)
                .outerjoin(
                    ClinicServicePrice,
                    (ClinicServicePrice.clinic_service_id == ClinicService.id)
                    & (ClinicServicePrice.superseded_at.is_(None)),
                )
                .where(ClinicService.organization_id == organization_id)
                .order_by(ClinicService.name)
            ).all()
            return [_to_service_record(service, price) for service, price in rows]

    # -- invoices --------------------------------------------------------------------------------

    def create_invoice(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        lines: list[LineRequest],
        idempotency_key: str,
    ) -> CreateInvoiceResult:
        """Issue one invoice, freezing a price snapshot per line.

        Order of operations, and why: the patient is proved billable by this organization, then
        **each line's source is proved to be work this organization performed** (ADR-066), then the
        prices are read and frozen, then the number is allocated under a lock. Validating the source
        before reading a price means a caller cannot learn another facility's prices by asking for a
        line they are not entitled to."""
        if not lines:
            raise InvoiceHasNoLines("an invoice needs at least one line")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")

        with self._sessions.begin() as session:
            patient = self._patient_for_billing(session, organization_id, patient_id)

            existing = session.scalar(
                select(Invoice).where(
                    Invoice.organization_id == organization_id,
                    Invoice.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.patient_id != patient_id:
                    raise BillingIdempotencyConflict(
                        f"idempotency_key {idempotency_key!r} already issued an invoice for a "
                        f"different patient -- refusing to return mismatched data"
                    )
                return CreateInvoiceResult(
                    invoice=self._to_invoice_record(session, existing, patient), created=False
                )

            prepared: list[tuple[LineRequest, str, Decimal, str]] = []
            for request in lines:
                if request.quantity <= 0:
                    raise ValueError("quantity must be positive")
                description, amount, currency = self._resolve_source(
                    session, organization_id, request
                )
                prepared.append((request, description, amount, currency))

            currencies = {currency for _r, _d, _a, currency in prepared}
            if len(currencies) > 1:
                raise BillingCurrencyMismatch(
                    f"an invoice carries one currency; these lines name {sorted(currencies)}"
                )
            currency = currencies.pop()

            number = self._next_invoice_number(session, organization_id)
            invoice = Invoice(
                organization_id=organization_id,
                patient_id=patient_id,
                number=number,
                currency=currency,
                idempotency_key=idempotency_key,
                created_by_identity_id=principal.identity_id,
            )
            try:
                # A SAVEPOINT, because the read above is not the whole of idempotency.
                #
                # Two threads submitting the same key both find nothing, both build an invoice, and
                # PostgreSQL then refuses the second at `uq_exchange_invoices_org_idempotency`. That
                # is the constraint doing its job -- but an `IntegrityError` reaching the caller
                # means a double-submitted `Créer la facture` shows an error to a clerk whose
                # invoice was in fact issued, and the natural reaction is to issue a second one.
                # So the loser resolves to a replay, exactly as the sequential path does.
                #
                # SQLite never produced this: its writer serialization turned the concurrent
                # submission into a sequential one, so the pre-read always won and this branch was
                # unreachable. Found by
                # `tests/integration/test_dhealthbilling001_postgres_qualification.py`.
                #
                # `session.add` belongs **inside** the SAVEPOINT. `begin_nested()` autoflushes
                # pending objects *before* establishing the savepoint, so adding the invoice first
                # put the failing INSERT outside the savepoint it was supposed to be protected by:
                # the `IntegrityError` then arrived with the outer transaction already dead, and
                # the context manager raised `Can't operate on closed transaction` on the way out.
                with session.begin_nested():
                    session.add(invoice)
                    session.flush()
            except IntegrityError:
                if invoice in session:
                    session.expunge(invoice)
                winner = session.scalar(
                    select(Invoice).where(
                        Invoice.organization_id == organization_id,
                        Invoice.idempotency_key == idempotency_key,
                    )
                )
                if winner is None:  # pragma: no cover - a different constraint; do not swallow it
                    raise
                if winner.patient_id != patient_id:
                    raise BillingIdempotencyConflict(
                        f"idempotency_key {idempotency_key!r} already issued an invoice for a "
                        f"different patient -- refusing to return mismatched data"
                    ) from None
                # The number this call allocated is simply not used. `_next_invoice_number` already
                # states that this is not a gapless fiscal sequence; burning one value is the right
                # trade against returning an error for an invoice that exists.
                return CreateInvoiceResult(
                    invoice=self._to_invoice_record(session, winner, patient), created=False
                )

            for request, description, amount, line_currency in prepared:
                session.add(
                    InvoiceLine(
                        invoice_id=invoice.id,
                        source_type=request.source_type.value,
                        clinic_service_id=(
                            request.source_id
                            if request.source_type is BillableSourceType.CLINIC_SERVICE
                            else None
                        ),
                        lab_order_item_id=(
                            request.source_id
                            if request.source_type is BillableSourceType.LAB_ORDER_ITEM
                            else None
                        ),
                        description_snapshot=description,
                        quantity=request.quantity,
                        unit_amount=amount,
                        currency=line_currency,
                    )
                )
            session.flush()
            self._record(
                session,
                event_type="invoice_created",
                principal=principal,
                organization_id=organization_id,
                resource_type="invoice",
                resource_id=invoice.id,
                outcome="created",
            )
            return CreateInvoiceResult(
                invoice=self._to_invoice_record(session, invoice, patient), created=True
            )

    def list_invoices(
        self,
        organization_id: uuid.UUID,
        *,
        patient_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[InvoiceRecord]:
        with self._sessions() as session:
            stmt = (
                select(Invoice)
                .where(Invoice.organization_id == organization_id)
                .order_by(Invoice.issued_at.desc())
                .limit(limit)
            )
            if patient_id is not None:
                stmt = stmt.where(Invoice.patient_id == patient_id)
            return [
                self._to_invoice_record(session, invoice, session.get(Patient, invoice.patient_id))
                for invoice in session.scalars(stmt).all()
            ]

    def get_invoice(
        self, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> InvoiceRecord | None:
        """Tenant-safe lookup. `None` both when no such invoice exists and when it belongs to
        another organization, so the caller maps both to one 404."""
        with self._sessions() as session:
            invoice = session.scalar(
                select(Invoice).where(
                    Invoice.id == invoice_id, Invoice.organization_id == organization_id
                )
            )
            if invoice is None:
                return None
            return self._to_invoice_record(
                session, invoice, session.get(Patient, invoice.patient_id)
            )

    def list_billable_sources(
        self, organization_id: uuid.UUID, patient_id: uuid.UUID
    ) -> list[BillableSourceRecord]:
        """Everything this organization could charge this patient for, and nothing else.

        Two kinds, and both are filtered by ADR-066's service-performance rule rather than by what
        the caller happens to be able to see:

        * the organization's own **active, priced** services -- a service with no current price is
          omitted rather than offered at zero, because offering it would invite a line that claims
          the service is free;
        * the **lab tests this organization actually performed** for this patient, found through
          ``LabSelection``. A clinic therefore does not see the routed tests its laboratory ran, and
          could not invoice them if it did (``_require_performed_here`` refuses).

        ``already_invoiced`` is advisory only. Nothing refuses a second charge for the same source:
        a facility may legitimately perform a service twice, and a system that silently blocked the
        second one would be wrong more often than the clerk."""
        with self._sessions() as session:
            self._patient_for_billing(session, organization_id, patient_id)

            invoiced_services = set(
                session.scalars(
                    select(InvoiceLine.clinic_service_id)
                    .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                    .where(
                        Invoice.organization_id == organization_id,
                        Invoice.patient_id == patient_id,
                        InvoiceLine.clinic_service_id.is_not(None),
                    )
                ).all()
            )
            invoiced_items = set(
                session.scalars(
                    select(InvoiceLine.lab_order_item_id)
                    .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                    .where(
                        Invoice.organization_id == organization_id,
                        InvoiceLine.lab_order_item_id.is_not(None),
                    )
                ).all()
            )

            sources: list[BillableSourceRecord] = []
            for service, price in session.execute(
                select(ClinicService, ClinicServicePrice)
                .join(
                    ClinicServicePrice,
                    (ClinicServicePrice.clinic_service_id == ClinicService.id)
                    & (ClinicServicePrice.superseded_at.is_(None)),
                )
                .where(
                    ClinicService.organization_id == organization_id,
                    ClinicService.active.is_(True),
                )
                .order_by(ClinicService.name)
            ).all():
                sources.append(
                    BillableSourceRecord(
                        source_type=BillableSourceType.CLINIC_SERVICE.value,
                        source_id=service.id,
                        description=service.name,
                        amount=price.amount,
                        currency=price.currency,
                        already_invoiced=service.id in invoiced_services,
                    )
                )

            performed = session.execute(
                select(LabOrderItem)
                .join(LabOrder, LabOrder.id == LabOrderItem.lab_order_id)
                .join(PatientReference, PatientReference.id == LabOrder.patient_reference_id)
                .join(LabSelection, LabSelection.lab_order_id == LabOrder.id)
                .where(
                    PatientReference.patient_id == patient_id,
                    LabSelection.lab_organization_id == organization_id,
                )
                .order_by(LabOrderItem.catalog_display_name_snapshot)
            ).all()
            for (item,) in performed:
                try:
                    amount, currency = self._lab_item_price(session, organization_id, item)
                except ServicePriceMissing:
                    # Offered with no price rather than hidden: the laboratory performed this test,
                    # so a clerk looking for it should find it and learn that it needs a price,
                    # instead of being told nothing is chargeable.
                    amount, currency = None, None
                sources.append(
                    BillableSourceRecord(
                        source_type=BillableSourceType.LAB_ORDER_ITEM.value,
                        source_id=item.id,
                        description=item.catalog_display_name_snapshot,
                        amount=amount,
                        currency=currency,
                        already_invoiced=item.id in invoiced_items,
                    )
                )
            return sources

    def search_patients(
        self, organization_id: uuid.UUID, *, query: str, limit: int = 20
    ) -> list[BillingPatientRecord]:
        """The clinic till's way in: find the person being billed, and nothing more.

        ── Why this exists separately from the clinician's patient directory ──

        A laboratory clerk starts from an order (`list_billable_orders`). A clinic clerk cannot:
        a consultation is not an order, so there is no row to start from, and the provider's own
        patient directory is behind `require_clinician` -- which a `BILLING_CLERK` deliberately does
        not hold, because being a cashier is not clinical authority (ADR-066 §2).

        Identifying the person you are about to charge is a legitimate *financial* act. It should
        not require clinical authority, and the answer is emphatically not to hand a clerk
        `CLINICIAN` so that a search box works, nor to open the clinician's directory endpoint to
        billing. So this is a second, narrower read with its own gate and its own projection.

        ── What it returns, and why exactly that ──

        `id`, `display_name`, and `date_of_birth` when present. A name alone was considered and
        rejected: two people sharing a name is ordinary, and a till that cannot tell them apart
        bills the wrong person -- a wrong-patient error arriving through the financial door instead
        of the clinical one. The date of birth is the minimum second fact that distinguishes them,
        and it is what a clerk can confirm out loud with the person standing in front of them.

        There is **no facility patient reference number**, because DHealth has none to give. The
        only other identifier in this schema is `PatientReference`, which ADR-051 establishes as a
        *provenance binding for one exchanged transaction* -- one human may hold several, and none
        of them is intended for a human to read aloud. Returning one would look like a patient
        number and would not be one. Inventing a real one is a product decision nobody has made.

        ── What it deliberately cannot reach ──

        Contact details, clinical notes, reason for visit, visits, orders, result values, result
        history and clinician notes are all absent, and none of them is reachable from here: this
        reads `Patient` and joins nothing clinical. `BillingPatientRecord` has three fields and
        `test_bill36` asserts the response carries exactly those, so a future field added to
        `Patient` cannot arrive on a clerk's screen by being added to a shared serializer.

        ── Tenancy ──

        Scoped by exactly the rule `_patient_for_billing` enforces on the write path, so what a
        clerk can find and what the server will let them invoice cannot disagree: a provider
        searches its own directory; a laboratory, which holds no directory of its own (**D-6** is
        open), searches only the patients it has actually done work for. Knowing another
        organization's patient UUID buys nothing -- there is no lookup-by-id here at all, and
        `_patient_for_billing` refuses that id on the invoice call anyway.

        A blank query returns nothing rather than the whole directory. This is a lookup for a person
        who is present, not a browsable roster, and the difference matters: one is the minimum a
        clerk needs, the other is the clinical directory by another name.
        """
        needle = query.strip()
        if not needle:
            return []

        with self._sessions() as session:
            pattern = f"%{needle}%"
            # A laboratory holds no directory of its own, so its reachable set is the patients
            # behind orders it actually performed -- the same `LabSelection` relationship
            # `_patient_for_billing` proves on the write path.
            #
            # An `EXISTS` rather than a `UNION` of two entity selects: a union returns *columns*,
            # so `session.scalars` hands back the first one (a bare UUID) instead of a `Patient`,
            # and this read is one `Patient` row either way. It also keeps the shape identical to
            # `_patient_for_billing`'s own two-branch test, which is what makes "what a clerk can
            # find and what the server will let them invoice cannot disagree" true by construction
            # rather than by two queries that happen to agree today.
            performed_here = (
                select(LabOrder.id)
                .join(PatientReference, PatientReference.id == LabOrder.patient_reference_id)
                .join(LabSelection, LabSelection.lab_order_id == LabOrder.id)
                .where(
                    PatientReference.patient_id == Patient.id,
                    LabSelection.lab_organization_id == organization_id,
                )
                .exists()
            )
            rows = session.scalars(
                select(Patient)
                .where(
                    Patient.display_name.ilike(pattern),
                    or_(
                        Patient.provider_organization_id == organization_id,
                        performed_here,
                    ),
                )
                .order_by(Patient.display_name)
                .limit(limit)
            ).all()
            return [
                BillingPatientRecord(
                    id=patient.id,
                    display_name=patient.display_name,
                    date_of_birth=patient.date_of_birth,
                )
                for patient in rows
            ]

    def list_billable_orders(
        self, organization_id: uuid.UUID, *, limit: int = 50
    ) -> list[BillableOrderRecord]:
        """The laboratory's own entry point into billing: the orders it has actually taken.

        A laboratory clerk cannot start from a patient the way a clinic clerk does. A laboratory
        holds no patient directory of its own (**D-6** is open), and the provider's directory is
        behind `require_clinician`, which a clerk rightly does not hold. So the lab's route into
        billing is the order -- *Ordres → this order → Facturer* -- and this is the read that makes
        it possible without handing a clerk the clinical order surface.

        Deliberately minimal (§32): the order, the patient's display identity, and the names of the
        tests. No status history, no specimen, no result value, no clinician note, no reference
        interval. The patient identity shown here is the same projection a lab technician already
        sees on the order, so nothing is disclosed to the clerk that the laboratory did not already
        hold.

        Scoped by `LabSelection` and the same `BILLABLE_ORDER_STATUSES` that
        `_require_performed_here` applies,
        so the list a clerk sees and the lines the server will accept can never disagree."""
        with self._sessions() as session:
            rows = session.execute(
                select(LabOrder, Patient)
                .join(LabSelection, LabSelection.lab_order_id == LabOrder.id)
                .join(PatientReference, PatientReference.id == LabOrder.patient_reference_id)
                .join(Patient, Patient.id == PatientReference.patient_id)
                .where(
                    LabSelection.lab_organization_id == organization_id,
                    LabOrder.status.in_(sorted(BILLABLE_ORDER_STATUSES)),
                )
                .order_by(LabOrder.created_at.desc())
                .limit(limit)
            ).all()

            invoiced = set(
                session.scalars(
                    select(InvoiceLine.lab_order_item_id)
                    .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                    .where(
                        Invoice.organization_id == organization_id,
                        InvoiceLine.lab_order_item_id.is_not(None),
                    )
                ).all()
            )

            records: list[BillableOrderRecord] = []
            for order, patient in rows:
                items = session.scalars(
                    select(LabOrderItem)
                    .where(LabOrderItem.lab_order_id == order.id)
                    .order_by(LabOrderItem.catalog_display_name_snapshot)
                ).all()
                sources: list[BillableSourceRecord] = []
                for item in items:
                    try:
                        amount, currency = self._lab_item_price(session, organization_id, item)
                    except ServicePriceMissing:
                        amount, currency = None, None
                    sources.append(
                        BillableSourceRecord(
                            source_type=BillableSourceType.LAB_ORDER_ITEM.value,
                            source_id=item.id,
                            description=item.catalog_display_name_snapshot,
                            amount=amount,
                            currency=currency,
                            already_invoiced=item.id in invoiced,
                        )
                    )
                records.append(
                    BillableOrderRecord(
                        lab_order_id=order.id,
                        patient_id=patient.id,
                        patient_display_name=patient.display_name,
                        status=order.status,
                        created_at=order.created_at,
                        items=tuple(sources),
                    )
                )
            return records

    # -- payments --------------------------------------------------------------------------------

    def record_payment(
        self,
        *,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        amount: Decimal,
        method: PaymentMethod,
        reference: str | None,
        idempotency_key: str,
    ) -> RecordPaymentResult:
        """Record that money was received. Append-only, and never more than is owed.

        The invoice row is locked `FOR UPDATE` and the paid total summed **inside** that lock. This
        is the one place in the module where the lock is load-bearing rather than tidy: two cashiers
        taking the last 3 500 DJF simultaneously both read the same remaining balance without it,
        and both succeed. Qualified against real PostgreSQL, because `with_for_update` is a no-op on
        SQLite."""
        if amount <= 0:
            raise ValueError("a payment must be positive")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")

        with self._sessions.begin() as session:
            invoice = session.scalar(
                select(Invoice).where(Invoice.id == invoice_id).with_for_update()
            )
            if invoice is None or invoice.organization_id != organization_id:
                raise BillingNotFound("invoice not found")

            existing = session.scalar(
                select(Payment).where(
                    Payment.organization_id == organization_id,
                    Payment.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.invoice_id != invoice_id or existing.amount != _money(amount):
                    raise BillingIdempotencyConflict(
                        f"idempotency_key {idempotency_key!r} already recorded a different payment "
                        f"-- refusing to return mismatched data"
                    )
                patient = session.get(Patient, invoice.patient_id)
                return RecordPaymentResult(
                    payment=_to_payment_record(existing, invoice.number),
                    invoice=self._to_invoice_record(session, invoice, patient),
                    created=False,
                )

            total = self._invoice_total(session, invoice.id)
            paid = self._invoice_paid(session, invoice.id)
            requested = _money(amount)
            if paid + requested > total:
                raise OverpaymentRefused(
                    f"this payment would exceed the invoice total: {paid} already paid of {total}, "
                    f"and {requested} was offered"
                )

            highest = session.scalar(
                select(func.max(Payment.sequence_number)).where(Payment.invoice_id == invoice.id)
            )
            payment = Payment(
                invoice_id=invoice.id,
                organization_id=organization_id,
                sequence_number=(highest or 0) + 1,
                amount=requested,
                method=method.value,
                reference=(reference or None),
                idempotency_key=idempotency_key,
                recorded_by_identity_id=principal.identity_id,
            )
            session.add(payment)
            session.flush()
            self._record(
                session,
                event_type="payment_recorded",
                principal=principal,
                organization_id=organization_id,
                resource_type="invoice",
                resource_id=invoice.id,
                outcome="created",
            )
            patient = session.get(Patient, invoice.patient_id)
            return RecordPaymentResult(
                payment=_to_payment_record(payment, invoice.number),
                invoice=self._to_invoice_record(session, invoice, patient),
                created=True,
            )

    # -- internals -------------------------------------------------------------------------------

    def _service_in_session(
        self, session: Session, organization_id: uuid.UUID, service_id: uuid.UUID
    ) -> ClinicService:
        service = session.get(ClinicService, service_id)
        if service is None or service.organization_id != organization_id:
            raise BillingNotFound("service not found")
        return service

    def _patient_for_billing(
        self, session: Session, organization_id: uuid.UUID, patient_id: uuid.UUID
    ) -> Patient:
        """Prove this organization may bill this person at all.

        A provider bills the patients in its own directory. A laboratory has no directory of its own
        (**D-6** is open), so it bills a patient it has actually done work for -- proven by an order
        assigned to it that references that patient. Either way the *lines* are checked separately
        and more strictly; this only establishes that the person is not a stranger."""
        patient = session.get(Patient, patient_id)
        if patient is None:
            raise BillingPatientNotFound("patient not found")
        if patient.provider_organization_id == organization_id:
            return patient

        performed = session.scalar(
            select(func.count())
            .select_from(LabOrder)
            .join(PatientReference, PatientReference.id == LabOrder.patient_reference_id)
            .join(LabSelection, LabSelection.lab_order_id == LabOrder.id)
            .where(
                PatientReference.patient_id == patient_id,
                LabSelection.lab_organization_id == organization_id,
            )
        )
        if not performed:
            raise BillingPatientNotFound("patient not found")
        return patient

    def _resolve_source(
        self, session: Session, organization_id: uuid.UUID, request: LineRequest
    ) -> tuple[str, Decimal, str]:
        """Prove the source is this organization's own work, then freeze its description and price.

        Returns `(description, unit_amount, currency)` -- the three facts the line keeps forever."""
        if request.source_type is BillableSourceType.CLINIC_SERVICE:
            service = session.get(ClinicService, request.source_id)
            if service is None:
                raise BillingNotFound("service not found")
            if service.organization_id != organization_id:
                # A real service, belonging to somebody else. ADR-066: not our work to charge for.
                raise ServiceNotPerformedHere(
                    "this service belongs to another organization and cannot be invoiced here"
                )
            price = session.scalar(
                select(ClinicServicePrice).where(
                    ClinicServicePrice.clinic_service_id == service.id,
                    ClinicServicePrice.superseded_at.is_(None),
                )
            )
            if price is None:
                raise ServicePriceMissing(f"no current price is configured for {service.name!r}")
            return service.name, price.amount, price.currency

        item = session.get(LabOrderItem, request.source_id)
        if item is None:
            raise BillingNotFound("order item not found")
        self._require_performed_here(session, organization_id, item)
        price = self._lab_item_price(session, organization_id, item)
        return item.catalog_display_name_snapshot, price[0], price[1]

    def _require_performed_here(
        self, session: Session, organization_id: uuid.UUID, item: LabOrderItem
    ) -> None:
        """ADR-066's rule, at the only point where it can be enforced truthfully.

        The laboratory that the order was **assigned to** is the one that performed the test, so it
        is the one that may charge for it. The *ordering provider* may not -- that is exactly the
        Clinic A / Lab B case D-2a leaves open, and billing it here would silently answer that
        question in one of its three possible ways.

        Deliberately checked against `LabSelection` rather than against the order's provider:
        knowing an order item's id proves nothing, and a caller who can read an order id is not
        thereby entitled to invoice for it.

        **Routing is not performance, and the status check is the difference.** A `LabSelection` row
        exists from the moment a provider *assigns* an order -- before the laboratory has accepted
        it, and while it may still reject it. Billing then would let a laboratory charge for a test
        it never ran and afterwards declined: a real bad outcome, not a theoretical one. So the
        order must also have reached `ACCEPTED` or beyond, meaning the laboratory took the work.
        `SELECTED` is excluded because it means only "somebody was asked"; `REJECTED` and
        `CANCELLED` because the work will not happen."""
        row = session.execute(
            select(LabSelection.lab_organization_id, LabOrder.status)
            .join(LabOrder, LabOrder.id == LabSelection.lab_order_id)
            .where(LabSelection.lab_order_id == item.lab_order_id)
        ).first()
        if row is None:
            raise ServiceNotPerformedHere(
                "this order has not been routed to a laboratory, so no organization has "
                "performed it"
            )
        performing, order_status = row
        if performing != organization_id:
            raise ServiceNotPerformedHere(
                "this test was performed by another organization and cannot be invoiced here"
            )
        if order_status not in BILLABLE_ORDER_STATUSES:
            raise ServiceNotPerformedHere(
                f"this order is {order_status}; a laboratory may invoice a test only once it has "
                f"accepted the work"
            )

    def _lab_item_price(
        self, session: Session, organization_id: uuid.UUID, item: LabOrderItem
    ) -> tuple[Decimal, str]:
        """The performing laboratory's own current list price for this test.

        Reads `LabPrice` through the lab's `LabOffering`, which `docs/NATIVE-OPERATING-PRODUCT.md`
        §18 explicitly endorses as a billing source: it already supersedes rather than mutates, so
        it preserves history. What is **not** read is `LabOrderItem.selected_price_amount` -- that
        is DH-EX-005's *marketplace price-consent* fact, written only by the patient-selection path,
        and §18 is explicit that conflating it with a billing price would make a consent record look
        like an invoice. `assign_by_provider` leaves it NULL, meaning "price was not part of this
        transaction", never "costs zero"."""
        from daher_adapter.exchange.models import LabOffering, LabPrice

        row = session.execute(
            select(LabPrice.amount, LabPrice.currency)
            .join(LabOffering, LabOffering.id == LabPrice.lab_offering_id)
            .where(
                LabOffering.lab_organization_id == organization_id,
                LabOffering.catalog_entry_id == item.catalog_entry_id,
                LabPrice.superseded_at.is_(None),
            )
        ).first()
        if row is None:
            raise ServicePriceMissing(
                f"no current price is configured for {item.catalog_display_name_snapshot!r}"
            )
        return row[0], row[1]

    def _next_invoice_number(self, session: Session, organization_id: uuid.UUID) -> str:
        """`FAC-2026-000001`, allocated under a row lock.

        `count(*) + 1` is the classic double-issue bug: two cashiers invoicing at the same instant
        both count *n* and both write *n+1*. The counter row is taken `FOR UPDATE`, so the second
        caller waits and reads the first's committed value. The unique constraint on
        `(organization_id, number)` is the second layer behind this, not the first.

        **Not a legal numbering scheme.** Gapless fiscal sequences, registration marks and mandated
        prefixes are a jurisdictional policy question nobody has answered for Djibouti here; ADR-066
        records that rather than inventing one."""
        year = utc_now().year
        sequence = session.get(InvoiceNumberSequence, (organization_id, year), with_for_update=True)
        if sequence is None:
            candidate = InvoiceNumberSequence(
                organization_id=organization_id, year=year, next_value=1
            )
            # A SAVEPOINT, **never** `session.rollback()`. This runs inside the caller's invoice
            # transaction, which by this point may already hold a flushed `Invoice` and its lines;
            # a bare rollback would discard all of it and leave the caller building on a dead
            # session. The first version of this method did exactly that. Same pattern as
            # `visits.start()` and `order_intents._create_in_session`.
            try:
                with session.begin_nested():
                    session.add(candidate)
                    session.flush()
                sequence = candidate
            except IntegrityError:
                # Another transaction created this year's counter between the read and the insert.
                # Re-read it under the lock rather than guessing at its value.
                #
                # `if candidate in session` is load-bearing, not defensive. Rolling a SAVEPOINT back
                # *already* detaches whatever was added inside it, so an unconditional
                # `session.expunge(candidate)` raises `InvalidRequestError: Instance ... is not
                # present in this Session` -- and does so exactly when this branch is reached, which
                # is exactly when two facilities issue their first invoice of the year at the same
                # instant. SQLite never reaches it (no `FOR UPDATE`, and its writer serialization
                # hides the race), so the unit suite was green while the real backend was not. Found
                # by `tests/integration/test_dhealthbilling001_postgres_qualification.py`.
                if candidate in session:
                    session.expunge(candidate)
                sequence = session.get(
                    InvoiceNumberSequence, (organization_id, year), with_for_update=True
                )
                if sequence is None:  # pragma: no cover - the row provably exists by now
                    raise
        value = sequence.next_value
        sequence.next_value = value + 1
        session.flush()
        return f"FAC-{year}-{value:06d}"

    def _invoice_total(self, session: Session, invoice_id: uuid.UUID) -> Decimal:
        rows = session.execute(
            select(InvoiceLine.quantity, InvoiceLine.unit_amount).where(
                InvoiceLine.invoice_id == invoice_id
            )
        ).all()
        return _money(sum((Decimal(q) * a for q, a in rows), Decimal("0")))

    def _invoice_paid(self, session: Session, invoice_id: uuid.UUID) -> Decimal:
        total = session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.invoice_id == invoice_id
            )
        )
        return _money(Decimal(total or 0))

    def _to_invoice_record(
        self, session: Session, invoice: Invoice, patient: Patient | None
    ) -> InvoiceRecord:
        lines = session.scalars(
            select(InvoiceLine)
            .where(InvoiceLine.invoice_id == invoice.id)
            .order_by(InvoiceLine.created_at, InvoiceLine.id)
        ).all()
        payments = session.scalars(
            select(Payment)
            .where(Payment.invoice_id == invoice.id)
            .order_by(Payment.sequence_number)
        ).all()
        total = _money(sum((Decimal(x.quantity) * x.unit_amount for x in lines), Decimal("0")))
        paid = _money(sum((x.amount for x in payments), Decimal("0")))
        return InvoiceRecord(
            id=invoice.id,
            organization_id=invoice.organization_id,
            patient_id=invoice.patient_id,
            patient_display_name=patient.display_name if patient is not None else None,
            number=invoice.number,
            currency=invoice.currency,
            issued_at=invoice.issued_at,
            created_by_identity_id=invoice.created_by_identity_id,
            lines=tuple(
                InvoiceLineRecord(
                    id=line.id,
                    source_type=line.source_type,
                    clinic_service_id=line.clinic_service_id,
                    lab_order_item_id=line.lab_order_item_id,
                    description=line.description_snapshot,
                    quantity=line.quantity,
                    unit_amount=line.unit_amount,
                    currency=line.currency,
                    line_total=_money(Decimal(line.quantity) * line.unit_amount),
                )
                for line in lines
            ),
            payments=tuple(_to_payment_record(p, invoice.number) for p in payments),
            total=total,
            paid=paid,
            balance=_money(total - paid),
            status=_status_for(total, paid),
        )

    def _record(
        self,
        session: Session,
        *,
        event_type: SecurityEventType,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        resource_type: str,
        resource_id: uuid.UUID,
        outcome: SecurityOutcome,
    ) -> None:
        self._audit.record(
            session,
            event_type=event_type,
            actor_identity_id=principal.identity_id,
            organization_id=organization_id,
            resource_type=resource_type,  # type: ignore[arg-type]
            resource_id=resource_id,
            outcome=outcome,
        )


def _to_service_record(
    service: ClinicService, price: ClinicServicePrice | None
) -> ClinicServiceRecord:
    return ClinicServiceRecord(
        id=service.id,
        organization_id=service.organization_id,
        name=service.name,
        active=service.active,
        amount=price.amount if price is not None else None,
        currency=price.currency if price is not None else None,
        created_at=service.created_at,
    )


def _to_payment_record(payment: Payment, invoice_number: str) -> PaymentRecord:
    return PaymentRecord(
        id=payment.id,
        invoice_id=payment.invoice_id,
        sequence_number=payment.sequence_number,
        amount=payment.amount,
        method=payment.method,
        reference=payment.reference,
        recorded_by_identity_id=payment.recorded_by_identity_id,
        recorded_at=payment.recorded_at,
        receipt_reference=f"{invoice_number}-R{payment.sequence_number}",
    )
