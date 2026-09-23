"""DH-CONNECT-AGENT-001 correction — a professional release may outlive connector availability.

Two failures, both of which lost clinical work silently, and both of which only a durable record
can fix.

**Suspension deleted the obligation.** The hook queued only for a ``REGISTERED`` installation, so:
order submitted, connector suspended for an afternoon, laboratory releases, nothing queued,
connector restored -- and the result is simply gone from Connect delivery. Nothing anywhere recorded
that a provider was waiting. Suspension means *do not carry traffic now*; it never meant *forget the
work that became due while you were away*.

**A build failure produced a log line.** If the notification could not be constructed, the release
committed, a warning was logged and the function returned. A log line is not a state. After a
restart DHealth had no queryable answer to "which released results were never queued, why, and can
they be recovered" -- the three questions an operator actually has.

The invariant these tests pin:

    correlated CONNECT provider + professional release
      = a durable delivery, **or** a durable record that one is owed and why

and never a release that committed with nothing anywhere recording that a provider is waiting. The
release itself always commits: refusing a professional's clinical act because an integration could
not be arranged inverts the priority, and the laboratory has done the work either way.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from exchange_auth_support import ISSUER
from exchange_support import make_lab, make_offering, make_provider
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import Direction, fingerprint
from daher_adapter.connect.correlation import CorrelationNamespace, CorrelationRepository
from daher_adapter.connect.delivery import DeliveryRepository
from daher_adapter.connect.reconciliation import (
    reconcile_delivery_obligations,
    unresolved_obligations,
)
from daher_adapter.connect.repository import ConnectorInstallationRepository
from daher_adapter.connect.result_hook import UNRESOLVED_SOURCE_SYSTEM
from daher_adapter.connect.runtime_models import (
    ConnectorDelivery,
    ConnectorDeliveryObligation,
    DeliveryState,
    ObligationState,
)
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabObservation,
    LabOrderItem,
    LabResultType,
    ParticipationMode,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
    _effective_observation_rows,
)
from daher_adapter.exchange.selection import LabSelectionRepository

SOURCE_SYSTEM = "medsys"
EXTERNAL_ORDER = "ORDER-123"


@pytest.fixture
def world(sessions: sessionmaker[Session]):
    """A CONNECT provider with a live connector, a native laboratory that can finish work, and one
    submitted-and-correlated order ready to be released."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    provider = make_provider(organizations, "Clinique de la Paix", operational=False)
    FacilityEnrollmentRepository(sessions).create(
        organization_id=provider.id, participation_mode=ParticipationMode.CONNECT
    )
    lab = make_lab(organizations, "Laboratoire Central", operational=True)
    LabDirectoryProfileRepository(sessions, organizations).upsert(
        lab_organization_id=lab.id,
        public_address="Boulevard de la République",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    entry, _ = catalog.create_entry(code="GLU", display_name="Glycémie à jeun")
    offering, _ = make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    price, _ = catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer=ISSUER, subject="platform-admin", display_name="Platform", email=None
    )
    installations = ConnectorInstallationRepository(sessions)
    installation = installations.register(
        organization_id=provider.id,
        connector_key="synthetic-emr",
        connector_version="1.0",
        source_system=SOURCE_SYSTEM,
        interface_type="FHIR",
        configuration_fingerprint=fingerprint({"endpoint": "https://medsys.invalid"}),
        mapping_fingerprint=fingerprint({"mapping_revision": [[SOURCE_SYSTEM, "GLU"]]}),
        actor_identity_id=identity.id,
    )

    patient = PatientReferenceRepository(sessions, organizations).get_or_create(
        source_organization_id=provider.id,
        source_system=SOURCE_SYSTEM,
        source_identity_system="urn:medsys:mrn",
        source_identity_value="MRN-1",
    )
    practitioner = PractitionerReferenceRepository(sessions).get_or_create(
        source_organization_id=provider.id,
        source_system=SOURCE_SYSTEM,
        source_identity_system="urn:medsys:staff",
        source_identity_value="DR-1",
    )
    created = LabOrderRepository(sessions, organizations, catalog).create(
        idempotency_key=f"ob-{uuid.uuid4()}",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )
    namespace = CorrelationNamespace(organization_id=provider.id, source_system=SOURCE_SYSTEM)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=namespace,
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=created.order.id,
            created_by_installation_id=installation.id,
        )

    return {
        "sessions": sessions,
        "organizations": organizations,
        "provider_id": provider.id,
        "lab_id": lab.id,
        "installation_id": installation.id,
        "installations": installations,
        "actor_id": identity.id,
        "price_id": price.id,
        "order_id": created.order.id,
        "namespace": namespace,
    }


