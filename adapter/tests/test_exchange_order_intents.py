"""DH-CLINIC-003C: repository tests for backend-owned LabOrderIntent and exact commit replay."""

from __future__ import annotations

import pytest
from exchange_support import (
    make_lab,
    make_patient_reference,
    make_practitioner_reference,
    make_priced_offering,
    make_provider,
)
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.order_intents import (
    LabOrderIntentRepository,
    OrderIntentNotFound,
)
from daher_adapter.exchange.orders import (
    DuplicateOrderItems,
    EmptyOrder,
    IdempotencyKeyConflict,
    LabOrderRepository,
)
from daher_adapter.exchange.organizations import OrganizationRepository


def _build(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    intents = LabOrderIntentRepository(sessions, organizations, catalog)
    return organizations, catalog, patients, practitioners, orders, intents


def test_order_intent_create_and_retrieve(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, intents = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry_one, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-CBC-001")
    entry_two, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-GLU-001")
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)

    res = intents.create(
        idempotency_key="intent-key-1",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        catalog_entry_ids=[entry_one.id, entry_two.id],
    )
    assert res.created is True
    intent = res.intent
    assert intent.idempotency_key == "intent-key-1"
    assert intent.provider_organization_id == provider.id
    assert intent.patient_reference_id == patient.id
    assert intent.practitioner_reference_id == practitioner.id
    assert intent.committed_lab_order_id is None
    assert len(intent.items) == 2
    assert {i.catalog_code_snapshot for i in intent.items} == {"DH-CBC-001", "DH-GLU-001"}

    # Retrieve by ID
    by_id = intents.get_for_organization(provider.id, intent.id)
    assert by_id is not None
    assert by_id.id == intent.id
    assert len(by_id.items) == 2

    # Retrieve by idempotency key
    by_key = intents.get_by_idempotency_key(provider.id, "intent-key-1")
    assert by_key is not None
    assert by_key.id == intent.id


def test_order_intent_replay_and_conflict(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, intents = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry_one, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-CBC-001")
    entry_two, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-GLU-001")
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)

    res1 = intents.create(
        idempotency_key="replay-key",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        catalog_entry_ids=[entry_one.id, entry_two.id],
    )
    assert res1.created is True

    # Same payload -> replay returns created=False
    res2 = intents.create(
        idempotency_key="replay-key",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        catalog_entry_ids=[entry_one.id, entry_two.id],
    )
    assert res2.created is False
    assert res2.intent.id == res1.intent.id

    # Changed items -> 409 conflict
    with pytest.raises(IdempotencyKeyConflict):
        intents.create(
            idempotency_key="replay-key",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            catalog_entry_ids=[entry_one.id],
        )

    # Changed patient -> 409 conflict
    patient2 = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="SYNTH-PATIENT-2"
    )
    with pytest.raises(IdempotencyKeyConflict):
        intents.create(
            idempotency_key="replay-key",
            provider_organization_id=provider.id,
            patient_reference_id=patient2.id,
            practitioner_reference_id=practitioner.id,
            catalog_entry_ids=[entry_one.id, entry_two.id],
        )


def test_order_intent_commit_and_commit_replay(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, intents = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry_one, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-CBC-001")
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)

    res = intents.create(
        idempotency_key="commit-key-1",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        catalog_entry_ids=[entry_one.id],
    )
    intent = res.intent
    assert intent.committed_lab_order_id is None

    # First commit -> created=True
    with sessions() as session:
        commit_res1 = intents.commit(provider.id, intent.id, orders, session=session)
        session.commit()

    assert commit_res1.created is True
    order = commit_res1.order
    assert order.idempotency_key == "commit-key-1"
    assert order.patient_reference_id == patient.id

    # Intent now shows committed_lab_order_id
    updated_intent = intents.get_for_organization(provider.id, intent.id)
    assert updated_intent is not None
    assert updated_intent.committed_lab_order_id == order.id

    # Second commit -> replay returns existing order with created=False
    with sessions() as session:
        commit_res2 = intents.commit(provider.id, intent.id, orders, session=session)
        session.commit()

    assert commit_res2.created is False
    assert commit_res2.order.id == order.id


def test_order_intent_replay_reads_uncommitted_order_in_same_session(
    sessions: sessionmaker[Session],
) -> None:
    """Regression: replay is valid before the outer transaction becomes visible elsewhere."""
    organizations, catalog, patients, practitioners, orders, intents = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-INTENT-REPLAY")
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    intent = intents.create(
        idempotency_key="same-session-replay",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        catalog_entry_ids=[entry.id],
    ).intent

    with sessions() as session:
        first = intents.commit(provider.id, intent.id, orders, session=session)
        replay = intents.commit(provider.id, intent.id, orders, session=session)
        assert first.created is True
        assert replay.created is False
        assert replay.order.id == first.order.id
        session.commit()


def test_order_intent_validation_guards(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, intents = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry_one, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-CBC-001")
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)

    # Empty order
    with pytest.raises(EmptyOrder):
        intents.create(
            idempotency_key="k1",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            catalog_entry_ids=[],
        )

    # Duplicate catalog items
    with pytest.raises(DuplicateOrderItems):
        intents.create(
            idempotency_key="k2",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            catalog_entry_ids=[entry_one.id, entry_one.id],
        )

    # Empty idempotency key
    with pytest.raises(ValueError, match="idempotency_key is required"):
        intents.create(
            idempotency_key="   ",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            catalog_entry_ids=[entry_one.id],
        )


def test_order_intent_provider_isolation(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, intents = _build(sessions)
    provider1 = make_provider(organizations)
    provider2 = make_provider(organizations)
    lab = make_lab(organizations)
    entry_one, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-CBC-001")
    patient = make_patient_reference(patients, provider_org_id=provider1.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider1.id)

    res = intents.create(
        idempotency_key="iso-key",
        provider_organization_id=provider1.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        catalog_entry_ids=[entry_one.id],
    )

    # Provider 2 cannot see provider 1's intent
    assert intents.get_for_organization(provider2.id, res.intent.id) is None
    assert intents.get_by_idempotency_key(provider2.id, "iso-key") is None

    # Provider 2 cannot commit provider 1's intent
    with sessions() as session:
        with pytest.raises(OrderIntentNotFound):
            intents.commit(provider2.id, res.intent.id, orders, session=session)
