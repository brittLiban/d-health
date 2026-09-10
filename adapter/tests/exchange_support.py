"""DH-EX-001 test helpers, shared by ``test_exchange_*.py``.

Deliberately not a ``conftest.py`` -- these are plain functions, not fixtures, so this file has
zero effect on any existing (non-exchange) adapter test's collection or fixtures. Every exchange
test still uses the existing ``sessions``/``engine`` fixtures from ``conftest.py`` unmodified.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from daher_adapter.exchange.catalog import CatalogEntryRecord, CatalogRepository, LabOfferingRecord
from daher_adapter.exchange.identity import (
    PatientReferenceRecord,
    PatientReferenceRepository,
    PractitionerReferenceRecord,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import OrganizationType
from daher_adapter.exchange.organizations import OrganizationRecord, OrganizationRepository


def make_provider(
    organizations: OrganizationRepository, name: str = "Clinic A"
) -> OrganizationRecord:
    return organizations.create(org_type=OrganizationType.PROVIDER.value, name=name)


def make_lab(organizations: OrganizationRepository, name: str = "Lab B") -> OrganizationRecord:
    return organizations.create(org_type=OrganizationType.LAB.value, name=name)


def make_patient_reference(
    patients: PatientReferenceRepository,
    *,
    provider_org_id: uuid.UUID,
    source_identity_value: str = "SYNTH-PATIENT-1",
) -> PatientReferenceRecord:
    return patients.get_or_create(
        source_organization_id=provider_org_id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=source_identity_value,
        display_name="Synthetic Patient",
    )


def make_practitioner_reference(
    practitioners: PractitionerReferenceRepository,
    *,
    provider_org_id: uuid.UUID,
    source_identity_value: str = "SYNTH-DOCTOR-1",
) -> PractitionerReferenceRecord:
    return practitioners.get_or_create(
        source_organization_id=provider_org_id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value=source_identity_value,
        display_name="Dr Synthetic",
    )


def make_priced_offering(
    catalog: CatalogRepository,
    *,
    lab_organization_id: uuid.UUID,
    code: str = "DH-CBC-001",
    display_name: str = "Complete Blood Count (synthetic)",
    amount: Decimal = Decimal("500.00"),
    currency: str = "DJF",
    active: bool = True,
) -> tuple[CatalogEntryRecord, LabOfferingRecord]:
    entry, _ = catalog.create_entry(code=code, display_name=display_name)
    offering, _ = catalog.create_offering(
        lab_organization_id=lab_organization_id, catalog_entry_id=entry.id
    )
    catalog.set_price(
        offering_id=offering.id,
        amount=amount,
        currency=currency,
        expected_current_price_id=None,
    )
    if not active:
        catalog.set_offering_active(offering.id, False)
    return entry, offering
