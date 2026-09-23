"""DH-CONNECT-AGENT-001 §83–§85 — assigning an order to a CONNECT laboratory queues it.

The hook is one function called from the two places an order becomes SELECTED, and the tests below
are mostly about what it *does not* do. A hook that fires too eagerly is a queue full of work for
laboratories that never asked for a connector; a hook that fires too rarely is a laboratory waiting
for an order nobody told it about. Both fail silently, which is why each case has its own test.

    F1  a CONNECT laboratory with a registered connector is notified
    F2  a NATIVE laboratory is not
    F3  a laboratory with no connector is not
    F4  a suspended installation is not
    F5  the notification rides the assignment's own transaction
    F6  assigning twice queues once
    F7  an offline runtime still receives the queued work when it returns  (§84)
    F8  the notification carries the provider's identifiers and the ordered codes, and no more
"""

from __future__ import annotations

import uuid

import pytest
from exchange_support import make_patient_reference, make_practitioner_reference, make_provider
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import Direction, Envelope, fingerprint
from daher_adapter.connect.models import ConnectorInstallation
from daher_adapter.connect.outbox_hook import (
    notify_connector_of_assignment,
    order_notification_event_id,
)
from daher_adapter.connect.repository import ConnectorInstallationRepository
from daher_adapter.connect.runtime_models import ConnectorDelivery, DeliveryState
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabOrder,
    LabOrderStatus,
    LabSelection,
    Organization,
    OrganizationStatus,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.models import utc_now

CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir"})
MAPPING_FP = fingerprint({"mapping_revision": [["synthetic-lis", "GLU", "cat-1", "GLU"]]})


@pytest.fixture
def actor(sessions: sessionmaker[Session]) -> uuid.UUID:
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://issuer.example", subject="platform-admin", display_name="P", email=None
    )
    return identity.id


def _lab(sessions, *, mode: ParticipationMode, name: str = "Laboratoire Central") -> uuid.UUID:
    with sessions.begin() as session:
        org = Organization(
            name=name,
            org_type=OrganizationType.LAB.value,
            status=OrganizationStatus.ACTIVE.value,
        )
        session.add(org)
        session.flush()
        organization_id = org.id
    FacilityEnrollmentRepository(sessions).create(
        organization_id=organization_id, participation_mode=mode
    )
    return organization_id


def _install(sessions, organization_id, actor) -> ConnectorInstallation:
    repository = ConnectorInstallationRepository(sessions)
    record = repository.register(
        organization_id=organization_id,
        connector_key="synthetic-lis",
        connector_version="0.1",
        source_system="synthetic-lis",
        interface_type="FHIR",
        configuration_fingerprint=CONFIG_FP,
        mapping_fingerprint=MAPPING_FP,
        actor_identity_id=actor,
    )
    return record


