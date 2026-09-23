"""DH-CONNECT-AGENT-001 (provider side) — proving the three provider capabilities by using them.

Before this existed, ``ORDER_SUBMISSION``, ``RELEASED_RESULT_RECEIPT`` and ``AMENDMENT_RECEIPT``
were permanently ``DECLARED``: nothing in the codebase could exercise them, so a provider connector
could not reach qualification at all no matter how well it worked. That is the same hole
``ORDER_RECEIPT`` was in before the runtime existed, and it is closed the same way.

§41's rule governs here exactly as it does on the laboratory side:

    Invalid qualification: call the adapter directly and mark the capability exercised. That
    bypasses the runtime this ticket is supposed to prove.

So nothing here calls an adapter. Each drive puts real work into the real path and then checks
**three independent places**, because any one of them alone can be true while the path is broken:

    the partner's system has it        ... but DHealth might never have learned it arrived
    the central row is DELIVERED       ... but the agent might have acknowledged without delivering
    the local row is ACKNOWLEDGED      ... but that is the agent agreeing with itself

**The result and amendment drives deliberately do not enqueue anything themselves.** They run the
laboratory's real release and let ``connect.result_hook`` decide whether to queue a notification --
because resolving the provider's own ``external_order_id`` through the correlation is precisely the
thing under proof. A drive that hand-built the envelope would skip the step most likely to be wrong
and would still pass.

**Committed phases, injected runner** -- the same two constraints ``runtime_qualification`` records.
The harness runs its scenarios inside one long transaction; the agent commits through its own
sessions against the same database, which on SQLite deadlocks and on PostgreSQL simply would not be
visible. So every drive here runs to completion first and hands a finished result to the harness.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import Capability
from daher_adapter.connect.correlation import (
    CorrelationNamespace,
    CorrelationRepository,
    CorrelationUnknown,
)
from daher_adapter.connect.models import EvidenceLevel
from daher_adapter.connect.result_hook import result_notification_event_id
from daher_adapter.connect.runtime_models import ConnectorDelivery, DeliveryState

#: Shared by every scenario below. The point of saying it on each recorded capability rather than
#: once in a docstring: §82 and §86 both turn on nobody reading a synthetic pass as a real one, and
#: a limitation that lives only in source is a limitation the evidence does not carry.
SYNTHETIC_LIMITATION = (
    "exercée contre l'adaptateur synthétique : aucun système partenaire réel n'a participé"
)


@dataclass(frozen=True, slots=True)
class SubmissionEvidence:
    """What the submission drive observed.

    ``lab_order_id`` is the drive's real output as well as its evidence: the two later drives are
    about *that* order, and if the correlation did not bind there is nothing for them to be about.
    """

    reached_dhealth: bool
    central_state: str | None
    lab_order_id: uuid.UUID | None
    correlation_bound: bool
    detail: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.reached_dhealth
            and self.central_state == DeliveryState.DELIVERED.value
            and self.lab_order_id is not None
            and self.correlation_bound
        )


@dataclass(frozen=True, slots=True)
class NotificationEvidence:
    """What a result or amendment drive observed, in the three places that have to agree.

    ``adapter_calls`` is a count rather than a boolean on purpose. The synthetic adapter is
    idempotent, so "the report is filed" stays true after a redelivery that should never have
    happened -- exactly the gap NC-RUNTIME-4 found on the laboratory side, where a passing test
    survived deleting the agent's replay check. Exactly one call is the claim.
    """

    filed_by_partner: bool
    adapter_calls: int
    central_state: str | None
    locally_acknowledged: bool
    detail: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.filed_by_partner
            and self.adapter_calls == 1
            and self.central_state == DeliveryState.DELIVERED.value
            and self.locally_acknowledged
        )


#: Answers "has the partner's system filed this report?" and "how many times was it handed one?"
#: without this module knowing what a partner system is. The caller closes over its adapter, so
#: §3's protocol-neutrality survives into the qualification suite.
PartnerHasReport = Callable[[str, int], bool]
PartnerCallCount = Callable[[], int]
#: Answers "is this delivery acknowledged in the agent's own store?". Same reasoning.
LocallyAcknowledged = Callable[[str], bool]


def drive_order_submission(
    sessions: sessionmaker[Session],
    *,
    installation_id: uuid.UUID,
    namespace: CorrelationNamespace,
    external_order_id: str,
    emit_submission: Callable[[], None],
    run_cycle: Callable[[], object],
    cycles: int = 2,
) -> SubmissionEvidence:
    """Have the partner's system emit an order, run the agent, and see whether DHealth has it.

    ``emit_submission`` queues the event on the adapter -- the caller's business, because only the
    caller knows what its adapter is. Everything after that is the real path: the agent polls, the
    agent submits, DHealth's applier creates the order and binds the correlation in one transaction.

    ``cycles`` defaults to two for the reason the laboratory drive gives: a lost acknowledgement is
    paid at the *start* of the next cycle, so one cycle would report a spurious failure for a path
    recovering exactly as designed.
    """
    emit_submission()
    for _ in range(cycles):
        run_cycle()

    with sessions() as session:
        row = session.scalar(
            select(ConnectorDelivery).where(
                ConnectorDelivery.connector_installation_id == installation_id,
                ConnectorDelivery.message_type == "ORDER_SUBMISSION",
            )
        )
        central_state = None if row is None else row.state
        failure_detail = None if row is None else row.last_failure_detail
        try:
            lab_order_id = CorrelationRepository().resolve_order(
                session, namespace=namespace, external_order_id=external_order_id
            )
        except CorrelationUnknown:
            # Not an error here. "The submission never bound a correlation" is one of the outcomes
            # this drive exists to detect, and it is reported as a failed scenario rather than
            # raised out of the qualification suite.
            lab_order_id = None

    detail: str | None = None
    if row is None:
        detail = "aucune soumission n'est parvenue à DHealth par le chemin de livraison durable"
    elif central_state != DeliveryState.DELIVERED.value:
        detail = failure_detail or f"soumission non appliquée (état {central_state})"
    elif lab_order_id is None:
        detail = (
            "commande créée sans corrélation : le système du prestataire ne pourrait pas "
            "rattacher le résultat à sa propre commande"
        )

    return SubmissionEvidence(
        reached_dhealth=row is not None,
        central_state=central_state,
        lab_order_id=lab_order_id,
        correlation_bound=lab_order_id is not None,
        detail=detail,
    )


def drive_release_notification(
    sessions: sessionmaker[Session],
    *,
    installation_id: uuid.UUID,
    lab_order_id: uuid.UUID,
    external_order_id: str,
    release_version: int,
    perform_release: Callable[[], None],
    run_cycle: Callable[[], object],
    partner_has_report: PartnerHasReport,
    partner_call_count: PartnerCallCount,
    locally_acknowledged: LocallyAcknowledged,
    cycles: int = 2,
) -> NotificationEvidence:
    """Run the laboratory's real release, then the agent, and see whether the provider's EMR has it.

    ``perform_release`` is the laboratory workflow, committed, done by the caller: DHealth's own
    release repository, with a real releaser identity, exactly as a laboratory technician's release
    reaches it. **Nothing here queues a notification** -- the release hook does, or does not, and
    which of those happened is the substance of the test.

    Serves both the original release and an amendment; ``release_version`` selects which delivery to
    look for and the caller's ``perform_release`` decides which act produced it.
    """
    perform_release()
    for _ in range(cycles):
        run_cycle()

    event_id = result_notification_event_id(lab_order_id, release_version)
    with sessions() as session:
        row = session.scalar(
            select(ConnectorDelivery).where(
                ConnectorDelivery.connector_installation_id == installation_id,
                ConnectorDelivery.source_event_id == event_id,
            )
        )
        central_state = None if row is None else row.state
        failure_detail = None if row is None else row.last_failure_detail

    filed = partner_has_report(external_order_id, release_version)
    calls = partner_call_count()
    acknowledged = locally_acknowledged(event_id)

    detail: str | None = None
    if row is None:
        detail = (
            "aucune notification n'a été mise en file après la validation : le prestataire n'a "
            "jamais été informé"
        )
    elif not filed:
        detail = failure_detail or "le compte rendu n'a pas atteint le système du prestataire"
    elif calls != 1:
        detail = f"le système du prestataire a été sollicité {calls} fois pour un seul compte rendu"
    elif central_state != DeliveryState.DELIVERED.value:
        detail = f"livraison non confirmée à DHealth (état {central_state})"
    elif not acknowledged:
        detail = "l'agent n'a pas enregistré l'accusé de réception localement"

    return NotificationEvidence(
        filed_by_partner=filed,
        adapter_calls=calls,
        central_state=central_state,
        locally_acknowledged=acknowledged,
        detail=detail,
    )


def provider_scenarios(
    *,
    submission: SubmissionEvidence | None,
    released_result: NotificationEvidence | None,
    amendment: NotificationEvidence | None,
):
    """The three scenario results the harness records, from whatever the caller actually drove.

    ``None`` for any of them means the caller had no runtime to drive it with, and the honest answer
    is the declaration-only scenario: ``DECLARED``, failing, saying so. That is not a technicality
    -- it is the whole distinction the evidence model exists for, and it is what keeps a connector
    that has merely *claimed* these capabilities out of production qualification.

    ``EXERCISED`` is recorded whether a drive passed or failed. Evidence level says how a claim was
    tested, not whether the test succeeded: a provider connector that was driven and refused the
    report has been exercised and has failed, and recording that as ``DECLARED`` would throw away
    the fact that DHealth actually tried.
    """
    from daher_adapter.connect.harness import ScenarioResult

    def scenario(
        name: str,
        capability: Capability,
        evidence: SubmissionEvidence | NotificationEvidence | None,
        undriven_detail: str,
    ) -> ScenarioResult:
        if evidence is None:
            return ScenarioResult(
                scenario=name,
                capability=capability,
                evidence_level=EvidenceLevel.DECLARED,
                passed=False,
                detail=undriven_detail,
            )
        return ScenarioResult(
            scenario=name,
            capability=capability,
            evidence_level=EvidenceLevel.EXERCISED,
            passed=evidence.passed,
            detail=evidence.detail,
            limitation=SYNTHETIC_LIMITATION,
        )

    return (
        scenario(
            "PQ1-order-submission",
            Capability.ORDER_SUBMISSION,
            submission,
            "aucun agent n'a soumis de commande : capacité déclarée, non exercée",
        ),
        scenario(
            "PQ2-released-result-receipt",
            Capability.RELEASED_RESULT_RECEIPT,
            released_result,
            "aucun compte rendu validé n'a été livré : capacité déclarée, non exercée",
        ),
        scenario(
            "PQ3-amendment-receipt",
            Capability.AMENDMENT_RECEIPT,
            amendment,
            "aucune correction n'a été livrée : capacité déclarée, non exercée",
        ),
    )


class ProviderQualificationHarness:
    """Turns three finished drives into one recordable qualification run.

    Deliberately thin, and deliberately **not** a mirror of ``LabQualificationHarness``'s internals.
    That class drives its scenarios itself, through a ``ConnectBridge`` inside the caller's
    transaction, because every laboratory scenario is a single inbound message. Not one provider
    scenario is: each needs the agent, committed transactions on both sides, and in two cases a real
    laboratory workflow in between. So the driving happens outside and this assembles the verdict --
    which is the same arrangement ``order_receipt`` already has on the laboratory side, arrived at
    for the same reason.

    ``REVIEW_REPORTING`` is not scenario'd. It is optional, nothing in DHealth consumes a
    connector-reported review yet, and inventing a scenario for it would manufacture evidence for a
    capability no facility can currently use.
    """

    def __init__(self, *, declared_capabilities: frozenset[Capability]) -> None:
        self._declared = declared_capabilities

    def run(
        self,
        *,
        started_at,
        completed_at,
        submission: SubmissionEvidence | None = None,
        released_result: NotificationEvidence | None = None,
        amendment: NotificationEvidence | None = None,
    ):
        """Assemble the run. A capability the connector never declared fails without being driven.

        Undeclared is a failure rather than an omission for the same reason the laboratory harness
        treats it that way: the required provider bundle is the set whose absence breaks the round
        trip, and a connector that submits orders and cannot receive results never learns the
        answer. Reporting that as "not applicable" would let it qualify.
        """
        from daher_adapter.connect.harness import QualificationRun, ScenarioResult

        results: list[ScenarioResult] = []
        for result in provider_scenarios(
            submission=submission, released_result=released_result, amendment=amendment
        ):
            capability = result.capability
            if capability is not None and capability not in self._declared:
                results.append(
                    ScenarioResult(
                        scenario=result.scenario,
                        capability=capability,
                        evidence_level=EvidenceLevel.EXERCISED,
                        passed=False,
                        detail="capacité non déclarée par le connecteur",
                    )
                )
                continue
            results.append(result)

        return QualificationRun(
            started_at=started_at, completed_at=completed_at, scenarios=tuple(results)
        )
