"""DH-CLINIC-003C: backend-owned durable canonical diagnostic order intent repository.

An immutable record of one intended clinical submission created prior to committing
a LabOrder. Allows exact mutation recovery across browser crashes or page reloads
without persisting diagnostic service IDs or test names in browser local storage.

Uniqueness is scoped to ``(provider_organization_id, idempotency_key)``, preserving
exact provider isolation and duplicate-safe replay semantics.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.models import (
    LabOrderIntent,
    LabOrderIntentItem,
    LabOrder,
    OrganizationType,
    PatientReference,
    PractitionerReference,
)
from daher_adapter.exchange.orders import (
    CreateOrderResult,
    DuplicateOrderItems,
    EmptyOrder,
    IdempotencyKeyConflict,
    LabOrderRepository,
    OrderItemInput,
)
from daher_adapter.exchange.organizations import OrganizationRepository


class OrderIntentNotFound(RuntimeError):
    """No LabOrderIntent matches the given id or
    (provider_organization_id, idempotency_key) pair."""


@dataclass(frozen=True, slots=True)
class LabOrderIntentItemRecord:
    id: uuid.UUID
    catalog_entry_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str


@dataclass(frozen=True, slots=True)
class LabOrderIntentRecord:
    id: uuid.UUID
    idempotency_key: str
    provider_organization_id: uuid.UUID
    patient_reference_id: uuid.UUID
    practitioner_reference_id: uuid.UUID
    committed_lab_order_id: uuid.UUID | None
    created_at: datetime
    items: tuple[LabOrderIntentItemRecord, ...]


@dataclass(frozen=True, slots=True)
class CreateIntentResult:
    intent: LabOrderIntentRecord
    created: bool


def _to_intent_record(intent: LabOrderIntent) -> LabOrderIntentRecord:
    return LabOrderIntentRecord(
        id=intent.id,
        idempotency_key=intent.idempotency_key,
        provider_organization_id=intent.provider_organization_id,
        patient_reference_id=intent.patient_reference_id,
        practitioner_reference_id=intent.practitioner_reference_id,
        committed_lab_order_id=intent.committed_lab_order_id,
        created_at=intent.created_at,
        items=tuple(
            LabOrderIntentItemRecord(
                id=item.id,
                catalog_entry_id=item.catalog_entry_id,
                catalog_code_snapshot=item.catalog_code_snapshot,
                catalog_display_name_snapshot=item.catalog_display_name_snapshot,
            )
            for item in intent.items
        ),
    )


class LabOrderIntentRepository:
    """Encapsulates creation, recovery, and commit of backend-owned LabOrderIntents."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        organizations: OrganizationRepository,
        catalog: CatalogRepository,
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations
        self._catalog = catalog

    def create(
        self,
        *,
        idempotency_key: str,
        provider_organization_id: uuid.UUID,
        patient_reference_id: uuid.UUID,
        practitioner_reference_id: uuid.UUID,
        catalog_entry_ids: list[uuid.UUID],
        session: Session | None = None,
    ) -> CreateIntentResult:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if not catalog_entry_ids:
            raise EmptyOrder("an order intent must have at least one item")
        if len(set(catalog_entry_ids)) != len(catalog_entry_ids):
            raise DuplicateOrderItems(
                "an order intent must not request the same catalog_entry_id more than once"
            )

        if session is not None:
            snapshots = self._validate_canonical_request(
                session, provider_organization_id, patient_reference_id,
                practitioner_reference_id, catalog_entry_ids
            )
            return self._create_in_session(
                session,
                idempotency_key=idempotency_key,
                provider_organization_id=provider_organization_id,
                patient_reference_id=patient_reference_id,
                practitioner_reference_id=practitioner_reference_id,
                catalog_entry_ids=catalog_entry_ids,
                snapshots=snapshots,
            )
        with self._sessions() as owned_session:
            snapshots = self._validate_canonical_request(
                owned_session, provider_organization_id, patient_reference_id,
                practitioner_reference_id, catalog_entry_ids
            )
            result = self._create_in_session(
                owned_session,
                idempotency_key=idempotency_key,
                provider_organization_id=provider_organization_id,
                patient_reference_id=patient_reference_id,
                practitioner_reference_id=practitioner_reference_id,
                catalog_entry_ids=catalog_entry_ids,
                snapshots=snapshots,
            )
            owned_session.commit()
            return result

    def _validate_canonical_request(
        self,
        session: Session,
        provider_organization_id: uuid.UUID,
        patient_reference_id: uuid.UUID,
        practitioner_reference_id: uuid.UUID,
        catalog_entry_ids: list[uuid.UUID],
    ) -> list[tuple[uuid.UUID, object]]:
        """Single canonical request guard used before an immutable reservation exists.

        It deliberately validates identifiers only; item snapshots are then frozen on the
        intent, so commit never revalidates or accepts browser replacement content.
        """
        self._organizations.require_type(provider_organization_id, OrganizationType.PROVIDER)
        patient = session.get(PatientReference, patient_reference_id)
        practitioner = session.get(PractitionerReference, practitioner_reference_id)
        if patient is None or patient.source_organization_id != provider_organization_id:
            raise ValueError("patient reference does not belong to provider organization")
        if practitioner is None or practitioner.source_organization_id != provider_organization_id:
            raise ValueError("practitioner reference does not belong to provider organization")
        snapshots: list[tuple[uuid.UUID, object]] = []
        for entry_id in sorted(catalog_entry_ids, key=str):
            entry = self._catalog.get_entry(entry_id)
            if not entry.active:
                raise ValueError(f"catalog entry {entry_id} is not active")
            if not entry.is_synthetic:
                raise ValueError(f"catalog entry {entry_id} is not a synthetic pilot-phase entry")
            snapshots.append((entry_id, entry))
        return snapshots

    def _create_in_session(
        self,
        session: Session,
        *,
        idempotency_key: str,
        provider_organization_id: uuid.UUID,
        patient_reference_id: uuid.UUID,
        practitioner_reference_id: uuid.UUID,
        catalog_entry_ids: list[uuid.UUID],
        snapshots: list[tuple[uuid.UUID, object]],
    ) -> CreateIntentResult:
        intent = LabOrderIntent(
            idempotency_key=idempotency_key,
            provider_organization_id=provider_organization_id,
            patient_reference_id=patient_reference_id,
            practitioner_reference_id=practitioner_reference_id,
        )
        intent.items = [
            LabOrderIntentItem(
                catalog_entry_id=entry_id,
                catalog_code_snapshot=entry.code,
                catalog_display_name_snapshot=entry.display_name,
            )
            for entry_id, entry in snapshots
        ]

        try:
            with session.begin_nested():
                session.add(intent)
                session.flush()
            return CreateIntentResult(intent=_to_intent_record(intent), created=True)
        except IntegrityError:
            existing = session.scalar(
                select(LabOrderIntent)
                .options(selectinload(LabOrderIntent.items))
                .where(
                    LabOrderIntent.provider_organization_id == provider_organization_id,
                    LabOrderIntent.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            existing_record = _to_intent_record(existing)
            existing_item_ids = frozenset(item.catalog_entry_id for item in existing_record.items)
            requested_item_ids = frozenset(catalog_entry_ids)
            if (
                existing_record.patient_reference_id != patient_reference_id
                or existing_record.practitioner_reference_id != practitioner_reference_id
                or existing_item_ids != requested_item_ids
            ):
                raise IdempotencyKeyConflict(
                    f"idempotency_key {idempotency_key!r} for provider organization "
                    f"{provider_organization_id} was already used for an intent with a "
                    f"different patient, practitioner, or requested test set -- refusing to "
                    f"return mismatched data"
                ) from None
            return CreateIntentResult(intent=existing_record, created=False)

    def get_for_organization(
        self,
        provider_organization_id: uuid.UUID,
        intent_id: uuid.UUID,
        *,
        session: Session | None = None,
    ) -> LabOrderIntentRecord | None:
        """Tenant-safe intent lookup by intent_id."""
        if session is not None:
            intent = session.scalar(
                select(LabOrderIntent)
                .options(selectinload(LabOrderIntent.items))
                .where(
                    LabOrderIntent.id == intent_id,
                    LabOrderIntent.provider_organization_id == provider_organization_id,
                )
            )
            return _to_intent_record(intent) if intent is not None else None

        with self._sessions() as owned_session:
            intent = owned_session.scalar(
                select(LabOrderIntent)
                .options(selectinload(LabOrderIntent.items))
                .where(
                    LabOrderIntent.id == intent_id,
                    LabOrderIntent.provider_organization_id == provider_organization_id,
                )
            )
            return _to_intent_record(intent) if intent is not None else None

    def get_by_idempotency_key(
        self,
        provider_organization_id: uuid.UUID,
        idempotency_key: str,
        *,
        session: Session | None = None,
    ) -> LabOrderIntentRecord | None:
        """Tenant-safe intent lookup by (provider_organization_id, idempotency_key)."""
        if session is not None:
            intent = session.scalar(
                select(LabOrderIntent)
                .options(selectinload(LabOrderIntent.items))
                .where(
                    LabOrderIntent.provider_organization_id == provider_organization_id,
                    LabOrderIntent.idempotency_key == idempotency_key,
                )
            )
            return _to_intent_record(intent) if intent is not None else None

        with self._sessions() as owned_session:
            intent = owned_session.scalar(
                select(LabOrderIntent)
                .options(selectinload(LabOrderIntent.items))
                .where(
                    LabOrderIntent.provider_organization_id == provider_organization_id,
                    LabOrderIntent.idempotency_key == idempotency_key,
                )
            )
            return _to_intent_record(intent) if intent is not None else None

    def commit(
        self,
        provider_organization_id: uuid.UUID,
        intent_id: uuid.UUID,
        orders_repo: LabOrderRepository,
        *,
        session: Session,
    ) -> CreateOrderResult:
        """Commit an immutable backend intent to a LabOrder inside session with row-level locking.

        If already committed, returns existing order with created=False.
        """
        intent = session.scalar(
            select(LabOrderIntent)
            .options(selectinload(LabOrderIntent.items))
            .where(
                LabOrderIntent.id == intent_id,
                LabOrderIntent.provider_organization_id == provider_organization_id,
            )
            .with_for_update()
        )
        if intent is None:
            raise OrderIntentNotFound(f"Order intent {intent_id} not found")

        if intent.committed_lab_order_id is not None:
            existing_order = orders_repo.get(intent.committed_lab_order_id, session=session)
            return CreateOrderResult(order=existing_order, created=False)

        # Create LabOrder using exact canonical intent data
        order_items = [
            OrderItemInput(catalog_entry_id=item.catalog_entry_id) for item in intent.items
        ]
        result = orders_repo.create(
            idempotency_key=intent.idempotency_key,
            provider_organization_id=intent.provider_organization_id,
            patient_reference_id=intent.patient_reference_id,
            practitioner_reference_id=intent.practitioner_reference_id,
            items=order_items,
            intent_id=intent.id,
            session=session,
        )

        # The two durable pointers are written and checked in this one transaction.
        # A pre-003C direct order with the same key must not be retroactively claimed:
        # it remains historical/unowned instead of becoming one-sided provenance.
        created_order = session.get(LabOrder, result.order.id)
        if created_order is None or created_order.intent_id != intent.id:
            raise IdempotencyKeyConflict(
                "idempotency key is already associated with an order outside this durable intent"
            )

        intent.committed_lab_order_id = result.order.id
        session.flush()
        return result
