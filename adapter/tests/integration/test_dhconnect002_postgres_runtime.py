"""DH-CONNECT-AGENT-001 §101 real PostgreSQL qualification: the runtime schema and its one
concurrency property SQLite structurally cannot test.

Most files in this directory make the general "production is PostgreSQL" argument. This one has a
specific claim that the SQLite suite is *incapable* of evaluating, not merely unlikely to:

*   ``DeliveryRepository.claim`` uses ``SELECT ... FOR UPDATE SKIP LOCKED``. **SQLite ignores both
    clauses silently.** Every claim test in ``tests/test_connect_runtime.py`` therefore passes
    whether or not that line exists, and two agents racing for the same message would be caught by
    nothing. Two real concurrent transactions against real PostgreSQL are the only way to know.
*   ``uq_connector_deliveries_replay`` and ``uq_connector_message_receipts_event`` are what make
    at-least-once delivery safe. A unique index that did not apply would look identical in every
    single-threaded test.
*   ``uq_connector_installations_service_client`` is a **partial** unique index over a nullable
    column, and it is the structural half of §8: one OIDC client authenticates for exactly one
    installation, including across retired rows.
*   ``0029_connect_runtime`` drops and recreates the audit-vocabulary CHECK. SQLite has no
    ``ALTER TABLE ... DROP CONSTRAINT``, so that branch is never executed by the SQLite suite.

**Manual / opt-in only**, like every other live-infrastructure test here: skipped by default
(including in CI) unless every ``DHCONNECT002_PG_*`` variable is set. Never point it at a real
facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHCONNECT002_PG_HOST=127.0.0.1 \\
    DHCONNECT002_PG_PORT=5432 \\
    DHCONNECT002_PG_USER=dhconnect001 \\
    DHCONNECT002_PG_PASSWORD=dhconnect001 \\
    DHCONNECT002_PG_DATABASE=dhconnect001_qual \\
        pytest adapter/tests/integration/test_dhconnect002_postgres_runtime.py -v

The target database must already be migrated through ``0029_connect_runtime``; this file does not
run migrations. No credential belonging to any external EMR/LIS appears here.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from exchange_support import make_patient_reference, make_practitioner_reference, make_provider
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    Direction,
    Envelope,
    FailureCategory,
    OrderNotification,
    ScopedExternalId,
    SourcePractitionerRef,
    fingerprint,
)
from daher_adapter.connect.delivery import DeliveryRepository, DurableReceiptStore
from daher_adapter.connect.models import ConnectorInstallation, ConnectorInstallationStatus
from daher_adapter.connect.runtime_models import (
    ConnectorDelivery,
    ConnectorMessageReceipt,
    ConnectorRuntimeInstance,
    DeliveryState,
)
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository

_REQUIRED = (
    "DHCONNECT002_PG_HOST",
    "DHCONNECT002_PG_PORT",
    "DHCONNECT002_PG_USER",
    "DHCONNECT002_PG_PASSWORD",
    "DHCONNECT002_PG_DATABASE",
)

pytestmark = pytest.mark.skipif(
    any(not os.environ.get(name) for name in _REQUIRED),
    reason="requires a disposable PostgreSQL instance; set every DHCONNECT002_PG_* variable",
)


@pytest.fixture(scope="module")
def engine():
    url = (
        f"postgresql+psycopg://{os.environ['DHCONNECT002_PG_USER']}:"
        f"{os.environ['DHCONNECT002_PG_PASSWORD']}@{os.environ['DHCONNECT002_PG_HOST']}:"
        f"{os.environ['DHCONNECT002_PG_PORT']}/{os.environ['DHCONNECT002_PG_DATABASE']}"
    )
    made = create_engine(url)
    yield made
    made.dispose()


@pytest.fixture
def sessions(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def installation(sessions):
    """A registered installation, a throwaway organization, and **a real laboratory order**.

    The order is not optional scaffolding. ``connector_deliveries.lab_order_id`` is a foreign key
    with ``ON DELETE RESTRICT``, and an ``ORDER_NOTIFICATION`` envelope is required to carry one --
    so a fabricated UUID is a row PostgreSQL refuses outright while SQLite accepts it silently. That
    asymmetry is exactly what hid ``0028``'s revision-length bug, and it is why these fixtures build
    the real rows rather than the convenient ones.

    Cleaned up in dependency order and by id, never by truncation: this database holds the 0027/0028
    qualification evidence the earlier ticket verified, and a test that tidied up with TRUNCATE
    would destroy it.
    """
    with sessions.begin() as session:
        organization_id = session.execute(
            text(
                "INSERT INTO exchange_organizations (id, name, org_type, status, created_at) "
                "VALUES (gen_random_uuid(), :name, 'LAB', 'ACTIVE', now()) RETURNING id"
            ),
            {"name": f"PG Runtime Lab {uuid.uuid4().hex[:8]}"},
        ).scalar_one()
        row = ConnectorInstallation(
            organization_id=organization_id,
            connector_key="synthetic-lis",
            connector_version="0.1",
            contract_version=CONTRACT_VERSION,
            source_system="synthetic-lis",
            interface_type="FHIR",
            configuration_fingerprint=fingerprint({"pg": "runtime"}),
            mapping_fingerprint=fingerprint({"pg": "mapping"}),
            status=ConnectorInstallationStatus.REGISTERED.value,
            service_client_id=f"pg-client-{uuid.uuid4().hex[:8]}",
        )
        session.add(row)
        session.flush()
        installation_id = row.id

    order_id, provider_id, patient_id, reference_ids, entry_id = _seed_order(sessions)

    yield _World(
        installation_id=installation_id,
        organization_id=organization_id,
        lab_order_id=order_id,
    )

    with sessions.begin() as session:
        session.execute(
            text("DELETE FROM connector_deliveries WHERE connector_installation_id = :i"),
            {"i": installation_id},
        )
        session.execute(
            text("DELETE FROM connector_runtime_instances WHERE connector_installation_id = :i"),
            {"i": installation_id},
        )
        session.execute(
            text("DELETE FROM connector_correlations WHERE connector_installation_id = :i"),
            {"i": installation_id},
        )
        session.execute(
            text("DELETE FROM connector_installations WHERE id = :i"), {"i": installation_id}
        )
        # Dependency order, RESTRICT all the way down: items before the order, the order before the
        # references it names, the references before the organizations that asserted them.
        # Creating an order also creates the rows that track what is owed on it -- a result
        # delivery and a pending doctor review. Every one is RESTRICT, so they come out first; a
        # fixture that deleted only what it explicitly wrote would leave the order undeletable and
        # the database dirtier after every run.
        for dependent in (
            "exchange_result_deliveries",
            "exchange_doctor_reviews",
            "exchange_lab_selections",
            "exchange_lab_order_items",
        ):
            session.execute(
                text(f"DELETE FROM {dependent} WHERE lab_order_id = :o"),  # noqa: S608
                {"o": order_id},
            )
        session.execute(text("DELETE FROM exchange_lab_orders WHERE id = :o"), {"o": order_id})
        for reference_id in reference_ids:
            session.execute(
                text("DELETE FROM exchange_patient_references WHERE id = :r"), {"r": reference_id}
            )
        session.execute(text("DELETE FROM exchange_patients WHERE id = :p"), {"p": patient_id})
        session.execute(
            text("DELETE FROM exchange_practitioner_references WHERE source_organization_id = :o"),
            {"o": provider_id},
        )
        session.execute(
            text("DELETE FROM exchange_test_catalog_entries WHERE id = :e"), {"e": entry_id}
        )
        # A provider built by `make_provider` is *enrolled and staffed*, which is the point of it --
        # an unstaffed facility cannot have work routed from it. That means memberships, role
        # assignments and an enrollment row, all RESTRICT, all of which have to come out before the
        # organization itself. Listed explicitly rather than discovered, so a future table that
        # starts referencing organizations fails this teardown loudly rather than leaving rows.
        session.execute(
            text(
                "DELETE FROM exchange_organization_role_assignments WHERE membership_id IN "
                "(SELECT id FROM exchange_organization_memberships "
                " WHERE organization_id IN (:o, :p))"
            ),
            {"o": organization_id, "p": provider_id},
        )
        for dependent in (
            "exchange_organization_memberships",
            "exchange_facility_enrollments",
            # `exchange_lab_directory_profiles` keys on `lab_organization_id`, not
            # `organization_id`, and is handled separately below.
        ):
            session.execute(
                text(f"DELETE FROM {dependent} WHERE organization_id IN (:o, :p)"),  # noqa: S608
                {"o": organization_id, "p": provider_id},
            )
        session.execute(
            text(
                "DELETE FROM exchange_lab_directory_profiles WHERE lab_organization_id IN (:o, :p)"
            ),
            {"o": organization_id, "p": provider_id},
        )
        session.execute(
            text("DELETE FROM exchange_organizations WHERE id IN (:o, :p)"),
            {"o": organization_id, "p": provider_id},
        )


@dataclass(frozen=True, slots=True)
class _World:
    """What each test is handed: the installation, its facility, and a real order to reference."""

    installation_id: uuid.UUID
    organization_id: uuid.UUID
    lab_order_id: uuid.UUID


def _seed_order(sessions):
    """A real provider, patient, practitioner, catalog entry and order, through the repositories.

    Through the repositories rather than raw SQL so the rows satisfy every invariant the Exchange
    itself enforces -- a hand-written INSERT that happened to satisfy the foreign keys could still
    be a shape the domain would never produce, and a fixture like that tests the test.
    """
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    provider = make_provider(organizations, f"PG Clinique {uuid.uuid4().hex[:8]}", operational=True)
    patient = make_patient_reference(
        PatientReferenceRepository(sessions, organizations), provider_org_id=provider.id
    )
    practitioner = make_practitioner_reference(
        PractitionerReferenceRepository(sessions), provider_org_id=provider.id
    )
    entry, _ = catalog.create_entry(
        code=f"PG{uuid.uuid4().hex[:6].upper()}", display_name="Analyse de qualification"
    )
    created = LabOrderRepository(sessions, organizations, catalog).create(
        idempotency_key=f"pg-runtime-{uuid.uuid4()}",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )
    with sessions() as session:
        patient_row = session.execute(
            text("SELECT patient_id FROM exchange_patient_references WHERE id = :r"),
            {"r": patient.id},
        ).scalar_one()
        reference_ids = [
            row
            for row in session.execute(
                text(
                    "SELECT id FROM exchange_patient_references WHERE source_organization_id = :o"
                ),
                {"o": provider.id},
            ).scalars()
        ]
    return created.order.id, provider.id, patient_row, reference_ids, entry.id


def _envelope(installation_id, organization_id, event: str, lab_order_id) -> Envelope:
    moment = datetime.now(UTC)
    return Envelope(
        contract_version=CONTRACT_VERSION,
        message_id=uuid.uuid4(),
        connector_installation_id=installation_id,
        organization_id=organization_id,
        source_system="dhealth",
        source_event_id=event,
        source_occurred_at=moment,
        sent_at=moment,
        lab_order_id=lab_order_id,
        payload=OrderNotification(
            patient=ScopedExternalId(
                source_organization_id=organization_id,
                source_system="dhealth",
                source_identity_system="urn:dhealth:pg",
                source_identity_value=f"p-{event}",
            ),
            practitioner=SourcePractitionerRef(
                source_organization_id=organization_id,
                source_system="dhealth",
                source_identity_system="urn:dhealth:pg",
                source_identity_value="d-1",
            ),
            catalog_codes=("GLU",),
        ),
    )


def test_PG12_two_concurrent_claims_never_take_the_same_message(sessions, installation):
    """The property SQLite structurally cannot test. ``FOR UPDATE SKIP LOCKED``, for real.

    Two transactions, both open, both claiming. Without ``SKIP LOCKED`` the second would block
    on the first's row locks and then -- once the first committed -- either return nothing, having
    waited, or hand back rows the first had already taken. With it, the second transaction skips
    what the first holds and takes the rest.

    The assertion is disjointness: **no delivery id appears in both claims.** That is the whole
    guarantee. Two agents delivering the same order to one laboratory means a duplicate specimen
    request for a patient who has already been drawn.
    """
    installation_id = installation.installation_id
    organization_id = installation.organization_id
    deliveries = DeliveryRepository()
    with sessions.begin() as session:
        for index in range(6):
            deliveries.enqueue(
                session,
                installation_id=installation_id,
                envelope=_envelope(
                    installation_id,
                    organization_id,
                    f"pg12-{index}",
                    installation.lab_order_id,
                ),
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            )

    first = sessions()
    second = sessions()
    try:
        first.begin()
        second.begin()
        claimed_a = deliveries.claim(
            first,
            installation_id=installation_id,
            direction=Direction.DHEALTH_TO_CONNECTOR,
            runtime_instance_id="agent-a",
            limit=3,
        )
        # `second` runs while `first` still holds its locks -- the race, actually raced.
        claimed_b = deliveries.claim(
            second,
            installation_id=installation_id,
            direction=Direction.DHEALTH_TO_CONNECTOR,
            runtime_instance_id="agent-b",
            limit=3,
        )
        ids_a = {row.id for row in claimed_a}
        ids_b = {row.id for row in claimed_b}
        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()

    assert len(ids_a) == 3
    assert ids_a & ids_b == set(), "two runtime instances claimed the same delivery"
    assert len(ids_a | ids_b) == len(ids_a) + len(ids_b)


def test_PG13_the_replay_key_refuses_a_duplicate_at_the_database(sessions, installation):
    """The unique index, not the read-then-write check above it.

    ``enqueue`` looks for an existing row first, which handles the sequential case. Under real
    concurrency that read is not atomic and the index is what actually holds -- so it is exercised
    directly, by inserting behind the repository's back.
    """
    installation_id = installation.installation_id
    organization_id = installation.organization_id
    envelope = _envelope(installation_id, organization_id, "pg13", installation.lab_order_id)
    with sessions.begin() as session:
        DeliveryRepository().enqueue(
            session,
            installation_id=installation_id,
            envelope=envelope,
            expected_direction=Direction.DHEALTH_TO_CONNECTOR,
        )

    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            ConnectorDelivery(
                connector_installation_id=installation_id,
                direction=Direction.DHEALTH_TO_CONNECTOR.value,
                message_type="ORDER_NOTIFICATION",
                message_id=uuid.uuid4(),
                replay_scope=envelope.replay_scope(),
                source_event_id="pg13",
                content_fingerprint="different",
                payload="{}",
                state=DeliveryState.PENDING.value,
                attempt_count=0,
                next_attempt_at=datetime.now(UTC),
            )
        )


def test_PG14_one_service_client_cannot_authenticate_for_two_installations(sessions, installation):
    """§8's structural half, on the database that enforces it.

    A partial unique index over a nullable column: many installations may have no client, and no two
    may share one. It includes retired rows deliberately -- retiring an installation must invalidate
    its identity rather than recycle it, or a still-valid token would resolve to another facility.
    """
    installation_id = installation.installation_id
    with sessions() as session:
        existing = session.get(ConnectorInstallation, installation_id).service_client_id

    with pytest.raises(IntegrityError), sessions.begin() as session:
        other_org = session.execute(
            text(
                "INSERT INTO exchange_organizations (id, name, org_type, status, created_at) "
                "VALUES (gen_random_uuid(), :name, 'LAB', 'ACTIVE', now()) RETURNING id"
            ),
            {"name": f"PG Clash Lab {uuid.uuid4().hex[:8]}"},
        ).scalar_one()
        session.add(
            ConnectorInstallation(
                organization_id=other_org,
                connector_key="synthetic-lis",
                connector_version="0.1",
                contract_version=CONTRACT_VERSION,
                source_system="synthetic-lis",
                interface_type="FHIR",
                configuration_fingerprint=fingerprint({"pg": "clash"}),
                mapping_fingerprint=fingerprint({"pg": "clash"}),
                status=ConnectorInstallationStatus.REGISTERED.value,
                service_client_id=existing,
            )
        )


def test_PG15_many_installations_may_have_no_service_client(sessions, installation):
    """The partial half of the same index: ``NULL`` is not a value that collides with itself.

    Registering a connector and provisioning its credential are separate acts by separate people, so
    an index that refused a second unbound installation would force placeholder client ids -- which
    is how a "not yet issued" state turns into a fake one somebody later trusts.
    """
    # The fixture's own installation is bound to a client; these two are deliberately not, which
    # is the case the partial index must permit.
    created: list[tuple[uuid.UUID, uuid.UUID]] = []
    try:
        with sessions.begin() as session:
            for index in range(2):
                org = session.execute(
                    text(
                        "INSERT INTO exchange_organizations (id, name, org_type, status, "
                        "created_at) VALUES (gen_random_uuid(), :name, 'LAB', 'ACTIVE', now()) "
                        "RETURNING id"
                    ),
                    {"name": f"PG Unbound {index} {uuid.uuid4().hex[:8]}"},
                ).scalar_one()
                row = ConnectorInstallation(
                    organization_id=org,
                    connector_key="synthetic-lis",
                    connector_version="0.1",
                    contract_version=CONTRACT_VERSION,
                    source_system="synthetic-lis",
                    interface_type="FHIR",
                    configuration_fingerprint=fingerprint({"pg": f"unbound-{index}"}),
                    mapping_fingerprint=fingerprint({"pg": f"unbound-{index}"}),
                    status=ConnectorInstallationStatus.REGISTERED.value,
                    service_client_id=None,
                )
                session.add(row)
                session.flush()
                created.append((row.id, org))
    finally:
        with sessions.begin() as session:
            for row_id, org in created:
                session.execute(
                    text("DELETE FROM connector_installations WHERE id = :i"), {"i": row_id}
                )
                session.execute(
                    text("DELETE FROM exchange_organizations WHERE id = :o"), {"o": org}
                )
    assert len(created) == 2


def test_PG16_the_durable_receipt_store_refuses_a_contradicting_replay(sessions):
    """Same event id, different content, enforced by the index rather than by the read above it."""
    scope = f"pg16:{uuid.uuid4().hex[:8]}"
    try:
        with sessions.begin() as session:
            DurableReceiptStore(session).record(scope, "evt-1", "fp-1")
        with pytest.raises(IntegrityError), sessions.begin() as session:
            session.add(
                ConnectorMessageReceipt(
                    replay_scope=scope,
                    source_event_id="evt-1",
                    content_fingerprint="fp-2",
                    first_received_at=datetime.now(UTC),
                )
            )
    finally:
        with sessions.begin() as session:
            session.execute(
                text("DELETE FROM connector_message_receipts WHERE replay_scope = :s"), {"s": scope}
            )


def test_PG17_an_expired_lease_is_reclaimed_by_a_second_instance(sessions, installation):
    """Leases expire by the passage of time, on the database where the comparison is a timestamptz.

    The whole reason a lease beats a lock: the first agent is simply gone, and nothing has to notice
    or clean up for its work to become claimable again.
    """
    installation_id = installation.installation_id
    organization_id = installation.organization_id
    deliveries = DeliveryRepository()
    now = datetime.now(UTC)
    with sessions.begin() as session:
        deliveries.enqueue(
            session,
            installation_id=installation_id,
            envelope=_envelope(installation_id, organization_id, "pg17", installation.lab_order_id),
            expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            now=now,
        )
    with sessions.begin() as session:
        taken = deliveries.claim(
            session,
            installation_id=installation_id,
            direction=Direction.DHEALTH_TO_CONNECTOR,
            runtime_instance_id="agent-gone",
            lease=timedelta(minutes=2),
            now=now,
        )
        assert len(taken) == 1

    with sessions.begin() as session:
        too_soon = deliveries.claim(
            session,
            installation_id=installation_id,
            direction=Direction.DHEALTH_TO_CONNECTOR,
            runtime_instance_id="agent-next",
            now=now + timedelta(minutes=1),
        )
        assert too_soon == []

    with sessions.begin() as session:
        reclaimed = deliveries.claim(
            session,
            installation_id=installation_id,
            direction=Direction.DHEALTH_TO_CONNECTOR,
            runtime_instance_id="agent-next",
            now=now + timedelta(minutes=3),
        )
    assert len(reclaimed) == 1
    assert reclaimed[0].attempt_count == 2


def test_PG18_the_audit_vocabulary_admits_the_runtime_events_and_not_a_heartbeat(sessions):
    """0029 dropped and recreated the CHECK -- a branch the SQLite suite never runs.

    The negative half matters as much as the positive: §64 says a heartbeat is operational telemetry
    and must never become a security-audit row, and the constraint is where that is enforced rather
    than merely intended.
    """
    with sessions() as session:
        definition = session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_exchange_security_audit_events_event_type'"
            )
        ).scalar_one()
    for admitted in (
        "connector_runtime_connected",
        "connector_service_identity_set",
        "connector_message_refused",
    ):
        assert admitted in definition
    assert "connector_heartbeat" not in definition


def test_PG19_a_delivery_cannot_hold_half_a_lease(sessions, installation):
    """``ck_connector_deliveries_lease_paired``. A holder with no deadline never expires; a deadline
    with no holder frees nothing. Either half alone loses a message."""
    installation_id = installation.installation_id
    organization_id = installation.organization_id
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            ConnectorDelivery(
                connector_installation_id=installation_id,
                direction=Direction.DHEALTH_TO_CONNECTOR.value,
                message_type="ORDER_NOTIFICATION",
                message_id=uuid.uuid4(),
                replay_scope=f"{organization_id}:dhealth",
                source_event_id="pg19",
                content_fingerprint="fp",
                payload="{}",
                state=DeliveryState.PENDING.value,
                attempt_count=0,
                next_attempt_at=datetime.now(UTC),
                claimed_by_runtime_instance_id="agent-1",
                claim_expires_at=None,
            )
        )


def test_PG20_runtime_instances_are_unique_per_installation(sessions, installation):
    """One row per (installation, instance): a restarting agent updates its row rather than
    accumulating one per boot, which is what makes ``heartbeat_count`` meaningful."""
    installation_id = installation.installation_id
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.add(
            ConnectorRuntimeInstance(
                connector_installation_id=installation_id,
                runtime_instance_id="agent-pg20",
                agent_version="0.1.0",
                contract_version=CONTRACT_VERSION,
                first_seen_at=now,
                last_seen_at=now,
                reported_at=now,
                heartbeat_count=1,
                local_queue_depth=0,
                local_dead_letters=0,
                adapter_reachable=True,
            )
        )
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            ConnectorRuntimeInstance(
                connector_installation_id=installation_id,
                runtime_instance_id="agent-pg20",
                agent_version="0.1.0",
                contract_version=CONTRACT_VERSION,
                first_seen_at=now,
                last_seen_at=now,
                reported_at=now,
                heartbeat_count=1,
                local_queue_depth=0,
                local_dead_letters=0,
                adapter_reachable=True,
            )
        )


def test_PG21_a_dead_lettered_delivery_keeps_its_payload(sessions, installation):
    """§21 and §56 together: a dead letter is an unresolved row, not a deletion, and it still holds
    what it was asked to deliver -- a fingerprint alone could not be redelivered."""
    installation_id = installation.installation_id
    organization_id = installation.organization_id
    deliveries = DeliveryRepository()
    now = datetime.now(UTC)
    with sessions.begin() as session:
        deliveries.enqueue(
            session,
            installation_id=installation_id,
            envelope=_envelope(installation_id, organization_id, "pg21", installation.lab_order_id),
            expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            now=now,
        )
    with sessions.begin() as session:
        claimed = deliveries.claim(
            session,
            installation_id=installation_id,
            direction=Direction.DHEALTH_TO_CONNECTOR,
            runtime_instance_id="agent-pg21",
            now=now,
        )
        deliveries.mark_failed(
            session,
            delivery=claimed[0],
            runtime_instance_id="agent-pg21",
            category=FailureCategory.MAPPING,
            detail="code inconnu",
            now=now,
        )
    with sessions() as session:
        row = session.scalar(
            select(ConnectorDelivery).where(ConnectorDelivery.source_event_id == "pg21")
        )
        total = session.scalar(
            select(func.count(ConnectorDelivery.id)).where(
                ConnectorDelivery.connector_installation_id == installation_id
            )
        )
    assert row.state == DeliveryState.DEAD_LETTER.value
    assert row.dead_lettered_at is not None
    assert row.payload and row.payload != ""
    assert Envelope.model_validate_json(row.payload).source_event_id == "pg21"
    assert total >= 1, "a dead letter is never deleted"
