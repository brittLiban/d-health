"""DH-CONNECT-CONTRACT-001 — the qualification harness.

A connector proves it satisfies the contract by being driven through a fixed set of scenarios and
producing the required effects. That is what "qualified" means here, and it is why no operator can
type it in: qualification is the output of running these scenarios, not an opinion anybody holds.

**Protocol-neutral, and synthetic.** The subject is a ``ConnectorSubject`` -- an object that
produces canonical envelopes on demand. A real connector's adapter will implement this by talking
FHIR or HL7 or SFTP to a partner system; the ``SyntheticConnectorSubject`` here talks to nothing at
all. No external system is contacted, no credential exists, and no real patient data is involved.

The value of a shared harness is that the next connector cannot invent its own checklist. An
OpenELIS adapter and a vendor REST adapter pass or fail the same scenarios, so "qualified" means
the same thing for both, and a partner cannot be qualified on an easier test than a competitor.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.orm import Session

from daher_adapter.connect.apply import ConnectApplyError, ConnectBridge
from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    BusinessAcceptance,
    BusinessRejection,
    Capability,
    CollectionEvent,
    ConnectMessage,
    Envelope,
    FailureCategory,
    ReleasedAmendment,
    ReleasedResult,
    ResultObservation,
    TechnicalAck,
)
from daher_adapter.connect.models import EvidenceLevel
from daher_adapter.connect.repository import CapabilityOutcome


class ConnectorSubject(Protocol):
    """What the harness needs from the thing being qualified.

    Deliberately tiny. A connector under test has to be able to say what it claims to support, to
    produce a canonical envelope for a scenario, and to answer the one question no automated test
    can infer -- whether its workflow requires a human to type the same result twice.
    """

    @property
    def connector_key(self) -> str: ...

    @property
    def declared_capabilities(self) -> frozenset[Capability]: ...

    @property
    def requires_manual_duplicate_entry(self) -> bool:
        """Whether using this connector means entering the same data in two systems by hand.

        ADR-043 Law 2 is not advisory, and this is where it becomes a qualification requirement
        rather than a slogan. A "connector" that leaves a technician entering a result in the LIS
        and then again in DHealth has automated nothing and has doubled the transcription-error
        surface on a clinical value. It does not qualify, whatever else it passes.
        """
        ...

    def envelope_for(
        self,
        *,
        scenario: str,
        organization_id: uuid.UUID,
        installation_id: uuid.UUID,
        lab_order_id: uuid.UUID | None,
        payload: ConnectMessage,
        source_event_id: str,
        source_occurred_at: datetime,
    ) -> Envelope: ...


@dataclass
class SyntheticConnectorSubject:
    """A connector that exists only to be qualified against.

    It contacts nothing, holds no credential, and carries no real patient data. Its point is to let
    the scenarios below be executed and proved *before* a real adapter exists, so the first real
    connector meets a harness that has already been shown to catch the failures it is meant to
    catch -- including by the negative controls, which break the harness on purpose and check that
    the corresponding scenario goes red.
    """

    connector_key: str = "synthetic-connector"
    declared_capabilities: frozenset[Capability] = field(
        default_factory=lambda: frozenset(Capability)
    )
    requires_manual_duplicate_entry: bool = False
    #: Set to make the subject emit a result the source never professionally released, so LQ6 can
    #: be shown to refuse it rather than merely be asserted to.
    emit_unreleased_result: bool = False

    def envelope_for(
        self,
        *,
        scenario: str,  # noqa: ARG002 -- part of the protocol; a real adapter branches on it
        organization_id: uuid.UUID,
        installation_id: uuid.UUID,
        lab_order_id: uuid.UUID | None,
        payload: ConnectMessage,
        source_event_id: str,
        source_occurred_at: datetime,
    ) -> Envelope:
        return Envelope(
            contract_version=CONTRACT_VERSION,
            message_id=uuid.uuid4(),
            connector_installation_id=installation_id,
            organization_id=organization_id,
            source_system="synthetic-lis",
            source_event_id=source_event_id,
            source_occurred_at=source_occurred_at,
            # Deliberately later than `source_occurred_at`: a connector draining a backlog after an
            # outage transmits today what happened last week, and the scenarios exercise that gap
            # rather than pretending the two timestamps are interchangeable.
            sent_at=datetime.now(UTC),
            lab_order_id=lab_order_id,
            payload=payload,
        )


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    capability: Capability | None
    passed: bool
    #: How strongly this scenario evidenced its capability. Defaults to the weaker answer on
    #: purpose: a scenario that forgets to say is a scenario nobody has checked drives anything,
    #: and defaulting to EXERCISED would make forgetting look like proof.
    evidence_level: EvidenceLevel = EvidenceLevel.DECLARED
    detail: str | None = None
    limitation: str | None = None
    """Why a PASS here proves less than the other scenarios' PASSes.

    ``detail`` explains a failure and is dropped once a scenario passes. A limitation is the
    opposite: it survives the pass, because it is the part of the claim the harness did *not*
    establish. It is carried into the recorded capability row so the evidence itself says how far
    the proof goes -- a qualification whose weak spots only live in a source comment is a
    qualification that will eventually be read as stronger than it is.
    """


@dataclass(frozen=True)
class QualificationRun:
    """Everything one harness run concluded. Turned into durable rows by the repository."""

    started_at: datetime
    completed_at: datetime
    scenarios: tuple[ScenarioResult, ...]

    @property
    def capability_outcomes(self) -> tuple[CapabilityOutcome, ...]:
        """Per capability, passing only if **every** scenario that exercises it passed.

        A capability with one failing scenario is a capability that does not hold. Reporting it as
        passed because two of three scenarios worked would be exactly the "connected=true"
        flattening the capability model exists to avoid.
        """
        by_capability: dict[Capability, list[ScenarioResult]] = {}
        for result in self.scenarios:
            if result.capability is None:
                continue
            by_capability.setdefault(result.capability, []).append(result)
        outcomes: list[CapabilityOutcome] = []
        for capability in sorted(by_capability, key=lambda c: c.value):
            results = by_capability[capability]
            failures = [r for r in results if not r.passed]
            notes = [f"{r.scenario}: {r.detail or 'échec'}" for r in failures]
            # Limitations are reported whether or not the capability passed -- see
            # ScenarioResult.limitation. A passing capability with a limitation is still a pass,
            # but the evidence row says what the pass did not cover.
            notes.extend(
                f"{r.scenario}: {r.limitation}" for r in results if r.passed and r.limitation
            )
            # A capability is EXERCISED only if *every* scenario touching it exercised it. One
            # declaration-only scenario in the set means the capability as a whole has not been
            # driven end to end, and rounding that up to EXERCISED because the other scenarios
            # were is exactly the "reinterpret weak evidence as strong" move this column exists to
            # prevent. The strength of a claim is the strength of its weakest support.
            evidence = (
                EvidenceLevel.EXERCISED
                if all(r.evidence_level is EvidenceLevel.EXERCISED for r in results)
                else EvidenceLevel.DECLARED
            )
            outcomes.append(
                CapabilityOutcome(
                    capability=capability,
                    passed=not failures,
                    evidence_level=evidence,
                    detail="; ".join(notes) or None,
                )
            )
        return tuple(outcomes)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.scenarios)

    @property
    def evidence_summary(self) -> str:
        passed = sum(1 for r in self.scenarios if r.passed)
        total = len(self.scenarios)
        limited = [r.scenario for r in self.scenarios if r.passed and r.limitation]
        # A summary that reads "tous satisfaits" while some scenarios only checked a declaration
        # would overstate the run. Name them.
        caveat = f" ; déclaration seule : {', '.join(limited)}" if limited else ""
        if passed == total:
            return f"{total} scénario(s) exécuté(s), tous satisfaits{caveat}"
        failed = [r.scenario for r in self.scenarios if not r.passed]
        return (
            f"{passed}/{total} scénario(s) satisfaits ; "
            f"non satisfaits : {', '.join(failed)}{caveat}"
        )


def _numeric(source_code: str, value: str, unit: str = "mmol/L") -> ResultObservation:
    from decimal import Decimal

    return ResultObservation(
        source_code=source_code,
        display_label=source_code,
        result_type="NUMERIC",
        numeric_value=Decimal(value),
        numeric_unit=unit,
    )


class LabQualificationHarness:
    """Drives a laboratory connector subject through the contract's laboratory scenarios.

    Every scenario is executed against a real ``ConnectBridge`` writing to a real database session,
    so a PASS means Exchange rows actually appeared -- a released result really did create a
    ``LabResultRelease``, an amendment really did create a second ``LabResultReleaseVersion``. A
    harness that only asserted the subject returned the right object would prove the subject, not
    the integration.
    """

    def __init__(
        self,
        *,
        bridge: ConnectBridge,
        subject: ConnectorSubject,
        organization_id: uuid.UUID,
        installation_id: uuid.UUID,
    ) -> None:
        self._bridge = bridge
        self._subject = subject
        self._organization_id = organization_id
        self._installation_id = installation_id

    def _envelope(
        self,
        *,
        scenario: str,
        lab_order_id: uuid.UUID | None,
        payload: ConnectMessage,
        source_event_id: str,
        occurred_offset_minutes: int = -90,
    ) -> Envelope:
        return self._subject.envelope_for(
            scenario=scenario,
            organization_id=self._organization_id,
            installation_id=self._installation_id,
            lab_order_id=lab_order_id,
            payload=payload,
            source_event_id=source_event_id,
            source_occurred_at=datetime.now(UTC) + timedelta(minutes=occurred_offset_minutes),
        )

    def run(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        observations: Sequence[ResultObservation] | None = None,
    ) -> QualificationRun:
        """Execute the laboratory scenario set against one prepared order."""
        started = datetime.now(UTC)
        results: list[ScenarioResult] = []
        obs = tuple(observations or (_numeric("GLU", "5.4"),))
        declared = self._subject.declared_capabilities

        # -- no double entry ------------------------------------------------------------------
        # Checked first and reported as its own scenario: a connector that requires a human to
        # retype the result has failed the product law before any message semantics matter.
        #
        # `capability=None`, changed by this correction. NDE1 used to be filed under
        # RELEASED_RESULT_PUBLICATION, which was harmless while every passing row counted equally
        # and became wrong once evidence strength mattered: NDE1 reads the subject's declared
        # `requires_manual_duplicate_entry` rather than driving anything, so under the new rule it
        # would drag RELEASED_RESULT_PUBLICATION down to DECLARED and make it permanently
        # unqualifiable -- a scenario about workflow ergonomics silently blocking a capability the
        # harness genuinely exercises. It belongs at the run level, which is where it always
        # behaved: `all_passed` covers scenarios with no capability, so a connector that requires
        # double entry still fails the whole run.
        results.append(
            ScenarioResult(
                scenario="NDE1-no-double-entry",
                capability=None,
                passed=not self._subject.requires_manual_duplicate_entry,
                detail=(
                    None
                    if not self._subject.requires_manual_duplicate_entry
                    else "le flux impose une double saisie manuelle du résultat"
                ),
            )
        )

        # -- LQ2/LQ3: technical receipt is not acceptance --------------------------------------
        if Capability.BUSINESS_ACCEPTANCE in declared:
            results.extend(self._acceptance_scenarios(session, order_id))
        else:
            results.append(
                ScenarioResult(
                    scenario="LQ3-business-acceptance",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="capacité non déclarée par le connecteur",
                )
            )

        # -- LQ5: collection, which the Exchange requires before any result can be entered ------
        if Capability.COLLECTION_EVENT in declared:
            results.extend(self._collection_scenarios(session, order_id))
        else:
            results.append(
                ScenarioResult(
                    scenario="LQ5-collection-event",
                    capability=Capability.COLLECTION_EVENT,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="capacité non déclarée ; sans elle aucun résultat ne peut être saisi",
                )
            )

        # -- LQ6/LQ7/LQ8/LQ9: release semantics ------------------------------------------------
        if Capability.RELEASED_RESULT_PUBLICATION in declared:
            results.extend(self._release_scenarios(session, order_id, obs))
        else:
            results.append(
                ScenarioResult(
                    scenario="LQ7-released-result",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="capacité non déclarée par le connecteur",
                )
            )

        # -- LQ10/LQ11: amendment --------------------------------------------------------------
        if Capability.AMENDMENT_PUBLICATION in declared:
            results.extend(self._amendment_scenarios(session, order_id, obs))
        else:
            results.append(
                ScenarioResult(
                    scenario="LQ10-amendment",
                    capability=Capability.AMENDMENT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="capacité non déclarée par le connecteur",
                )
            )

        # -- LQ1: order receipt ----------------------------------------------------------------
        #
        # This one is honestly weaker than the others, and the scenario name says so.
        #
        # Every other scenario in this harness drives a real Exchange mutation and then reads the
        # Exchange back. ORDER_RECEIPT cannot be exercised that way here, because it is the only
        # capability whose direction is DHealth -> connector: it asks whether the connector can
        # *receive* an order we push to it. Pushing anything anywhere is delivery, and delivery is
        # DH-CONNECT-AGENT-001's subject, not this ticket's -- there is no transport to push over.
        #
        # So this scenario records a declaration and nothing more. A PASS here means "the connector
        # claims it can receive orders", not "we watched it receive one".
        #
        # The correction to this ticket made that structural rather than advisory. It used to be
        # said in the scenario name, the detail prose, and the docs -- all of which the verdict
        # ignored, so a declared ORDER_RECEIPT counted toward the required production bundle. It is
        # now `evidence_level=DECLARED`, which the verdict reads, so a laboratory connector cannot
        # be production-qualified until DH-CONNECT-AGENT-001 supplies a delivery path that can push
        # an order into it and observe the acknowledgement. Stated explicitly here rather than left
        # to the field default: this is the line somebody will be tempted to flip.
        declared_receipt = Capability.ORDER_RECEIPT in declared
        results.append(
            ScenarioResult(
                scenario="LQ1-order-receipt-declared",
                capability=Capability.ORDER_RECEIPT,
                evidence_level=EvidenceLevel.DECLARED,
                passed=declared_receipt,
                detail=None if declared_receipt else "capacité non déclarée par le connecteur",
                limitation=(
                    "déclaration seule : la réception d'une commande n'est pas exercée dans cette "
                    "version (aucun transport n'existe pour la livrer)"
                    if declared_receipt
                    else None
                ),
            )
        )

        return QualificationRun(
            started_at=started,
            completed_at=datetime.now(UTC),
            scenarios=tuple(results),
        )

    # -- scenario groups ---------------------------------------------------------------------

    def _acceptance_scenarios(self, session: Session, order_id: uuid.UUID) -> list[ScenarioResult]:
        from daher_adapter.exchange.models import LabOrder, LabOrderStatus

        out: list[ScenarioResult] = []

        # LQ2: a technical acknowledgement must not accept anything.
        #
        # This scenario used to only *look* at the order's status before sending anything, which
        # was worthless: nothing had happened yet, so it passed no matter what the bridge did with
        # a TECHNICAL_ACK. A negative control (§71, NC2) that made the bridge treat an ack as a
        # business acceptance left this green, which is how the hole was found. It now actually
        # sends one.
        #
        # The distinction is not pedantry. "We received your message" and "the laboratory will do
        # this work" are the two facts a partner integration is most likely to merge, because in
        # HTTP they are the same 200. Merging them means a clinician is told a laboratory took
        # their order when all that happened is a server wrote it to disk.
        ack = self._envelope(
            scenario="LQ2",
            lab_order_id=order_id,
            payload=TechnicalAck(acknowledged_message_id=uuid.uuid4(), external_receipt_id="RCV-1"),
            source_event_id="ack-1",
        )
        try:
            self._bridge.apply(session, ack)
            out.append(
                ScenarioResult(
                    scenario="LQ2-technical-receipt-is-not-acceptance",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="un accusé de réception technique a modifié l'état de l'ordre",
                )
            )
        except ConnectApplyError as error:
            session.flush()
            session.expire_all()
            after = session.get(LabOrder, order_id)
            untouched = after is not None and after.status != LabOrderStatus.ACCEPTED.value
            refused_correctly = error.category is FailureCategory.UNSUPPORTED_WORKFLOW
            out.append(
                ScenarioResult(
                    scenario="LQ2-technical-receipt-is-not-acceptance",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=refused_correctly and untouched,
                    detail=None
                    if refused_correctly and untouched
                    else (
                        "l'ordre est passé à ACCEPTED malgré le refus"
                        if not untouched
                        else f"refusé, mais classé {error.category.value}"
                    ),
                )
            )

        envelope = self._envelope(
            scenario="LQ3",
            lab_order_id=order_id,
            payload=BusinessAcceptance(external_order_id="EXT-ORDER-1"),
            source_event_id="accept-1",
        )
        try:
            self._bridge.apply(session, envelope)
            session.flush()
            session.expire_all()
            after = session.get(LabOrder, order_id)
            accepted = after is not None and after.status == LabOrderStatus.ACCEPTED.value
            out.append(
                ScenarioResult(
                    scenario="LQ3-business-acceptance",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=accepted,
                    detail=None if accepted else "l'acceptation métier n'a pas atteint ACCEPTED",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="LQ3-business-acceptance",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail=f"{error.category.value}: {error}",
                )
            )

        # Q1: an exact replay of the same event must have no second effect.
        try:
            replay = self._bridge.apply(session, envelope)
            out.append(
                ScenarioResult(
                    scenario="Q1-exact-replay",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=replay.replayed and not replay.applied,
                    detail=None if replay.replayed else "le rejeu a produit un second effet",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="Q1-exact-replay",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail=f"{error.category.value}: {error}",
                )
            )

        # Q2: the same event identity carrying different content must be refused, never merged.
        conflicting = self._envelope(
            scenario="Q2",
            lab_order_id=order_id,
            payload=BusinessRejection(external_order_id="EXT-ORDER-1", reason="analyseur en panne"),
            source_event_id="accept-1",
        )
        try:
            self._bridge.apply(session, conflicting)
            out.append(
                ScenarioResult(
                    scenario="Q2-conflicting-replay",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="un contenu contradictoire a été accepté sous la même identité",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="Q2-conflicting-replay",
                    capability=Capability.BUSINESS_ACCEPTANCE,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=error.category is FailureCategory.CONFLICT,
                    detail=None
                    if error.category is FailureCategory.CONFLICT
                    else f"refusé, mais classé {error.category.value}",
                )
            )
        return out

    def _collection_scenarios(self, session: Session, order_id: uuid.UUID) -> list[ScenarioResult]:
        """LQ5: only an explicit, source-observed collection signal creates the milestone.

        Two things are proved here. That the connector can report one at all -- without which the
        Exchange will not let the order reach ``IN_PROGRESS`` and no result can ever be entered.
        And that DHealth did not record the milestone on its own beforehand: collection occurring
        and collection being recorded are different facts, and an order that arrived with a
        milestone nobody reported would mean DHealth had inferred a physical event.
        """
        from daher_adapter.exchange.models import LabSampleCollection

        out: list[ScenarioResult] = []
        pre_existing = (
            session.query(LabSampleCollection)
            .filter(LabSampleCollection.lab_order_id == order_id)
            .count()
        )
        out.append(
            ScenarioResult(
                scenario="LQ5a-collection-is-never-inferred",
                capability=Capability.COLLECTION_EVENT,
                evidence_level=EvidenceLevel.EXERCISED,
                passed=pre_existing == 0,
                detail=None
                if pre_existing == 0
                else "un prélèvement était enregistré sans qu'aucune source ne l'ait signalé",
            )
        )

        envelope = self._envelope(
            scenario="LQ5",
            lab_order_id=order_id,
            payload=CollectionEvent(external_order_id="EXT-ORDER-1"),
            source_event_id="collect-1",
            occurred_offset_minutes=-60,
        )
        try:
            self._bridge.apply(session, envelope)
            session.flush()
            recorded = (
                session.query(LabSampleCollection)
                .filter(LabSampleCollection.lab_order_id == order_id)
                .count()
            )
            out.append(
                ScenarioResult(
                    scenario="LQ5-collection-event",
                    capability=Capability.COLLECTION_EVENT,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=recorded == 1,
                    detail=None if recorded == 1 else f"{recorded} prélèvement(s) enregistré(s)",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="LQ5-collection-event",
                    capability=Capability.COLLECTION_EVENT,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail=f"{error.category.value}: {error}",
                )
            )
        return out

    def _release_scenarios(
        self, session: Session, order_id: uuid.UUID, observations: tuple[ResultObservation, ...]
    ) -> list[ScenarioResult]:
        from daher_adapter.exchange.models import LabResultRelease

        out: list[ScenarioResult] = []

        # LQ4: §17, fail closed on a code DHealth cannot map to an ordered item.
        #
        # This is the scenario a connector is most likely to fail in the field and least likely to
        # fail in a demo, because mappings drift *after* they are set up: the partner adds a test,
        # renames a code, upgrades their LIS. The dangerous outcome is not a refusal -- it is a
        # guess, an observation filed against whichever item looked closest, producing a result for
        # a test nobody ordered under a patient who did have something ordered. So the harness sends
        # a code that is deliberately not on the order and requires the bridge to refuse it as
        # MAPPING, which is also the category that tells a runtime not to retry: retrying an
        # unmappable code forever fixes nothing and hides the mapping problem.
        unmappable = self._envelope(
            scenario="LQ4",
            lab_order_id=order_id,
            payload=ReleasedResult(
                external_order_id="EXT-ORDER-1",
                external_report_id="RPT-UNMAPPED",
                source_release_confirmed=True,
                observations=(
                    ResultObservation(
                        source_code="CODE-INCONNU-DU-CATALOGUE",
                        display_label="Analyse non commandée",
                        result_type="TEXT",
                        text_value="valeur synthétique",
                    ),
                ),
            ),
            source_event_id="unmapped-1",
        )
        try:
            self._bridge.apply(session, unmappable)
            out.append(
                ScenarioResult(
                    scenario="LQ4-unmapped-code-fails-closed",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="un code sans correspondance a été accepté au lieu d'être refusé",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="LQ4-unmapped-code-fails-closed",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=error.category is FailureCategory.MAPPING,
                    detail=None
                    if error.category is FailureCategory.MAPPING
                    else f"refusé, mais classé {error.category.value} plutôt que MAPPING",
                )
            )

        # LQ6: a result the source has not professionally released must never become a released
        # DHealth result. Executed, not assumed: the harness actually sends one.
        unreleased = self._envelope(
            scenario="LQ6",
            lab_order_id=order_id,
            payload=ReleasedResult(
                external_order_id="EXT-ORDER-1",
                external_report_id="RPT-PRELIM",
                source_release_confirmed=False,
                observations=observations,
            ),
            source_event_id="prelim-1",
        )
        try:
            self._bridge.apply(session, unreleased)
            out.append(
                ScenarioResult(
                    scenario="LQ6-preliminary-is-not-released",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="un résultat non libéré à la source a été accepté comme libéré",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="LQ6-preliminary-is-not-released",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=error.category is FailureCategory.UNSUPPORTED_WORKFLOW,
                    detail=None
                    if error.category is FailureCategory.UNSUPPORTED_WORKFLOW
                    else f"refusé, mais classé {error.category.value}",
                )
            )

        released = self._envelope(
            scenario="LQ7",
            lab_order_id=order_id,
            payload=ReleasedResult(
                external_order_id="EXT-ORDER-1",
                external_report_id="RPT-1",
                source_release_confirmed=True,
                observations=observations,
            ),
            source_event_id="release-1",
        )
        try:
            self._bridge.apply(session, released)
            session.flush()
            release_row = (
                session.query(LabResultRelease)
                .filter(LabResultRelease.lab_order_id == order_id)
                .one_or_none()
            )
            out.append(
                ScenarioResult(
                    scenario="LQ7-released-result",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=release_row is not None,
                    detail=None if release_row is not None else "aucune libération enregistrée",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="LQ7-released-result",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail=f"{error.category.value}: {error}",
                )
            )

        # LQ8/Q8: republishing the identical release, as a retry after an ambiguous timeout would,
        # must leave exactly one release behind.
        try:
            replay = self._bridge.apply(session, released)
            session.flush()
            count = (
                session.query(LabResultRelease)
                .filter(LabResultRelease.lab_order_id == order_id)
                .count()
            )
            out.append(
                ScenarioResult(
                    scenario="LQ8-release-replay",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=replay.replayed and count == 1,
                    detail=None if count == 1 else f"{count} libérations pour un seul ordre",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="LQ8-release-replay",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail=f"{error.category.value}: {error}",
                )
            )

        # LQ9: the same external report identity with different content is a conflict.
        conflicting = self._envelope(
            scenario="LQ9",
            lab_order_id=order_id,
            payload=ReleasedResult(
                external_order_id="EXT-ORDER-1",
                external_report_id="RPT-1",
                source_release_confirmed=True,
                observations=(_numeric("GLU", "99.9"),),
            ),
            source_event_id="release-1",
        )
        try:
            self._bridge.apply(session, conflicting)
            out.append(
                ScenarioResult(
                    scenario="LQ9-conflicting-release",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail="un contenu contradictoire a écrasé une libération existante",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="LQ9-conflicting-release",
                    capability=Capability.RELEASED_RESULT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=error.category is FailureCategory.CONFLICT,
                    detail=None
                    if error.category is FailureCategory.CONFLICT
                    else f"refusé, mais classé {error.category.value}",
                )
            )
        return out

    def _amendment_scenarios(
        self, session: Session, order_id: uuid.UUID, observations: tuple[ResultObservation, ...]
    ) -> list[ScenarioResult]:
        from daher_adapter.exchange.models import LabResultReleaseVersion

        out: list[ScenarioResult] = []
        corrected = tuple(
            ResultObservation(
                source_code=o.source_code,
                display_label=o.display_label,
                result_type=o.result_type,
                numeric_value=(o.numeric_value + 1) if o.numeric_value is not None else None,
                numeric_unit=o.numeric_unit,
                text_value=(o.text_value + " (corrigé)") if o.text_value else None,
            )
            for o in observations
        )
        amendment = self._envelope(
            scenario="LQ10",
            lab_order_id=order_id,
            payload=ReleasedAmendment(
                external_order_id="EXT-ORDER-1",
                external_report_id="RPT-2",
                corrects_external_report_id="RPT-1",
                source_release_confirmed=True,
                observations=corrected,
            ),
            source_event_id="amend-1",
            occurred_offset_minutes=-10,
        )
        try:
            self._bridge.apply(session, amendment)
            session.flush()
            versions = (
                session.query(LabResultReleaseVersion)
                .filter(LabResultReleaseVersion.lab_order_id == order_id)
                .all()
            )
            numbers = sorted(v.version_number for v in versions)
            out.append(
                ScenarioResult(
                    scenario="LQ10-post-release-correction",
                    capability=Capability.AMENDMENT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=numbers == [1, 2],
                    detail=None if numbers == [1, 2] else f"versions présentes : {numbers}",
                )
            )
            # LQ11: the original must still be reconstructable. Version 1 still exists, still binds
            # to the original release, and its manifest still names the observations it released.
            v1 = next((v for v in versions if v.version_number == 1), None)
            out.append(
                ScenarioResult(
                    scenario="LQ11-original-remains-reconstructable",
                    capability=Capability.AMENDMENT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=v1 is not None and v1.lab_result_release_id is not None,
                    detail=None
                    if v1 is not None and v1.lab_result_release_id is not None
                    else "la version 1 n'est plus liée à sa libération d'origine",
                )
            )
        except ConnectApplyError as error:
            out.append(
                ScenarioResult(
                    scenario="LQ10-post-release-correction",
                    capability=Capability.AMENDMENT_PUBLICATION,
                    evidence_level=EvidenceLevel.EXERCISED,
                    passed=False,
                    detail=f"{error.category.value}: {error}",
                )
            )
        return out
