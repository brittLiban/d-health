"""DH-NOTIFY-001 test helpers — one place that drives an order all the way to a released result.

Deliberately not a ``conftest.py``, matching ``exchange_support.py``'s own reasoning: plain
functions, so this file changes no existing test's collection or fixtures.

Everything here reuses the **real** repositories along the real workflow path -- create, select,
accept, record collection, start processing, record a value, complete entry, release. A helper that
inserted a ``LabResultRelease`` row directly would let every notification test pass while the actual
release hook was never wired, which is precisely the class of "passed for a reason unrelated to what
it claimed to verify" this repository's evidence discipline is written against.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from exchange_support import (
    lab_staff,
    make_patient_reference,
    make_practitioner_reference,
    make_priced_offering,
    make_provider,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import LabPrice, LabResultType
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patient_access import PatientAccessGrantRepository
from daher_adapter.exchange.results import (
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
)
from daher_adapter.exchange.selection import LabSelectionRepository
from daher_adapter.notify.models import NotificationChannel
from daher_adapter.notify.repository import PatientNotificationRepository

#: A destination that is syntactically valid E.164 and belongs to no real person. Djibouti's
#: mobile range, because the pilot is Djiboutian and an obviously-foreign number in a fixture reads
#: as an accident. Never dialled by any test -- every transport in the suite is the in-memory fake.
SYNTHETIC_DESTINATION = "+25377123456"
SECOND_DESTINATION = "+25377654321"

#: The synthetic clinical content every fixture enters. Named constants rather than literals
#: scattered through the tests, because the content tests assert on these exact strings: they are
#: the things a message must never contain, so the assertion and the fixture must be the same value
#: or the test proves nothing.
OBSERVATION_CODE = "SYNTH-OBS"
OBSERVATION_LABEL = "Synthetic analyte"
OBSERVATION_UNIT = "mmol/L"
RANGE_LOW = "3.5"
RANGE_HIGH = "6.0"


@dataclass(frozen=True, slots=True)
class NotifyRepos:
    organizations: OrganizationRepository
    catalog: CatalogRepository
    patients: PatientReferenceRepository
    practitioners: PractitionerReferenceRepository
    orders: LabOrderRepository
    selections: LabSelectionRepository
    workflow: LabWorkflowRepository
    results: LabResultRepository
    releases: LabResultReleaseRepository
    amendments: LabResultAmendmentRepository
    directory: LabDirectoryProfileRepository
    identities: ExchangeIdentityRepository
    grants: PatientAccessGrantRepository
    notifications: PatientNotificationRepository


def build_repos(sessions: sessionmaker[Session]) -> NotifyRepos:
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    return NotifyRepos(
        organizations=organizations,
        catalog=catalog,
        patients=PatientReferenceRepository(sessions),
        practitioners=PractitionerReferenceRepository(sessions),
        orders=LabOrderRepository(sessions, organizations, catalog),
        selections=LabSelectionRepository(sessions, organizations),
        workflow=LabWorkflowRepository(sessions, organizations),
        results=LabResultRepository(sessions),
        releases=LabResultReleaseRepository(sessions),
        amendments=LabResultAmendmentRepository(sessions),
        directory=LabDirectoryProfileRepository(sessions, organizations),
        identities=ExchangeIdentityRepository(sessions),
        grants=PatientAccessGrantRepository(sessions, ttl_hours=168),
        notifications=PatientNotificationRepository(),
    )


def identity_id(repos: NotifyRepos, subject: str) -> uuid.UUID:
    identity, _ = repos.identities.get_or_create(
        issuer="https://tests.dhealth.invalid", subject=subject
    )
    return identity.id


def _current_price_id(sessions: sessionmaker[Session], offering_id) -> object:
    with sessions() as session:
        return session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )


@dataclass(frozen=True, slots=True)
class OrderContext:
    order_id: uuid.UUID
    item_id: uuid.UUID
    provider_id: uuid.UUID
    lab_id: uuid.UUID


def order_in_progress(
    repos: NotifyRepos, sessions: sessionmaker[Session], *, lab_id: uuid.UUID, key: str
) -> OrderContext:
    """A one-item order assigned to ``lab_id`` and driven to ``IN_PROGRESS`` the real way."""
    repos.directory.upsert(
        lab_organization_id=lab_id,
        public_address="Test Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    entry, offering = make_priced_offering(
        repos.catalog, lab_organization_id=lab_id, amount=Decimal("500.00"), code=f"DH-{key}"
    )
    provider = make_provider(repos.organizations, name=f"Clinic-{key}")
    patient = make_patient_reference(
        repos.patients, provider_org_id=provider.id, source_identity_value=key
    )
    practitioner = make_practitioner_reference(
        repos.practitioners, provider_org_id=provider.id, source_identity_value=f"doc-{key}"
    )
    order = repos.orders.create(
        idempotency_key=key,
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order
    price_id = _current_price_id(sessions, offering.id)
    repos.selections.select(
        lab_order_id=order.id, lab_organization_id=lab_id, expected_price_ids=frozenset({price_id})
    )
    repos.workflow.accept(order_id=order.id, lab_organization_id=lab_id)
    repos.workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab_id,
        recorded_by_identity_id=lab_staff(repos.organizations, lab_id),
    )
    repos.workflow.start_processing(order_id=order.id, lab_organization_id=lab_id)

    refreshed = repos.orders.get(order.id)
    return OrderContext(
        order_id=order.id,
        item_id=refreshed.items[0].id,
        provider_id=provider.id,
        lab_id=lab_id,
    )


def enter_result(
    repos: NotifyRepos, context: OrderContext, *, key: str, value: str = "5.1"
) -> None:
    """One recorded observation and an explicit entry completion -- the DH-EX-012C path.

    A deliberately unremarkable numeric value. Nothing in this suite asserts on it *except* the
    content tests, which assert the exact opposite: that it never appears in any message body.
    """
    tech = identity_id(repos, f"labtech-{key}")
    repos.results.record_observation(
        order_id=context.order_id,
        lab_order_item_id=context.item_id,
        lab_organization_id=context.lab_id,
        result_type=LabResultType.NUMERIC,
        source_code=OBSERVATION_CODE,
        display_label_snapshot=OBSERVATION_LABEL,
        recorded_by_identity_id=tech,
        numeric_value=Decimal(value),
        numeric_unit=OBSERVATION_UNIT,
        reference_range_low=Decimal(RANGE_LOW),
        reference_range_high=Decimal(RANGE_HIGH),
    )
    repos.results.complete_entry(
        order_id=context.order_id,
        lab_order_item_id=context.item_id,
        lab_organization_id=context.lab_id,
        completed_by_identity_id=tech,
    )


def release(repos: NotifyRepos, context: OrderContext, *, key: str):
    """Release through the real repository, which is what fires the notification hook."""
    releaser = identity_id(repos, f"releaser-{key}")
    return repos.releases.release(
        order_id=context.order_id,
        lab_organization_id=context.lab_id,
        released_by_identity_id=releaser,
    )


def amend_and_release(repos: NotifyRepos, context: OrderContext, *, key: str, value: str = "5.9"):
    """A full post-release amendment episode, released -- the ADR-048 Model 3 path."""
    tech = identity_id(repos, f"labtech-{key}")
    releaser = identity_id(repos, f"releaser-{key}")
    amendment = repos.amendments.start_amendment(
        order_id=context.order_id,
        lab_organization_id=context.lab_id,
        started_by_identity_id=tech,
    )
    inspection = repos.amendments.get_amendment_inspection(amendment.id)
    observation_id = inspection.items[0].observations[0].id
    repos.amendments.correct_observation(
        amendment_id=amendment.id,
        order_id=context.order_id,
        lab_order_item_id=context.item_id,
        lab_organization_id=context.lab_id,
        observation_id=observation_id,
        result_type=LabResultType.NUMERIC,
        source_code=OBSERVATION_CODE,
        display_label_snapshot=OBSERVATION_LABEL,
        corrected_by_identity_id=tech,
        numeric_value=Decimal(value),
        numeric_unit=OBSERVATION_UNIT,
        reference_range_low=Decimal(RANGE_LOW),
        reference_range_high=Decimal(RANGE_HIGH),
    )
    return repos.amendments.release_amendment(
        amendment_id=amendment.id,
        order_id=context.order_id,
        lab_organization_id=context.lab_id,
        released_by_identity_id=releaser,
    )


def enrol(
    repos: NotifyRepos,
    sessions: sessionmaker[Session],
    context: OrderContext,
    *,
    channel: NotificationChannel = NotificationChannel.SMS,
    destination: str = SYNTHETIC_DESTINATION,
    key: str = "clinician",
):
    """Issue patient access and enrol the order for notification, as the API path does.

    Returns the issued grant, so a test can assert on the raw token's absence from storage.
    """
    # Resolved **before** the transaction opens. `identity_id` opens its own session, and a second
    # SQLite connection against a file already held by an open write transaction deadlocks --
    # "database is locked". The production API path has the same shape for the same reason: the
    # principal's identity id is already resolved by the auth dependency long before the issuance
    # transaction begins.
    consent_identity = identity_id(repos, key)
    with sessions.begin() as session:
        issued = repos.grants.issue(lab_order_id=context.order_id, session=session)
        repos.notifications.enroll(
            session,
            lab_order_id=context.order_id,
            patient_access_grant_id=issued.id,
            channel=channel,
            destination=destination,
            consent_recorded_by_identity_id=consent_identity,
        )
    return issued