def _prepare_and_enter(world, *, text_value: str | None = None) -> None:
    """Everything up to but not including the release: selection, acceptance, collection, entry."""
    sessions = world["sessions"]
    organizations = world["organizations"]
    workflow = LabWorkflowRepository(sessions, organizations)
    lab_id = world["lab_id"]
    order_id = world["order_id"]
    actor = world["actor_id"]

    LabSelectionRepository(sessions, organizations).select(
        lab_order_id=order_id,
        lab_organization_id=lab_id,
        expected_price_ids=frozenset({world["price_id"]}),
    )
    workflow.accept(order_id=order_id, lab_organization_id=lab_id)
    workflow.record_collection(order_id=order_id, lab_organization_id=lab_id)
    workflow.start_processing(order_id=order_id, lab_organization_id=lab_id)

    with sessions() as session:
        item_id = session.scalar(
            select(LabOrderItem.id).where(LabOrderItem.lab_order_id == order_id)
        )
    results = LabResultRepository(sessions)
    numeric = text_value is None
    results.record_observation(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC if numeric else LabResultType.TEXT,
        source_code="GLU",
        display_label_snapshot="Glycémie à jeun",
        recorded_by_identity_id=actor,
        numeric_value=Decimal("5.4") if numeric else None,
        numeric_unit="mmol/L" if numeric else None,
        text_value=text_value,
    )
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        completed_by_identity_id=actor,
    )


def _release_only(world) -> None:
    """The release step alone. Separated so a test can break something in between."""
    LabResultReleaseRepository(world["sessions"]).release(
        order_id=world["order_id"],
        lab_organization_id=world["lab_id"],
        released_by_identity_id=world["actor_id"],
    )


def _release(world) -> None:
    """The laboratory's own workflow, native, end to end through the Exchange's repositories."""
    _prepare_and_enter(world)
    _release_only(world)


def _amend(world, *, label: str = "Glycémie à jeun") -> None:
    sessions = world["sessions"]
    amendments = LabResultAmendmentRepository(sessions)
    lab_id = world["lab_id"]
    order_id = world["order_id"]
    actor = world["actor_id"]

    amendment = amendments.start_amendment(
        order_id=order_id, lab_organization_id=lab_id, started_by_identity_id=actor
    )
    with sessions() as session:
        item_id = session.scalar(
            select(LabOrderItem.id).where(LabOrderItem.lab_order_id == order_id)
        )
        predecessor_id = _effective_observation_rows(session, order_id)[0][1]
    amendments.correct_observation(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        observation_id=predecessor_id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot=label,
        corrected_by_identity_id=actor,
        numeric_value=Decimal("7.1"),
        numeric_unit="mmol/L",
    )
    amendments.release_amendment(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=actor,
    )


def _obligations(sessions) -> list[ConnectorDeliveryObligation]:
    with sessions() as session:
        return list(
            session.scalars(
                select(ConnectorDeliveryObligation).order_by(ConnectorDeliveryObligation.created_at)
            )
        )


def _deliveries(sessions) -> list[ConnectorDelivery]:
    with sessions() as session:
        return list(
            session.scalars(
                select(ConnectorDelivery).where(
                    ConnectorDelivery.message_type.in_(
                        ("RESULT_NOTIFICATION", "AMENDMENT_NOTIFICATION")
                    )
                )
            )
        )


# ── suspension ───────────────────────────────────────────────────────────────────────────────