def _order(sessions, *, codes=("GLU",)) -> tuple[uuid.UUID, uuid.UUID]:
    """A real order awaiting lab selection. Returns ``(order_id, provider_id)``."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    provider = make_provider(organizations, f"Clinique {uuid.uuid4().hex[:6]}", operational=True)
    patient = make_patient_reference(
        PatientReferenceRepository(sessions, organizations), provider_org_id=provider.id
    )
    practitioner = make_practitioner_reference(
        PractitionerReferenceRepository(sessions), provider_org_id=provider.id
    )
    items = []
    for code in codes:
        entry, _ = catalog.create_entry(code=code, display_name=f"Analyse {code}")
        items.append(OrderItemInput(catalog_entry_id=entry.id))
    created = LabOrderRepository(sessions, organizations, catalog).create(
        idempotency_key=f"hook-{uuid.uuid4()}",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=items,
    )
    return created.order.id, provider.id


def _select(sessions, order_id: uuid.UUID, lab_id: uuid.UUID) -> None:
    """Select the laboratory the way the repository does, hook included.

    Calls the hook explicitly rather than going through ``LabSelectionRepository`` because the
    repository's own eligibility checks (directory profile, offerings, staffing) are a different
    subject with their own tests -- what is under test here is the notification.
    """
    with sessions.begin() as session:
        session.add(
            LabSelection(
                lab_order_id=order_id,
                lab_organization_id=lab_id,
                selected_at=utc_now(),
                selected_via_patient_access_grant_id=None,
            )
        )
        session.get(LabOrder, order_id).status = LabOrderStatus.SELECTED.value
        session.flush()
        notify_connector_of_assignment(session, lab_order_id=order_id, lab_organization_id=lab_id)


def _deliveries(sessions) -> list[ConnectorDelivery]:
    with sessions() as session:
        return list(session.scalars(select(ConnectorDelivery)).all())


def test_F1_a_connect_laboratory_with_a_connector_is_notified(sessions, actor):
    """The case the hook exists for."""
    lab_id = _lab(sessions, mode=ParticipationMode.CONNECT)
    _install(sessions, lab_id, actor)
    order_id, _ = _order(sessions)

    _select(sessions, order_id, lab_id)

    rows = _deliveries(sessions)
    assert len(rows) == 1
    assert rows[0].direction == Direction.DHEALTH_TO_CONNECTOR.value
    assert rows[0].message_type == "ORDER_NOTIFICATION"
    assert rows[0].lab_order_id == order_id
    assert rows[0].state == DeliveryState.PENDING.value


def test_F2_a_native_laboratory_is_not_notified(sessions):
    """A NATIVE laboratory works in DHealth directly and has no connector to tell.

    Silence, not an error: the assignment must succeed exactly as it did before this ticket.
    """
    lab_id = _lab(sessions, mode=ParticipationMode.NATIVE)
    order_id, _ = _order(sessions)

    _select(sessions, order_id, lab_id)

    assert _deliveries(sessions) == []


def test_F3_a_connect_laboratory_without_a_connector_is_not_notified(sessions):
    """Enrolled as CONNECT and nobody has registered a connector yet. Nothing to queue for."""
    lab_id = _lab(sessions, mode=ParticipationMode.CONNECT)
    order_id, _ = _order(sessions)

    _select(sessions, order_id, lab_id)

    assert _deliveries(sessions) == []


def test_F4_a_suspended_installation_is_not_notified(sessions, actor):
    """Suspension is a human decision to stop this connector carrying traffic.

    Queueing for it would pile up work nobody is coming to take -- the opposite of the *unhealthy*
    case, where the agent is expected back and the queue is what lets it catch up.
    """
    lab_id = _lab(sessions, mode=ParticipationMode.CONNECT)
    record = _install(sessions, lab_id, actor)
    ConnectorInstallationRepository(sessions).suspend(
        installation_id=record.id, actor_identity_id=actor
    )
    order_id, _ = _order(sessions)

    _select(sessions, order_id, lab_id)

    assert _deliveries(sessions) == []


def test_F5_the_notification_rides_the_assignments_transaction(sessions, actor):
    """Roll the selection back and the notification goes with it.

    The property that makes "assigned but never told" impossible. If the enqueue committed
    separately, this test would find an order notification for an order that was never selected.
    """
    lab_id = _lab(sessions, mode=ParticipationMode.CONNECT)
    _install(sessions, lab_id, actor)
    order_id, _ = _order(sessions)

    with pytest.raises(RuntimeError, match="deliberate"):
        with sessions.begin() as session:
            session.add(
                LabSelection(
                    lab_order_id=order_id,
                    lab_organization_id=lab_id,
                    selected_at=utc_now(),
                    selected_via_patient_access_grant_id=None,
                )
            )
            session.get(LabOrder, order_id).status = LabOrderStatus.SELECTED.value
            session.flush()
            notify_connector_of_assignment(
                session, lab_order_id=order_id, lab_organization_id=lab_id
            )
            raise RuntimeError("deliberate failure after both writes")

    assert _deliveries(sessions) == []
    with sessions() as session:
        assert session.get(LabOrder, order_id).status != LabOrderStatus.SELECTED.value


def test_F6_assigning_twice_queues_once(sessions, actor):
    """The hook keeps no memory of its own; the queue's idempotency key does the work.

    A retried request or a replayed call converges on the single row the first one created.
    """
    lab_id = _lab(sessions, mode=ParticipationMode.CONNECT)
    _install(sessions, lab_id, actor)
    order_id, _ = _order(sessions)

    _select(sessions, order_id, lab_id)
    with sessions.begin() as session:
        notify_connector_of_assignment(session, lab_order_id=order_id, lab_organization_id=lab_id)

    rows = _deliveries(sessions)
    assert len(rows) == 1
    assert rows[0].source_event_id == order_notification_event_id(order_id)


def test_F7_an_offline_runtime_does_not_cancel_or_drop_the_order(sessions, actor):
    """§84. The agent has never connected, and the order is queued and waiting anyway.

    Explicitly **not** a cancellation: the order stays SELECTED, the delivery stays PENDING, and a
    connector that comes back tomorrow finds the work. Inventing a cancellation because a runtime is
    offline would be DHealth making a clinical decision on a partner's behalf, which §56 forbids and
    §84 names directly.
    """
    lab_id = _lab(sessions, mode=ParticipationMode.CONNECT)
    _install(sessions, lab_id, actor)
    order_id, _ = _order(sessions)

    _select(sessions, order_id, lab_id)

    rows = _deliveries(sessions)
    assert len(rows) == 1
    assert rows[0].state == DeliveryState.PENDING.value
    assert rows[0].dead_lettered_at is None
    with sessions() as session:
        assert session.get(LabOrder, order_id).status == LabOrderStatus.SELECTED.value


def test_F8_the_notification_carries_the_ordered_codes_and_nothing_extra(sessions, actor):
    """The laboratory learns what was ordered and who the provider says the patient is.

    And nothing else -- ADR-056 decision 5. The envelope is re-parsed rather than inspected as a
    dict, so the assertion is against the same validation a connector would apply.
    """
    lab_id = _lab(sessions, mode=ParticipationMode.CONNECT)
    _install(sessions, lab_id, actor)
    order_id, provider_id = _order(sessions, codes=("GLU", "CRP"))

    _select(sessions, order_id, lab_id)

    envelope = Envelope.model_validate_json(_deliveries(sessions)[0].payload)
    payload = envelope.payload
    assert set(payload.catalog_codes) == {"GLU", "CRP"}
    # The provider's own scoped identity, unchanged and not re-scoped to the laboratory.
    assert payload.patient.source_organization_id == provider_id
    assert payload.practitioner.source_organization_id == provider_id
    # And DHealth is the sender, so the two timestamps coincide honestly.
    assert envelope.source_occurred_at == envelope.sent_at
    assert envelope.lab_order_id == order_id
