"""DH-CONNECT-CONTRACT-001 — turning a canonical Connect message into real Exchange state.

This is the only place the contract meets the domain, and it is deliberately thin: every mutation
goes through an Exchange repository that already owns its rule. Nothing here re-implements order
transitions, release authorization, amendment versioning or patient identity -- if this file ever
starts writing an Exchange table directly, that is the signal that a rule is being duplicated.

**No transport.** Nothing here opens a socket, parses FHIR or HL7, reads a file, or calls an
external system. A connector runtime hands it an ``Envelope`` that some adapter already produced;
how that adapter obtained it is DH-CONNECT-AGENT-001's subject.

**Attribution is a placeholder, and named as one.** Exchange rows record *who* acted --
``released_by_identity_id`` is a non-nullable foreign key, by design, because "somebody released
this result" without a somebody is not a fact. A connector runtime will eventually authenticate as
a service identity bound to its facility (§58). That does not exist yet, so the caller supplies an
``ExchangeIdentity`` explicitly and the qualification harness uses a synthetic one. What is
deliberately *not* done is relaxing the column to nullable or inventing a second attribution model
to avoid the question.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from daher_adapter.connect.contract import (
    BusinessAcceptance,
    BusinessRejection,
    CollectionEvent,
    Envelope,
    FailureCategory,
    OrderSubmission,
    ReceiptStore,
    ReleasedAmendment,
    ReleasedResult,
    ResultObservation,
)
from daher_adapter.exchange.models import (
    LabObservation,
    LabOrder,
    LabOrderItem,
    LabResultType,
    TestCatalogEntry,
)
from daher_adapter.exchange.results import _effective_observation_rows


class ConnectApplyError(RuntimeError):
    """A message could not be applied, with the category that says whether retrying could help."""

    def __init__(self, category: FailureCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class ApplyOutcome:
    """What applying a message actually did.

    ``applied=False`` with no error is the replay case: the event had already been applied with
    identical content, so nothing happened and nothing needed to. That is a success, not a failure
    -- at-least-once transport means honest retries are normal traffic, and treating one as an
    error would make every timeout look like a problem.
    """

    applied: bool
    replayed: bool = False
    detail: str | None = None
    #: The DHealth order this message created, for ``ORDER_SUBMISSION`` only. The inbound applier
    #: binds the correlation from it, in the same transaction as the order itself -- an order whose
    #: correlation was written by a second, later transaction is an order whose results cannot be
    #: filed if that second transaction never runs.
    lab_order_id: uuid.UUID | None = None


def _guard_binding(envelope: Envelope, installation_organization_id: uuid.UUID) -> None:
    """A connector may act only for the facility its installation is bound to.

    The envelope carries an ``organization_id``, and this refuses to take its word for it: the
    authority is the installation row, and a mismatch is a cross-tenant write attempt whether it
    came from a bug or from somebody trying. This is the structural reason qualification for
    facility A can never affect facility B.
    """
    if envelope.organization_id != installation_organization_id:
        raise ConnectApplyError(
            FailureCategory.VALIDATION,
            f"envelope claims organization {envelope.organization_id} but this connector "
            f"installation is bound to {installation_organization_id}",
        )


def _replay_check(envelope: Envelope, receipts: ReceiptStore) -> bool:
    """``True`` when this exact event with this exact content has already been applied.

    A different content fingerprint under the same event identity is a **conflict**: two
    incompatible claims about one event, which cannot both be true and must never be resolved by
    letting the second overwrite the first. Quarantine is the only safe answer; the runtime that
    owns the dead-letter queue is DH-CONNECT-AGENT-001's.
    """
    scope = envelope.replay_scope()
    previous = receipts.seen(scope, envelope.source_event_id)
    if previous is None:
        return False
    if previous == envelope.content_fingerprint():
        return True
    raise ConnectApplyError(
        FailureCategory.CONFLICT,
        f"source event {envelope.source_event_id!r} was already applied with different content; "
        "refusing rather than overwriting what the first message asserted",
    )


def _load_order(session: Session, envelope: Envelope) -> LabOrder:
    """Fetch the order named by the envelope. **This does not check who owns it.**

    Ownership is enforced one layer down: every mutation path here passes
    ``lab_organization_id=self._organization_id`` into ``LabWorkflowRepository`` /
    ``LabResultReleaseRepository``, and those compare it against the order's ``LabSelection`` and
    raise ``LabOrderNotAssigned`` on a mismatch (lab_workflow.py:139, :272, :343). Combined with
    ``_guard_binding``, that is what stops a connector touching another facility's order.

    So: a new path added here that reads this order and then writes *without* going through a
    repository that takes ``lab_organization_id`` would have no tenant check at all. Do not add one.
    """
    order = session.get(LabOrder, envelope.lab_order_id)
    if order is None:
        raise ConnectApplyError(
            FailureCategory.VALIDATION, f"no DHealth order {envelope.lab_order_id}"
        )
    return order


def _observation_inputs(observations: tuple[ResultObservation, ...]) -> None:
    for obs in observations:
        if obs.result_type == "NUMERIC" and obs.numeric_value is None:
            raise ConnectApplyError(
                FailureCategory.VALIDATION,
                f"observation {obs.source_code!r} is NUMERIC with no value",
            )
        if obs.result_type == "TEXT" and not obs.text_value:
            raise ConnectApplyError(
                FailureCategory.VALIDATION,
                f"observation {obs.source_code!r} is TEXT with no value",
            )


class ConnectBridge:
    """Applies canonical Connect messages through the Exchange's own repositories.

    Constructed with the repositories rather than importing them, so a test can watch exactly which
    domain call each message makes -- and so it is visible at a glance that this class owns no rule
    of its own.
    """

    def __init__(
        self,
        *,
        workflow,  # LabWorkflowRepository
        results,  # LabResultRepository
        releases,  # LabResultReleaseRepository
        amendments,  # LabResultAmendmentRepository
        receipts: ReceiptStore,
        installation_organization_id: uuid.UUID,
        service_identity_id: uuid.UUID,
        orders=None,  # LabOrderRepository -- provider side only
        patients=None,  # PatientReferenceRepository -- provider side only
        practitioners=None,  # PractitionerReferenceRepository -- provider side only
    ) -> None:
        self._workflow = workflow
        self._results = results
        self._releases = releases
        self._amendments = amendments
        self._receipts = receipts
        self._organization_id = installation_organization_id
        # -- provider side, added by DH-CONNECT-AGENT-001 -------------------------------------
        #
        # Optional because a *laboratory* connector never submits an order, and requiring a
        # laboratory's bridge to be handed three repositories it will never call would be three more
        # things to wire wrongly. A provider bridge without them refuses ORDER_SUBMISSION with
        # UNSUPPORTED_WORKFLOW rather than half-creating something -- the fail-closed direction.
        self._orders = orders
        self._patients = patients
        self._practitioners = practitioners
        #: The identity every Exchange row this bridge writes is attributed to. A placeholder for
        #: the service identity DH-CONNECT-AGENT-001 will issue -- see the module docstring.
        self._identity_id = service_identity_id

    # -- laboratory side ---------------------------------------------------------------------

    def apply(self, session: Session, envelope: Envelope) -> ApplyOutcome:
        """Route one envelope to the handler for its message type."""
        _guard_binding(envelope, self._organization_id)
        if _replay_check(envelope, self._receipts):
            return ApplyOutcome(applied=False, replayed=True, detail="already applied")

        payload = envelope.payload
        if isinstance(payload, OrderSubmission):
            outcome = self._submit_order(session, envelope, payload)
        elif isinstance(payload, BusinessAcceptance):
            outcome = self._accept(session, envelope, payload)
        elif isinstance(payload, BusinessRejection):
            outcome = self._reject(session, envelope, payload)
        elif isinstance(payload, CollectionEvent):
            outcome = self._collect(session, envelope)
        elif isinstance(payload, ReleasedResult):
            outcome = self._release(session, envelope, payload)
        elif isinstance(payload, ReleasedAmendment):
            outcome = self._amend(session, envelope, payload)
        else:
            # A TECHNICAL_ACK reaching here means a connector tried to advance clinical workflow
            # with a transport receipt. It is not an Exchange mutation and never becomes one.
            raise ConnectApplyError(
                FailureCategory.UNSUPPORTED_WORKFLOW,
                f"{payload.message_type.value} does not change DHealth order state; a technical "
                "acknowledgement is not a business decision",
            )

        self._receipts.record(
            envelope.replay_scope(), envelope.source_event_id, envelope.content_fingerprint()
        )
        return outcome

    # -- provider side -----------------------------------------------------------------------

    def _submit_order(
        self, session: Session, envelope: Envelope, payload: OrderSubmission
    ) -> ApplyOutcome:
        """A provider's own system asking DHealth for diagnostic work.

        The only message that *creates* rather than advances, which makes it the only one where
        getting the identity wrong invents a patient instead of misfiling a result. So every
        identifier is taken exactly as the provider asserted it and bound through the Exchange's own
        repository — no inference, no demographic matching, no creation of a second identity for a
        patient the provider already has one for.

        **The whole submission fails, or none of it does.** An unmapped test code refuses the order
        rather than creating it with the codes that did map: a laboratory that runs four of five
        requested tests and reports four results has produced a clinically misleading report, and
        nothing downstream would know a test had been dropped. That is §42's fail-closed rule, and
        the reason this method resolves *every* code before it writes anything.

        Idempotent on the source event. A retried submission converges on the order the first one
        created rather than making a second — the replay check above catches the ordinary case, and
        the idempotency key carries the rest.
        """
        # Locally imported for the same reason ``_resolve_items`` imports ``OrderItemInput``
        # locally: ``exchange.facility_enrollment`` imports ``connect.qualification``, so a
        # module-level reach into the Exchange's order package from here closes a cycle.
        from daher_adapter.exchange.orders import DuplicateOrderItems, EmptyOrder
        from daher_adapter.exchange.organizations import InvalidOrganizationType

        if self._orders is None or self._patients is None or self._practitioners is None:
            raise ConnectApplyError(
                FailureCategory.UNSUPPORTED_WORKFLOW,
                "this connector installation is not configured to submit orders",
            )

        # The identity the provider asserts must be the provider's own. A submission carrying a
        # patient scoped to a *different* organization is a cross-tenant identity assertion, and
        # `_guard_binding` above only checked the envelope -- not what is inside it.
        for label, reference in (
            ("patient", payload.patient),
            ("practitioner", payload.practitioner),
        ):
            if reference.source_organization_id != self._organization_id:
                raise ConnectApplyError(
                    FailureCategory.VALIDATION,
                    f"the {label} identity is scoped to another organization; a connector may only "
                    "assert identities for the facility it is installed at",
                )

        items = self._resolve_items(session, payload.source_test_codes)

        # Every call below is about **one** organization -- this installation's -- because the loop
        # above refused any identity scoped elsewhere. So an ``InvalidOrganizationType`` from any of
        # them says the same thing: this facility is not a provider. Catching them together is
        # therefore accurate rather than convenient; were the identities allowed to name other
        # organizations, this would have to distinguish which one was rejected.
        try:
            patient, _ = self._patients.get_or_create_with_outcome(
                source_organization_id=self._organization_id,
                source_system=payload.patient.source_system,
                source_identity_system=payload.patient.source_identity_system,
                source_identity_value=payload.patient.source_identity_value,
                session=session,
            )
            practitioner = self._practitioners.get_or_create(
                source_organization_id=self._organization_id,
                source_system=payload.practitioner.source_system,
                source_identity_system=payload.practitioner.source_identity_system,
                source_identity_value=payload.practitioner.source_identity_value,
                session=session,
            )
            created = self._create_order(session, envelope, patient, practitioner, items)
        except InvalidOrganizationType as error:
            # A *laboratory* installation submitting an order, or a suspended facility submitting
            # one. Terminal rather than retryable: the Exchange's own provider check is what refuses
            # it, one layer below this file, and no amount of retrying turns a laboratory into a
            # provider. The refusal happens before any identity row is written, so nothing partial
            # survives even if the applier's rollback were ever removed.
            raise ConnectApplyError(FailureCategory.UNSUPPORTED_WORKFLOW, str(error)) from None
        except (DuplicateOrderItems, EmptyOrder) as error:
            raise ConnectApplyError(FailureCategory.VALIDATION, str(error)) from None
        return ApplyOutcome(
            applied=created.created,
            replayed=not created.created,
            detail=None if created.created else "commande déjà créée pour cet événement source",
            lab_order_id=created.order.id,
        )

    def _create_order(self, session, envelope, patient, practitioner, items):
        """The ``LabOrderRepository.create`` call, split out only so the caller's ``try`` is narrow.

        A wide ``try`` around the identity resolution above would catch an
        ``InvalidOrganizationType`` raised about a *patient's* organization and report it as "this
        connector cannot submit orders", which is a different and misleading fact.
        """
        if self._orders is None:  # pragma: no cover - the caller refuses before reaching here
            raise ConnectApplyError(
                FailureCategory.UNSUPPORTED_WORKFLOW,
                "this connector installation is not configured to submit orders",
            )
        return self._orders.create(
            # Scoped to the replay scope rather than the bare event id: two facilities numbering
            # their events from 1 would otherwise share an idempotency key, which is the same
            # collision `Envelope.replay_scope` exists to prevent one layer up.
            idempotency_key=f"connect:{envelope.replay_scope()}:{envelope.source_event_id}",
            provider_organization_id=self._organization_id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=items,
            session=session,
        )

    def _resolve_items(self, session: Session, source_test_codes: tuple[str, ...]):
        """DHealth catalog codes to catalog entries. Every code, before anything is written.

        **Translation is the connector's job, not DHealth's** -- symmetric with the laboratory
        direction, where a released result's ``source_code`` is matched against the order item's
        ``catalog_code_snapshot`` and a code that is not one is refused. The agent holds the
        qualified mapping, applies it on the partner's own hardware, and sends DHealth catalog
        codes; DHealth stores only the mapping's fingerprint, so it could not do the translation
        even if this method wanted to.

        An unrecognised code names itself in the refusal so the partner can fix their mapping
        rather than being told "something was wrong", and refuses the *whole* submission.
        """
        from daher_adapter.exchange.orders import OrderItemInput

        wanted = set(source_test_codes)
        rows = (
            session.query(TestCatalogEntry)
            .filter(TestCatalogEntry.code.in_(wanted), TestCatalogEntry.active.is_(True))
            .all()
        )
        # (code, version) is unique, so a code can have several rows. The current one is the highest
        # active version -- never a superseded one, which would order a test the catalog has
        # moved on from.
        by_code: dict[str, TestCatalogEntry] = {}
        for row in rows:
            held = by_code.get(row.code)
            if held is None or row.version > held.version:
                by_code[row.code] = row

        unknown = sorted(wanted - set(by_code))
        if unknown:
            raise ConnectApplyError(
                FailureCategory.MAPPING,
                f"not an active DHealth catalog code: {', '.join(unknown)}; the whole submission "
                "is refused rather than partially created",
            )
        return [OrderItemInput(catalog_entry_id=by_code[code].id) for code in source_test_codes]

    # -- laboratory side ---------------------------------------------------------------------

    def _accept(
        self, session: Session, envelope: Envelope, payload: BusinessAcceptance
    ) -> ApplyOutcome:
        """The laboratory says it will do the work. Only this message reaches ``ACCEPTED``."""
        order = _load_order(session, envelope)
        _, transitioned = self._workflow.accept(
            order_id=order.id, lab_organization_id=self._organization_id, session=session
        )
        return ApplyOutcome(
            applied=True,
            replayed=not transitioned,
            detail=f"accepted as {payload.external_accession_id or 'no accession'}",
        )

    def _reject(
        self, session: Session, envelope: Envelope, payload: BusinessRejection
    ) -> ApplyOutcome:
        order = _load_order(session, envelope)
        _, transitioned = self._workflow.reject(
            order_id=order.id, lab_organization_id=self._organization_id, session=session
        )
        # The reason travels to the operator, never into a decision. DHealth does not parse a
        # partner's sentence into a retry policy.
        return ApplyOutcome(applied=True, replayed=not transitioned, detail=payload.reason)

    def _collect(self, session: Session, envelope: Envelope) -> ApplyOutcome:
        """Only an explicit, source-observed collection event records a collection milestone.

        Never inferred from order creation, accession creation or a processing timestamp -- the
        rule DHealth has held since DH-011J2, and the reason its order status machine has no
        collection state at all.
        """
        order = _load_order(session, envelope)
        _, created = self._workflow.record_collection(
            order_id=order.id, lab_organization_id=self._organization_id, session=session
        )
        return ApplyOutcome(applied=True, replayed=not created)

    def _require_source_release(self, payload: ReleasedResult | ReleasedAmendment) -> None:
        if not payload.source_release_confirmed:
            # The single most load-bearing refusal in this file. A result the source has not
            # professionally released is a preliminary value; letting it into DHealth as released
            # would put an unvalidated number in front of a clinician with DHealth's authority
            # behind it. There is no "provisional release" state to put it in, deliberately, and a
            # LIS with no reliable release signal is a LIS that is not safely integrable -- which
            # onboarding already records as a blocker rather than working around here.
            raise ConnectApplyError(
                FailureCategory.UNSUPPORTED_WORKFLOW,
                "the source system has not confirmed professional release; a preliminary result "
                "never becomes a released DHealth result",
            )

    def _items_by_code(self, session: Session, order: LabOrder) -> Mapping[str, LabOrderItem]:
        items = session.query(LabOrderItem).filter(LabOrderItem.lab_order_id == order.id).all()
        return {item.catalog_code_snapshot: item for item in items}

    def _write_observations(
        self,
        session: Session,
        order: LabOrder,
        payload: ReleasedResult,
        items: Mapping[str, LabOrderItem],
    ) -> None:
        for obs in payload.observations:
            item = items.get(obs.source_code)
            if item is None:
                # Fail closed. An observation DHealth cannot attach to an ordered item is a result
                # for a test nobody asked for, or a mapping that has drifted -- either way, filing
                # it under a guess would be worse than refusing it.
                raise ConnectApplyError(
                    FailureCategory.MAPPING,
                    f"no ordered item matches source code {obs.source_code!r} on this order",
                )
            self._results.record_observation(
                order_id=order.id,
                lab_order_item_id=item.id,
                lab_organization_id=self._organization_id,
                result_type=LabResultType(obs.result_type),
                source_code=obs.source_code,
                display_label_snapshot=obs.display_label,
                recorded_by_identity_id=self._identity_id,
                numeric_value=obs.numeric_value,
                numeric_unit=obs.numeric_unit,
                reference_range_low=obs.reference_range_low,
                reference_range_high=obs.reference_range_high,
                text_value=obs.text_value,
                session=session,
            )
            self._results.complete_entry(
                order_id=order.id,
                lab_order_item_id=item.id,
                lab_organization_id=self._organization_id,
                completed_by_identity_id=self._identity_id,
                session=session,
            )

    def _require_processing(self, session: Session, order: LabOrder) -> None:
        """Bring the order to ``IN_PROGRESS``, or refuse if it never got there legitimately.

        §24, made concrete. External systems and networks deliver out of order, and a released
        result can genuinely arrive before the acceptance that should have preceded it. There are
        three cases and they are not the same:

        - ``ACCEPTED`` -- the laboratory took the work and has now finished it. Publishing a
          released result *is* the evidence it processed the specimen, so the Exchange's own
          ``start_processing`` transition is taken. Not a connector-only state machine: it is the
          existing ``LabOrderStatus`` path, walked through the repository that owns it.
        - ``IN_PROGRESS`` -- already there, nothing to do.
        - anything else -- most importantly ``SELECTED``, meaning no business acceptance ever
          arrived. Refused as a conflict rather than silently accepted on the laboratory's behalf.
          Inventing the missing acceptance would fabricate the one fact the contract most insists
          on keeping explicit, and the runtime's correct response is to hold or quarantine until
          the earlier message shows up.
        """
        from daher_adapter.exchange.models import LabOrderStatus

        if order.status == LabOrderStatus.IN_PROGRESS.value:
            return
        if order.status != LabOrderStatus.ACCEPTED.value:
            raise ConnectApplyError(
                FailureCategory.CONFLICT,
                f"order is {order.status!r}; a released result cannot be applied before the "
                "laboratory's business acceptance has arrived",
            )
        self._workflow.start_processing(
            order_id=order.id, lab_organization_id=self._organization_id, session=session
        )
        session.flush()
        session.refresh(order)

    def _release(
        self, session: Session, envelope: Envelope, payload: ReleasedResult
    ) -> ApplyOutcome:
        """A professionally released source result becomes exactly one DHealth released version."""
        self._require_source_release(payload)
        _observation_inputs(payload.observations)
        order = _load_order(session, envelope)
        self._require_processing(session, order)
        items = self._items_by_code(session, order)
        self._write_observations(session, order, payload, items)
        _, created = self._releases.release(
            order_id=order.id,
            lab_organization_id=self._organization_id,
            released_by_identity_id=self._identity_id,
            session=session,
        )
        # `created=False` is the Exchange's own idempotent replay: one release per order is a
        # database UNIQUE, so a second publication of the same report cannot produce a second
        # release even if the receipt store were empty. Both layers agree, which is the point.
        return ApplyOutcome(applied=True, replayed=not created)

    def _amend(
        self, session: Session, envelope: Envelope, payload: ReleasedAmendment
    ) -> ApplyOutcome:
        """A post-release correction becomes a new version; the original stays reconstructable."""
        self._require_source_release(payload)
        _observation_inputs(payload.observations)
        order = _load_order(session, envelope)

        amendment = self._amendments.start_amendment(
            order_id=order.id,
            lab_organization_id=self._organization_id,
            started_by_identity_id=self._identity_id,
            session=session,
        )
        items = self._items_by_code(session, order)
        # Reuses the Exchange's own shared effective-set query rather than a second copy, so what
        # this bridge considers currently-released is by construction what the release and
        # amendment machinery consider currently-released.
        effective_ids = [obs_id for _, obs_id in _effective_observation_rows(session, order.id)]
        by_code = {
            obs.source_code: obs
            for obs in session.query(LabObservation)
            .filter(LabObservation.id.in_(effective_ids))
            .all()
        }
        for obs in payload.observations:
            predecessor = by_code.get(obs.source_code)
            item = items.get(obs.source_code)
            if predecessor is None or item is None:
                # An amendment whose predecessor DHealth never saw. Refused rather than turned into
                # a first release: inventing the original would make the history claim something
                # was released that never was, and out-of-order arrival is a real, ordinary event
                # that the runtime should hold or quarantine, not paper over.
                raise ConnectApplyError(
                    FailureCategory.CONFLICT,
                    f"no released observation for {obs.source_code!r} to correct; the original "
                    "release has not been applied",
                )
            self._amendments.correct_observation(
                amendment_id=amendment.id,
                order_id=order.id,
                lab_order_item_id=item.id,
                lab_organization_id=self._organization_id,
                observation_id=predecessor.id,
                result_type=LabResultType(obs.result_type),
                source_code=obs.source_code,
                display_label_snapshot=obs.display_label,
                corrected_by_identity_id=self._identity_id,
                numeric_value=obs.numeric_value,
                numeric_unit=obs.numeric_unit,
                reference_range_low=obs.reference_range_low,
                reference_range_high=obs.reference_range_high,
                text_value=obs.text_value,
                session=session,
            )
        _, created = self._amendments.release_amendment(
            amendment_id=amendment.id,
            order_id=order.id,
            lab_organization_id=self._organization_id,
            released_by_identity_id=self._identity_id,
            session=session,
        )
        return ApplyOutcome(applied=True, replayed=not created)
