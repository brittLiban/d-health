"""DH-CONNECT-AGENT-001 §31–§35 — applying what a connector sent, after it is durably held.

``runtime_api.submit`` takes responsibility for a message and returns. This module is what then
*does* something with it. The separation is the whole point of a durable queue:

    submit   : the bytes are safe. DHealth will apply this, and will say so again if asked.
    apply    : the clinical mutation happens, exactly once, in its own transaction.

Collapsing the two -- applying inside the request -- would mean a timeout after the mutation
committed left the agent believing it must retry work already done, and the only protection would be
whatever the agent's own bookkeeping happened to be. Splitting them moves the guarantee to DHealth's
side of the wire, where a row and a receipt commit together.

**One transaction per delivery, deliberately.** A batch in one transaction would make one bad
message roll back the good ones beside it, and the retry would hit the same bad message again -- a
queue that stops at its first poison pill. Per-delivery commits mean a message that cannot be
applied dead-letters alone and the rest of the backlog drains past it.

**The applier is DHealth, not an agent.** It claims with a reserved runtime instance id, because
``claim`` is written in terms of lease holders and DHealth is one: if the process applying messages
dies mid-batch, its claims expire and the work is retried, which is exactly the behaviour the agent
side gets for free. The reserved id is not a hostname -- see ``EXCHANGE_APPLIER_INSTANCE_ID``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.apply import ConnectApplyError, ConnectBridge
from daher_adapter.connect.contract import (
    BusinessAcceptance,
    Direction,
    Envelope,
    FailureCategory,
    OrderSubmission,
    ReleasedAmendment,
    ReleasedResult,
)
from daher_adapter.connect.correlation import (
    CorrelationConflict,
    CorrelationRepository,
    CorrelationUnknown,
)
from daher_adapter.connect.delivery import (
    DeliveryConflict,
    DeliveryRepository,
    DurableReceiptStore,
)
from daher_adapter.connect.runtime_models import ConnectorDelivery
from daher_adapter.models import utc_now

#: The lease holder DHealth's own applier claims as. A fixed string rather than a hostname or a pid:
#: the point of the id is that an interrupted applier's claims expire and are retried, and a
#: per-process id would leave a dead process's rows attributed to something nothing will ever
#: reclaim by name. Reserved -- an agent choosing this id for itself would be able to acknowledge
#: DHealth's own inbound work, so ``runtime_api`` refuses it.
EXCHANGE_APPLIER_INSTANCE_ID = "dhealth-exchange-applier"


@dataclass(frozen=True, slots=True)
class AppliedDelivery:
    """What happened to one inbound delivery."""

    delivery_id: uuid.UUID
    message_type: str
    state: str
    #: ``True`` when the bridge performed a mutation. A replay is a success that changed nothing.
    mutated: bool
    replayed: bool
    failure_category: FailureCategory | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ApplyReport:
    """The outcome of one drain pass. Counts and categories only -- no payloads, no patient data."""

    results: tuple[AppliedDelivery, ...]

    @property
    def applied(self) -> int:
        return sum(1 for result in self.results if result.mutated)

    @property
    def replayed(self) -> int:
        return sum(1 for result in self.results if result.replayed)

    @property
    def dead_lettered(self) -> int:
        return sum(1 for result in self.results if result.state == "DEAD_LETTER")

    @property
    def retrying(self) -> int:
        return sum(
            1
            for result in self.results
            if result.state == "PENDING" and result.failure_category is not None
        )


#: Builds the ``ConnectBridge`` for one session. Injected rather than constructed here so this
#: module never imports the Exchange repositories -- ``apply.py``'s own discipline, for the same
#: reason: a file that can reach the domain directly is a file where a domain rule will eventually
#: be rewritten.
BridgeFactory = Callable[[Session, uuid.UUID, uuid.UUID], ConnectBridge]


def build_default_bridge_factory(session_factory: sessionmaker[Session]) -> BridgeFactory:
    """The production bridge factory, wired to the Exchange's own repositories.

    Imported lazily inside the function. ``exchange.facility_enrollment`` already imports
    ``connect.qualification``, so a module-level import of the Exchange repositories here would
    close a cycle -- and the modules this reaches for are heavy enough that a lazy import is worth
    having anyway.

    The repositories are constructed exactly as ``exchange/api.py`` constructs them, with the real
    session factory, and the bridge then calls every one of them with ``session=`` -- the
    ``*_in_session`` form. That is what keeps the whole application of one message in a single
    transaction: a repository falling back to its own factory would open a second one inside it, and
    the receipt would then be able to commit without the mutation.

    Only the ``ReceiptStore`` differs from the harness's wiring, and it is the difference that
    matters: ``DurableReceiptStore`` bound to *this* session, not ``InMemoryReceiptStore``.
    """

    def factory(
        session: Session, organization_id: uuid.UUID, identity_id: uuid.UUID
    ) -> ConnectBridge:
        from daher_adapter.exchange.catalog import CatalogRepository
        from daher_adapter.exchange.identity import (
            PatientReferenceRepository,
            PractitionerReferenceRepository,
        )
        from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
        from daher_adapter.exchange.orders import LabOrderRepository
        from daher_adapter.exchange.organizations import OrganizationRepository
        from daher_adapter.exchange.results import (
            LabResultAmendmentRepository,
            LabResultReleaseRepository,
            LabResultRepository,
        )

        organizations = OrganizationRepository(session_factory)
        catalog = CatalogRepository(session_factory, organizations)
        return ConnectBridge(
            workflow=LabWorkflowRepository(session_factory, organizations),
            results=LabResultRepository(session_factory),
            releases=LabResultReleaseRepository(session_factory),
            amendments=LabResultAmendmentRepository(session_factory),
            receipts=DurableReceiptStore(session),
            installation_organization_id=organization_id,
            service_identity_id=identity_id,
            # The provider side. Wired unconditionally rather than per installation kind: what a
            # connector may do is decided by its declared capabilities and its qualification, not by
            # which repositories happened to be handed to the bridge. A laboratory installation that
            # submits an order is refused by the contract, not by a missing object.
            orders=LabOrderRepository(session_factory, organizations, catalog),
            patients=PatientReferenceRepository(session_factory, organizations),
            practitioners=PractitionerReferenceRepository(session_factory),
        )

    return factory


def apply_pending_inbound(
    session_factory: sessionmaker[Session],
    *,
    installation_id: uuid.UUID,
    organization_id: uuid.UUID,
    identity_id: uuid.UUID,
    bridge_factory: BridgeFactory,
    deliveries: DeliveryRepository | None = None,
    limit: int = 10,
    now: Callable[[], datetime] = utc_now,
) -> ApplyReport:
    """Claim and apply this installation's pending inbound deliveries.

    Claims first, in its own transaction, then applies each claimed row in a transaction of its own.
    Two phases rather than one because the claim is what stops a second applier from picking up the
    same work, and holding it open for the whole batch would mean the lock lived as long as the
    slowest clinical mutation.

    Failure handling maps ``ConnectApplyError``'s category straight through: the bridge already
    decided whether retrying could help, and second-guessing it here would give the same question
    two answers. A ``DeliveryConflict`` from the receipt store is ``CONFLICT`` -- terminal, because
    the partner has contradicted themselves and no number of retries makes two different payloads
    under one event id agree.

    An unexpected exception is ``TEMPORARY``. That is the fail-*safe* direction rather than the
    fail-closed one, and the choice is deliberate: an unrecognised error is most often
    infrastructure, and dead-lettering real clinical work because DHealth raised something nobody
    anticipated would discard a message that a retry would have delivered. The retry budget bounds
    it -- five attempts and it dead-letters anyway, so an unknown *permanent* error still ends up in
    front of an operator.
    """
    repository = deliveries or DeliveryRepository()
    moment = now()

    with session_factory() as session:
        claimed = repository.claim(
            session,
            installation_id=installation_id,
            direction=Direction.CONNECTOR_TO_DHEALTH,
            runtime_instance_id=EXCHANGE_APPLIER_INSTANCE_ID,
            limit=limit,
            now=moment,
        )
        claimed_ids = [row.id for row in claimed]
        session.commit()

    results: list[AppliedDelivery] = []
    for delivery_id in claimed_ids:
        results.append(
            _apply_one(
                session_factory,
                delivery_id=delivery_id,
                organization_id=organization_id,
                identity_id=identity_id,
                bridge_factory=bridge_factory,
                repository=repository,
                now=now,
            )
        )
    return ApplyReport(results=tuple(results))


def _apply_one(
    session_factory: sessionmaker[Session],
    *,
    delivery_id: uuid.UUID,
    organization_id: uuid.UUID,
    identity_id: uuid.UUID,
    bridge_factory: BridgeFactory,
    repository: DeliveryRepository,
    now: Callable[[], datetime],
) -> AppliedDelivery:
    """One delivery, one transaction. The unit that must not be widened.

    The receipt and the clinical mutation are written by the same session and committed together, so
    there is no window in which DHealth has applied a message it does not remember applying. That
    window is precisely the bug ``DurableReceiptStore`` exists to close, and a wider transaction
    boundary would reopen it while looking more efficient.
    """
    moment = now()
    with session_factory() as session:
        delivery = session.get(ConnectorDelivery, delivery_id)
        if delivery is None:  # pragma: no cover - the claim just wrote this row
            raise RuntimeError(f"claimed delivery {delivery_id} vanished")

        try:
            envelope = Envelope.model_validate_json(delivery.payload)
        except ValueError as error:
            # A stored payload that no longer parses is terminal: the bytes are what they are, and
            # retrying will parse the same bytes again. Reachable in practice only across a contract
            # change that narrowed a field, which is exactly when an operator needs to see it.
            return _fail(
                session,
                repository,
                delivery,
                FailureCategory.VALIDATION,
                f"stored envelope no longer parses: {type(error).__name__}",
                moment,
            )

        bridge = bridge_factory(session, organization_id, identity_id)
        try:
            # Correlation is recorded **before** the clinical mutation, in the same transaction. A
            # partner identifier that contradicts one already bound must refuse before anything is
            # written -- binding afterwards would mean the result had already landed by the time
            # DHealth noticed it was claimed for a different order.
            _bind_correlations(session, delivery.connector_installation_id, envelope)
            outcome = bridge.apply(session, envelope)
            # ORDER_SUBMISSION is the exception to "correlation first": the DHealth order does not
            # exist until the bridge creates it, so there is nothing to bind *to* beforehand. Bound
            # here, in the same transaction, because a submitted order with no correlation is an
            # order whose results can never be filed back into the EMR that asked for them.
            if isinstance(envelope.payload, OrderSubmission) and outcome.lab_order_id is not None:
                CorrelationRepository().bind_order(
                    session,
                    installation_id=delivery.connector_installation_id,
                    external_order_id=envelope.payload.external_order_id,
                    lab_order_id=outcome.lab_order_id,
                    source_system=envelope.source_system,
                )
        except CorrelationConflict as error:
            conflict = FailureCategory.CONFLICT
            return _fail(session, repository, delivery, conflict, str(error), moment)
        except CorrelationUnknown as error:
            # The amendment case: it corrects a report DHealth has never been told about. Terminal
            # rather than retryable -- the missing original will not appear because this message is
            # retried, and §56 prefers an unresolved row an operator can see.
            conflict = FailureCategory.CONFLICT
            return _fail(session, repository, delivery, conflict, str(error), moment)
        except ConnectApplyError as error:
            return _fail(session, repository, delivery, error.category, str(error), moment)
        except DeliveryConflict as error:
            category = FailureCategory.CONFLICT
            return _fail(session, repository, delivery, category, str(error), moment)
        except Exception as error:  # noqa: BLE001 -- see the docstring on why this is TEMPORARY
            return _fail(
                session,
                repository,
                delivery,
                FailureCategory.TEMPORARY,
                f"unexpected error applying message: {type(error).__name__}",
                moment,
            )

        updated = repository.mark_delivered(
            session,
            delivery=delivery,
            runtime_instance_id=EXCHANGE_APPLIER_INSTANCE_ID,
            now=moment,
        )
        record = AppliedDelivery(
            delivery_id=updated.id,
            message_type=updated.message_type,
            state=updated.state,
            mutated=outcome.applied,
            replayed=outcome.replayed,
            detail=outcome.detail,
        )
        session.commit()
    return record


def _bind_correlations(session: Session, installation_id: uuid.UUID, envelope: Envelope) -> None:
    """Record what the partner calls this work, exactly, before anything clinical happens.

    §43's "no fuzzy matching" is enforced by there being nothing to match: an identifier either has
    a row here or it does not. The three messages that carry partner identifiers each bind what they
    assert, and the amendment additionally *checks* -- an amendment that claims to correct a report
    belonging to a different order is refused rather than applied across the boundary, which is the
    one correlation mistake that moves a clinical correction onto the wrong patient's chart.

    Idempotent throughout, because every one of these messages is delivered at-least-once.
    """
    payload = envelope.payload
    lab_order_id = envelope.lab_order_id
    if lab_order_id is None:  # pragma: no cover - the envelope validator requires it here
        return
    correlations = CorrelationRepository()

    if isinstance(payload, BusinessAcceptance):
        # The accession the laboratory assigned. `external_accession_id` when they gave one, else
        # their echo of the order id -- both are "what they call this order", and binding whichever
        # they sent is what lets a later result correlate.
        correlations.bind_order(
            session,
            installation_id=installation_id,
            external_order_id=payload.external_accession_id or payload.external_order_id,
            lab_order_id=lab_order_id,
            source_system=envelope.source_system,
        )
    elif isinstance(payload, ReleasedAmendment):
        # Checked first: an amendment naming a predecessor bound to another order is a cross-order
        # correction, and no amount of retrying makes it correct.
        predecessor = correlations.resolve_report(
            session,
            installation_id=installation_id,
            external_report_id=payload.corrects_external_report_id,
        )
        if predecessor != lab_order_id:
            raise CorrelationConflict(
                "this amendment corrects a report belonging to a different DHealth order"
            )
        correlations.bind_report(
            session,
            installation_id=installation_id,
            external_report_id=payload.external_report_id,
            lab_order_id=lab_order_id,
            source_system=envelope.source_system,
        )
    elif isinstance(payload, ReleasedResult):
        correlations.bind_report(
            session,
            installation_id=installation_id,
            external_report_id=payload.external_report_id,
            lab_order_id=lab_order_id,
            source_system=envelope.source_system,
        )


def _fail(
    session: Session,
    repository: DeliveryRepository,
    delivery: ConnectorDelivery,
    category: FailureCategory,
    detail: str,
    moment: datetime,
) -> AppliedDelivery:
    """Record the failure on a clean session and commit it.

    ``rollback`` first: the bridge may have left a partial mutation and a broken session behind, and
    recording the failure into that session would fail too -- losing the only evidence that anything
    went wrong. The delivery row is re-fetched because the rollback expired it.
    """
    delivery_id = delivery.id
    session.rollback()
    row = session.get(ConnectorDelivery, delivery_id)
    if row is None:  # pragma: no cover - the row exists; the rollback only expired the instance
        raise RuntimeError(f"delivery {delivery_id} vanished during failure handling")
    updated = repository.mark_failed(
        session,
        delivery=row,
        runtime_instance_id=EXCHANGE_APPLIER_INSTANCE_ID,
        category=category,
        detail=detail,
        now=moment,
    )
    record = AppliedDelivery(
        delivery_id=updated.id,
        message_type=updated.message_type,
        state=updated.state,
        mutated=False,
        replayed=False,
        failure_category=category,
        detail=detail,
    )
    session.commit()
    return record