class TestSuspensionDoesNotDeleteTheObligation:
    """§8. SD1–SD8, as one journey, because each step only means something after the one before."""

    def test_SD_a_result_released_during_a_suspension_is_still_owed_and_still_queued(
        self, world, sessions
    ):
        """SD1–SD7. The whole failure, and the whole fix.

        The delivery is queued **against the suspended installation** rather than withheld until it
        returns. That is the part worth being deliberate about: a suspended installation cannot
        authenticate, so nothing can claim the message and it waits exactly as long as the
        suspension lasts. Withholding it instead would mean the moment of restoration had to be
        noticed by something, and anything that has to be noticed is something that can be missed.
        """
        # SD1: order submitted and correlated -- the fixture did that.
        # SD2: the connector is suspended before the laboratory releases.
        world["installations"].suspend(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )

        # SD3: the release succeeds. A clinical act does not fail for an integration reason.
        _release(world)

        # SD4: a durable obligation exists...
        obligations = _obligations(sessions)
        assert len(obligations) == 1
        assert obligations[0].state == ObligationState.QUEUED.value
        assert obligations[0].intended_message_type == "RESULT_NOTIFICATION"
        assert obligations[0].source_system == SOURCE_SYSTEM

        # ...and it is queued rather than delivered. SD5: nothing reaches the partner while
        # suspended, because nothing can claim a suspended installation's work.
        deliveries = _deliveries(sessions)
        assert len(deliveries) == 1
        assert deliveries[0].state == DeliveryState.PENDING.value
        assert deliveries[0].connector_installation_id == world["installation_id"]

        # SD6: restored. SD7: exactly one delivery, not a second one created on the way back.
        world["installations"].restore(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        report = reconcile_delivery_obligations(sessions)
        assert report.attempted == 0, "nothing was left owed, so reconciliation has no work"
        assert len(_deliveries(sessions)) == 1

    def test_SD8_an_amendment_released_during_a_suspension_behaves_identically(
        self, world, sessions
    ):
        """A correction is the message whose loss matters most, so it gets the same guarantee.

        Two obligations and two deliveries, distinguished by release version -- the amendment is not
        a replay of the original and must not converge on its delivery.
        """
        _release(world)
        world["installations"].suspend(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        _amend(world)

        obligations = _obligations(sessions)
        assert [o.intended_message_type for o in obligations] == [
            "RESULT_NOTIFICATION",
            "AMENDMENT_NOTIFICATION",
        ]
        assert {o.state for o in obligations} == {ObligationState.QUEUED.value}
        assert len(_deliveries(sessions)) == 2

    def test_SD9_suspension_before_and_after_makes_no_difference_to_what_is_owed(
        self, world, sessions
    ):
        """The obligation is created by the release, not by the connector's mood at the time."""
        world["installations"].suspend(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        _release(world)
        world["installations"].restore(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        _amend(world)

        assert len(_obligations(sessions)) == 2
        assert len(_deliveries(sessions)) == 2


# ── retirement ───────────────────────────────────────────────────────────────────────────────


class TestRetirementLeavesTheObligationUnresolved:
    """§9. A retired connector is not a reason to lose a diagnostic result."""

    def test_RT1_a_release_with_no_successor_is_recorded_as_owed_and_unresolved(
        self, world, sessions
    ):
        """Retired, nothing registered in its place. The report is owed and says so.

        Not dead-lettered, not dropped, and **not** handed to some other connector. §56: an
        unresolved row an operator has to look at beats a cancellation DHealth invented.
        """
        world["installations"].retire(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        _release(world)

        obligations = _obligations(sessions)
        assert len(obligations) == 1
        assert obligations[0].state == ObligationState.PENDING.value
        assert obligations[0].failure_category == "REMOTE_UNAVAILABLE"
        assert obligations[0].resolved_at is None
        assert _deliveries(sessions) == []

        with sessions() as session:
            assert len(unresolved_obligations(session)) == 1

    def test_RT2_a_successor_for_the_same_source_system_discharges_it(self, world, sessions):
        """The preferred policy of §9, made real: register the successor, reconcile, done.

        The successor is a different installation with its own qualification to earn. What it
        inherits is the *namespace*, which is why the obligation it discharges is one created before
        it existed.
        """
        world["installations"].retire(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        _release(world)
        assert _obligations(sessions)[0].state == ObligationState.PENDING.value

        successor = world["installations"].register(
            organization_id=world["provider_id"],
            connector_key="synthetic-emr",
            connector_version="2.0",
            source_system=SOURCE_SYSTEM,
            interface_type="FHIR",
            configuration_fingerprint=fingerprint({"endpoint": "https://medsys.invalid/v2"}),
            mapping_fingerprint=fingerprint({"mapping_revision": [[SOURCE_SYSTEM, "GLU"]]}),
            actor_identity_id=world["actor_id"],
        )

        report = reconcile_delivery_obligations(sessions)
        assert len(report.queued) == 1

        obligations = _obligations(sessions)
        assert obligations[0].state == ObligationState.QUEUED.value
        assert obligations[0].resolved_at is not None
        deliveries = _deliveries(sessions)
        assert len(deliveries) == 1
        assert deliveries[0].connector_installation_id == successor.id

    def test_RT3_a_successor_for_a_different_source_system_does_not_discharge_it(
        self, world, sessions
    ):
        """§6 at the delivery layer. Replacing MedSys with Epic does not hand Epic MedSys's reports.

        The obligation stays owed, which is the correct answer: the report is real, the provider's
        old system is gone, and what to do about that is a decision for a human with context --
        never a routing guess.
        """
        world["installations"].retire(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        _release(world)
        world["installations"].register(
            organization_id=world["provider_id"],
            connector_key="synthetic-emr",
            connector_version="1.0",
            source_system="epic",
            interface_type="FHIR",
            configuration_fingerprint=fingerprint({"endpoint": "https://epic.invalid"}),
            mapping_fingerprint=fingerprint({"mapping_revision": [["epic", "GLU"]]}),
            actor_identity_id=world["actor_id"],
        )

        report = reconcile_delivery_obligations(sessions)
        assert report.queued == ()
        assert len(report.still_unresolved) == 1
        assert _deliveries(sessions) == [], "no report may be routed to a system that did not order"

    def test_RT4_a_delivery_orphaned_by_a_later_retirement_is_re_targeted_not_left_stranded(
        self, world, sessions
    ):
        """The state a legal sequence of operator actions reaches, which nothing else catches.

        Suspend, release -- the delivery is correctly queued against the suspended installation and
        the obligation reads QUEUED. Then the operator gives up on that connector and retires it,
        registering a successor. The delivery is now attached to a principal that will never
        authenticate again, so it waits forever *while the obligation says it was discharged*: the
        exact "release committed and nothing will ever carry it" state §11 forbids, reached without
        anybody doing anything wrong.

        So the obligation is owed again, the successor gets its own delivery, and the orphaned row
        is left where it is (§51) rather than deleted or repointed.
        """
        world["installations"].suspend(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        _release(world)
        assert _obligations(sessions)[0].state == ObligationState.QUEUED.value

        world["installations"].retire(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        with sessions() as session:
            assert len(unresolved_obligations(session)) == 1, (
                "an operator reading what is owed must see this one, not a row marked QUEUED"
            )

        successor = world["installations"].register(
            organization_id=world["provider_id"],
            connector_key="synthetic-emr",
            connector_version="2.0",
            source_system=SOURCE_SYSTEM,
            interface_type="FHIR",
            configuration_fingerprint=fingerprint({"endpoint": "https://medsys.invalid/v2"}),
            mapping_fingerprint=fingerprint({"mapping_revision": [[SOURCE_SYSTEM, "GLU"]]}),
            actor_identity_id=world["actor_id"],
        )
        report = reconcile_delivery_obligations(sessions)
        assert len(report.queued) == 1

        obligation = _obligations(sessions)[0]
        assert obligation.state == ObligationState.QUEUED.value
        deliveries = {d.connector_installation_id: d for d in _deliveries(sessions)}
        assert set(deliveries) == {world["installation_id"], successor.id}, (
            "the orphaned delivery is kept as history; the successor gets its own"
        )
        assert obligation.connector_delivery_id == deliveries[successor.id].id

    def test_RT5_an_orphaned_obligation_with_no_successor_stops_claiming_to_be_queued(
        self, world, sessions
    ):
        """The same sequence without a successor. Demotion is not conditional on the fix working.

        If re-targeting only corrected the record when it could also solve the problem, the worst
        case -- retired, nothing registered in its place -- would be the one still reading QUEUED.
        """
        world["installations"].suspend(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        _release(world)
        world["installations"].retire(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )

        report = reconcile_delivery_obligations(sessions)
        assert report.queued == ()
        assert len(report.still_unresolved) == 1

        obligation = _obligations(sessions)[0]
        assert obligation.state == ObligationState.PENDING.value
        assert obligation.failure_category == "REMOTE_UNAVAILABLE"
        assert obligation.resolved_at is None

    def test_RT6_a_delivered_message_is_not_re_sent_when_its_installation_retires(
        self, world, sessions
    ):
        """The other half of the rule: retirement only reopens work the provider never received.

        A connector that filed the result and was then retired has discharged the obligation, and
        re-queueing it against a successor would file the same report twice.
        """
        _release(world)
        # Through the queue's own claim-and-confirm, not by writing DELIVERED onto the row: the
        # state has invariants (``ck_connector_deliveries_delivered_at``), and a test that set the
        # column directly would be asserting about a row the runtime cannot actually produce.
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            claimed = deliveries.claim(
                session,
                installation_id=world["installation_id"],
                direction=Direction.DHEALTH_TO_CONNECTOR,
                runtime_instance_id="rt6",
            )
            deliveries.mark_delivered(session, delivery=claimed[0], runtime_instance_id="rt6")
        world["installations"].retire(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        world["installations"].register(
            organization_id=world["provider_id"],
            connector_key="synthetic-emr",
            connector_version="2.0",
            source_system=SOURCE_SYSTEM,
            interface_type="FHIR",
            configuration_fingerprint=fingerprint({"endpoint": "https://medsys.invalid/v2"}),
            mapping_fingerprint=fingerprint({"mapping_revision": [[SOURCE_SYSTEM, "GLU"]]}),
            actor_identity_id=world["actor_id"],
        )

        report = reconcile_delivery_obligations(sessions)
        assert report.attempted == 0
        assert len(_deliveries(sessions)) == 1


# ── build failure ────────────────────────────────────────────────────────────────────────────


def _empty_every_observation_label(sessions) -> None:
    """Make the released report inexpressible as a Connect message, for a realistic reason.

    ``ResultObservation.display_label`` is ``min_length=1``, so a blank snapshot is refused by the
    contract. That is the shape of a real fault -- a catalog entry whose label never got filled in,
    reaching the boundary through a path that did not check -- rather than a monkeypatched builder,
    which would have tested the ``except`` clause instead of the behaviour.
    """
    with sessions.begin() as session:
        for row in session.scalars(select(LabObservation)):
            row.display_label_snapshot = ""


def _restore_every_observation_label(sessions) -> None:
    with sessions.begin() as session:
        for row in session.scalars(select(LabObservation)):
            row.display_label_snapshot = "Glycémie à jeun"


class TestBuildFailureIsDurable:
    """§10–§13. The release commits, the failure becomes a row, and reconciliation recovers it."""

    def test_BF1_the_release_commits_and_the_failure_is_a_row_not_a_log_line(self, world, sessions):
        """The invariant, stated as one assertion pair.

        Before this, the second half was a ``logger.warning`` -- so a restart left DHealth unable to
        answer which released results had never been queued. The release committing is not the
        change; the release committing *with a durable record beside it* is.
        """
        from daher_adapter.exchange.models import LabResultRelease

        _prepare_and_enter(world)
        _empty_every_observation_label(sessions)
        _release_only(world)

        with sessions() as session:
            releases = list(session.scalars(select(LabResultRelease)))
        assert len(releases) == 1, "a professional released this; that stands regardless"

        obligations = _obligations(sessions)
        assert len(obligations) == 1
        assert obligations[0].state == ObligationState.FAILED.value
        assert obligations[0].failure_category == "VALIDATION"
        assert obligations[0].resolved_at is None
        assert _deliveries(sessions) == []

    def test_BF2_the_recorded_reason_names_the_field_and_never_the_value(self, world, sessions):
        """§57. A validation error quotes the input that failed, and that input is patient data.

        What survives into the operational record is the shape of the fault -- which field, what
        kind of error -- which is what an operator needs to fix a configuration anyway. Pinned as a
        test because the natural implementation is ``str(error)``, and it would leak on the day a
        text result failed instead of a label.
        """
        # A **TEXT** result, so the observation carries a free-text clinical value that a naive
        # ``str(error)`` would quote back. A NUMERIC one cannot hold text at all --
        # ``ck_exchange_lab_observations_value_matches_type`` refuses it -- which this test
        # discovered.
        _prepare_and_enter(world, text_value="PATIENT SECRET NARRATIVE")
        _empty_every_observation_label(sessions)
        _release_only(world)

        detail = _obligations(sessions)[0].failure_detail
        assert "display_label" in detail, "an operator must learn which field"
        assert "PATIENT SECRET NARRATIVE" not in detail
        assert "Glycémie" not in detail
        assert len(detail) <= 512

    def test_BF3_fixing_the_cause_lets_reconciliation_queue_the_exact_release_version(
        self, world, sessions
    ):
        """§13. Rebuilt from the immutable release version, not from a stored copy of the message.

        The obligation holds no report. What it holds is a pointer to the release version whose
        manifest already *is* the clinical truth, so the message reconciliation produces is the
        report as released -- which is also why this works months later and after a restart.
        """
        _prepare_and_enter(world)
        _empty_every_observation_label(sessions)
        _release_only(world)
        failed = _obligations(sessions)[0]
        assert failed.state == ObligationState.FAILED.value
        failed_id, version_id = failed.id, failed.release_version_id

        _restore_every_observation_label(sessions)
        report = reconcile_delivery_obligations(sessions)
        assert len(report.queued) == 1

        obligations = _obligations(sessions)
        assert obligations[0].id == failed_id, "the same obligation, moved -- not a second row"
        assert obligations[0].state == ObligationState.QUEUED.value
        assert obligations[0].release_version_id == version_id
        deliveries = _deliveries(sessions)
        assert len(deliveries) == 1
        assert deliveries[0].message_type == "RESULT_NOTIFICATION"

    def test_BF4_an_amendment_whose_notification_fails_recovers_the_same_way(self, world, sessions):
        """The correction, which is the message whose loss matters most, gets the same treatment.

        Two obligations afterwards, both QUEUED, and the amendment's delivery is distinct from the
        original's -- a correction that converged on the original's delivery would be a correction
        nobody ever saw.
        """
        _release(world)
        assert _obligations(sessions)[0].state == ObligationState.QUEUED.value

        # The correction writes its *own* observation, so blanking the earlier ones would not reach
        # it -- an amendment's manifest is the corrected rows, not the originals. Found by this test
        # failing for exactly that reason.
        _amend(world, label="")
        amendment_obligation = [
            o for o in _obligations(sessions) if o.intended_message_type == "AMENDMENT_NOTIFICATION"
        ][0]
        assert amendment_obligation.state == ObligationState.FAILED.value

        _restore_every_observation_label(sessions)
        report = reconcile_delivery_obligations(sessions)
        assert len(report.queued) == 1

        obligations = _obligations(sessions)
        assert {o.state for o in obligations} == {ObligationState.QUEUED.value}, (
            "both the original and its correction are queued"
        )
        deliveries = _deliveries(sessions)
        assert sorted(d.message_type for d in deliveries) == [
            "AMENDMENT_NOTIFICATION",
            "RESULT_NOTIFICATION",
        ]

    def test_BF5_reconciliation_is_idempotent(self, world, sessions):
        """Run twice, one delivery. The idempotency key is deterministic in order and version, so a
        second pass converges on the row the first created rather than duplicating a report."""
        _prepare_and_enter(world)
        _empty_every_observation_label(sessions)
        _release_only(world)
        _restore_every_observation_label(sessions)

        first = reconcile_delivery_obligations(sessions)
        second = reconcile_delivery_obligations(sessions)
        assert len(first.queued) == 1
        assert second.attempted == 0, "a queued obligation is no longer owed"
        assert len(_deliveries(sessions)) == 1

    def test_BF6_an_obligation_that_still_cannot_be_discharged_is_left_owed(self, world, sessions):
        """Reconciliation never resolves what it could not do, and never deletes it either."""
        _prepare_and_enter(world)
        _empty_every_observation_label(sessions)
        _release_only(world)

        report = reconcile_delivery_obligations(sessions)
        assert report.queued == ()
        assert len(report.still_unresolved) == 1
        obligations = _obligations(sessions)
        assert len(obligations) == 1
        assert obligations[0].state == ObligationState.FAILED.value
        assert obligations[0].resolved_at is None

    def test_BF7_a_bounded_pass_rotates_instead_of_grinding_on_the_oldest(self, world, sessions):
        """Fairness, which a bounded pass has to have on purpose or it does not have at all.

        The heartbeat reconciles five at a time. Ordered by age, a facility holding five
        permanently stuck obligations -- a build failure nobody has fixed, a source system that will
        never get a connector -- would re-attempt those same five forever and never reach a newer
        one that could be discharged. The claim "a successor's first heartbeat queues what its
        predecessor left owed" would then be false for precisely the facility that needed it.

        So the pass takes the **least recently attempted**. Driven here with ``limit=1``, which is
        the smallest bound that can express the difference: pass one must fail on the older
        obligation, and pass two must reach the newer one rather than returning to it.
        """
        # An original release whose notification cannot be built, and stays unbuildable.
        _prepare_and_enter(world)
        _empty_every_observation_label(sessions)
        _release_only(world)
        # ...and an amendment that can be: the correction writes its own observation, so blanking
        # the released rows above never reached it.
        _amend(world)

        obligations = _obligations(sessions)
        assert len(obligations) == 2
        older, newer = obligations
        assert older.state == ObligationState.FAILED.value
        assert newer.state == ObligationState.QUEUED.value

        # Put the amendment back in the owed set the way a real fault would: its delivery is
        # orphaned by retiring the installation it was queued against, and no successor exists yet.
        world["installations"].retire(
            installation_id=world["installation_id"], actor_identity_id=world["actor_id"]
        )
        successor = world["installations"].register(
            organization_id=world["provider_id"],
            connector_key="synthetic-emr",
            connector_version="2.0",
            source_system=SOURCE_SYSTEM,
            interface_type="FHIR",
            configuration_fingerprint=fingerprint({"endpoint": "https://medsys.invalid/v2"}),
            mapping_fingerprint=fingerprint({"mapping_revision": [[SOURCE_SYSTEM, "GLU"]]}),
            actor_identity_id=world["actor_id"],
        )

        first = reconcile_delivery_obligations(sessions, limit=1)
        assert first.attempted == 1
        second = reconcile_delivery_obligations(sessions, limit=1)
        assert second.attempted == 1
        assert second.queued != first.queued or second.still_unresolved != first.still_unresolved, (
            "two passes that attempted the same row are the starvation this ordering prevents"
        )

        by_id = {row.id: row for row in _obligations(sessions)}
        assert by_id[older.id].state == ObligationState.FAILED.value, "still unbuildable"
        assert by_id[newer.id].state == ObligationState.QUEUED.value
        assert by_id[newer.id].connector_delivery_id is not None
        installations = {d.connector_installation_id for d in _deliveries(sessions)}
        assert successor.id in installations


class TestAnAmbiguousCorrelationIsOwedAndNotGuessed:
    """§43 at the delivery layer: the refusal to choose has to leave something behind.

    Two source systems at one facility both claiming to have ordered this. There is no rule that
    makes one of them more likely to be right, and picking either files a clinical report into a
    system that did not ask for it. The honest outcome is an obligation an operator has to settle --
    and it has to be an obligation, because the alternative is that the most dangerous case is the
    one that leaves no trace.
    """

    def _claim_the_order_from_a_second_system(self, world, sessions) -> None:
        with sessions.begin() as session:
            CorrelationRepository().bind_order(
                session,
                namespace=CorrelationNamespace(
                    organization_id=world["provider_id"], source_system="epic"
                ),
                external_order_id="EPIC-ORDER-9",
                lab_order_id=world["order_id"],
                created_by_installation_id=world["installation_id"],
            )

    def test_CF1_the_release_commits_and_the_obligation_says_it_cannot_be_placed(
        self, world, sessions
    ):
        """Owed, unresolved, and carrying no guess about which namespace it belongs to."""
        self._claim_the_order_from_a_second_system(world, sessions)
        _release(world)

        obligations = _obligations(sessions)
        assert len(obligations) == 1
        assert obligations[0].state == ObligationState.PENDING.value
        assert obligations[0].failure_category == "CONFLICT"
        assert obligations[0].source_system == UNRESOLVED_SOURCE_SYSTEM, (
            "a real system name here would be the guess this path exists to refuse"
        )
        assert obligations[0].intended_message_type == "RESULT_NOTIFICATION"
        assert obligations[0].resolved_at is None
        assert _deliveries(sessions) == [], "no report may be filed while it is unclear who asked"

    def test_CF2_an_amendment_records_that_it_was_an_amendment_that_could_not_be_placed(
        self, world, sessions
    ):
        """The message type is read before the namespace, because the operator needs both.

        An obligation saying ``RESULT_NOTIFICATION`` for a correction sends whoever picks it up
        looking for the wrong thing -- and a correction is the message whose loss matters most.
        """
        _release(world)
        self._claim_the_order_from_a_second_system(world, sessions)
        _amend(world)

        amendment = _obligations(sessions)[1]
        assert amendment.intended_message_type == "AMENDMENT_NOTIFICATION"
        assert amendment.state == ObligationState.PENDING.value
        assert amendment.failure_category == "CONFLICT"

    def test_CF3_reconciliation_does_not_resolve_it_and_does_not_delete_it(self, world, sessions):
        """Nothing in the runtime deletes a correlation, so this stays owed until a human acts.

        Which is the point: the recovery path must not quietly "fix" an ambiguity by picking one.
        """
        self._claim_the_order_from_a_second_system(world, sessions)
        _release(world)

        report = reconcile_delivery_obligations(sessions)
        assert report.queued == ()
        assert len(report.still_unresolved) == 1

        with sessions() as session:
            owed = unresolved_obligations(session)
        assert len(owed) == 1
        assert owed[0].state == ObligationState.PENDING.value
        assert owed[0].failure_category == "CONFLICT"
        assert owed[0].source_system == UNRESOLVED_SOURCE_SYSTEM
        assert _deliveries(sessions) == []


# ── what is deliberately not owed ────────────────────────────────────────────────────────────


def test_NO1_a_natively_placed_order_creates_no_obligation(world, sessions):
    """Silence has exactly one cause and it is this one.

    The EMR never asked for this order and has no identifier to file it against, so nothing is owed
    -- and an obligation row here would be DHealth inventing a debt to a system that is not waiting.
    """
    organizations = world["organizations"]
    sessions_ = world["sessions"]
    catalog = CatalogRepository(sessions_, organizations)
    patient = PatientReferenceRepository(sessions_, organizations).get_or_create(
        source_organization_id=world["provider_id"],
        source_system="dhealth",
        source_identity_system="urn:dhealth:native",
        source_identity_value="NATIVE-1",
    )
    practitioner = PractitionerReferenceRepository(sessions_).get_or_create(
        source_organization_id=world["provider_id"],
        source_system="dhealth",
        source_identity_system="urn:dhealth:native",
        source_identity_value="NATIVE-DR",
    )
    with sessions_() as session:
        entry_id = session.scalar(
            select(LabOrderItem.catalog_entry_id).where(
                LabOrderItem.lab_order_id == world["order_id"]
            )
        )
    native = LabOrderRepository(sessions_, organizations, catalog).create(
        idempotency_key=f"native-{uuid.uuid4()}",
        provider_organization_id=world["provider_id"],
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry_id)],
    )

    world["order_id"] = native.order.id
    _release(world)

    assert _obligations(sessions) == []
    assert _deliveries(sessions) == []
