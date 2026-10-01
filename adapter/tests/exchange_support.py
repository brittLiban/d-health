"""DH-EX-001 test helpers, shared by ``test_exchange_*.py``.

Deliberately not a ``conftest.py`` -- these are plain functions, not fixtures, so this file has
zero effect on any existing (non-exchange) adapter test's collection or fixtures. Every exchange
test still uses the existing ``sessions``/``engine`` fixtures from ``conftest.py`` unmodified.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from daher_adapter.exchange.catalog import CatalogEntryRecord, CatalogRepository, LabOfferingRecord
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRecord,
    PatientReferenceRepository,
    PractitionerReferenceRecord,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_result_templates import (
    LabResultTemplateRepository,
    TemplateObservationDefinitionInput,
)
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    OnboardingStatus,
    OrganizationRole,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.organizations import OrganizationRecord, OrganizationRepository


def _enrol_and_staff(
    organizations: OrganizationRepository,
    organization: OrganizationRecord,
    roles: tuple[OrganizationRole, ...],
) -> None:
    """Give a test organization the enrollment and staffing a real one has.

    DH-MVP-ONBOARD-001 made production routing readiness-gated: a facility must be enrolled and
    must actually hold the roles needed to do the work before orders may be routed to or from it.
    Migration 0026 backfills an enrollment for every organization, so in production there is no
    such thing as an organization without one -- which means a helper that created a bare
    organization was building a state that cannot occur, and ~20 tests were silently relying on it.

    Reaches for the repository's own session factory rather than taking a second parameter, so the
    221 existing call sites keep working unchanged. A private attribute in a test-support module is
    a contained trade against editing every one of them.
    """
    sessions = organizations._sessions  # noqa: SLF001 -- see docstring
    FacilityEnrollmentRepository(sessions).create(
        organization_id=organization.id,
        participation_mode=ParticipationMode.NATIVE,
        onboarding_status=OnboardingStatus.ORGANIZATION_READY,
    )
    if not roles:
        return
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://tests.dhealth.invalid",
        subject=f"staff-{organization.id}",
        display_name=f"Personnel {organization.name}",
        email=None,
    )
    membership, _ = OrganizationMembershipRepository(sessions).create(
        organization_id=organization.id, identity_id=identity.id
    )
    assignments = OrganizationRoleAssignmentRepository(sessions)
    for role in roles:
        assignments.assign(membership_id=membership.id, role=role)


def lab_staff(organizations: OrganizationRepository, lab_organization_id: uuid.UUID) -> uuid.UUID:
    """DHEALTH-SPECIMEN-001: the identity id ``make_lab`` already staffed this laboratory with.

    ``LabSpecimen.recorded_by_identity_id`` is a ``NOT NULL`` foreign key to
    ``exchange_identities`` -- deliberately, because every real caller has a genuine identity to
    attribute (a human through ``AuthenticatedPrincipal``, a connector through
    ``ConnectorServicePrincipal``). So a test that records a collection needs a real identity row
    too, and a bare ``uuid.uuid4()`` would pass under SQLite while failing the foreign key against
    real PostgreSQL -- which is precisely the class of gap the integration suites exist to catch.

    ``get_or_create`` with the same deterministic ``subject`` ``_enrol_and_staff`` uses, so this
    returns the laboratory's *existing* staff identity rather than inventing a second one. Reaches
    for the repository's own session factory for the same reason ``_enrol_and_staff`` does."""
    sessions = organizations._sessions  # noqa: SLF001 -- see `_enrol_and_staff`'s docstring
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://tests.dhealth.invalid",
        subject=f"staff-{lab_organization_id}",
        display_name=f"Personnel {lab_organization_id}",
        email=None,
    )
    return identity.id


def make_provider(
    organizations: OrganizationRepository, name: str = "Clinic A", *, operational: bool = True
) -> OrganizationRecord:
    """A provider organization. By default it is enrolled and holds a CLINICIAN, i.e. a facility
    production work may actually be routed from. Pass ``operational=False`` for a test that is
    specifically about an unenrolled or unstaffed facility."""
    organization = organizations.create(org_type=OrganizationType.PROVIDER.value, name=name)
    if operational:
        _enrol_and_staff(organizations, organization, (OrganizationRole.CLINICIAN,))
    return organization


def make_lab(
    organizations: OrganizationRepository, name: str = "Lab B", *, operational: bool = True
) -> OrganizationRecord:
    """A lab organization. By default it is enrolled and holds both lab authorities -- entry and
    release are separate (ADR-044), and a lab missing either cannot complete an order it receives.
    One membership holds both, which is a real small-laboratory configuration."""
    organization = organizations.create(org_type=OrganizationType.LAB.value, name=name)
    if operational:
        _enrol_and_staff(
            organizations,
            organization,
            (OrganizationRole.LAB_TECH, OrganizationRole.LAB_RESULT_RELEASER),
        )
    return organization


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


def make_offering(
    catalog: CatalogRepository,
    *,
    lab_organization_id: uuid.UUID,
    catalog_entry_id: uuid.UUID,
    templated: bool = True,
) -> tuple[LabOfferingRecord, bool]:
    """An active offering a NATIVE laboratory can actually complete in DHealth.

    Drop-in for ``catalog.create_offering`` -- same arguments, same ``(record, created)`` return.

    DH-MVP-ONBOARD-001's correction makes an untemplated offering unroutable for a NATIVE lab:
    DHealth's own human result entry runs against a controlled active template, so an offering
    without one is a service that could be ordered and never completed. That is a real product
    rule, so the fixture satisfies it rather than the gate being weakened to accommodate fixtures
    -- the same choice ``make_lab(operational=True)`` already makes for staffing.

    Pass ``templated=False`` to build the defective state deliberately, which is what the tests
    of this rule need.

    Reaches ``catalog._sessions`` to construct the template repository, following the convention
    ``_enrol_and_staff`` above already established in this module: a test helper composing several
    repositories over one session factory, rather than every call site threading four fixtures.
    """
    offering, created = catalog.create_offering(
        lab_organization_id=lab_organization_id, catalog_entry_id=catalog_entry_id
    )
    if templated:
        activate_result_template(catalog, offering_id=offering.id)
    return offering, created


def activate_result_template(
    catalog: CatalogRepository, *, offering_id: uuid.UUID, version: int = 1
) -> None:
    """Create and activate one minimal, synthetic result template for an offering.

    One required NUMERIC observation -- the smallest shape the repository accepts (it demands at
    least one definition and at least one required one). Nothing clinical is invented here: the
    label and code are obviously synthetic placeholders, exactly as every other fixture value in
    this module is.
    """
    templates = LabResultTemplateRepository(catalog._sessions)  # noqa: SLF001 -- see make_offering
    template, _created = templates.create_template(
        lab_offering_id=offering_id,
        version=version,
        observations=[
            TemplateObservationDefinitionInput(
                source_code="SYNTH-OBS-1",
                display_label="Valeur synthétique",
                result_type="NUMERIC",
                numeric_unit="u",
                required=True,
                sort_order=0,
            )
        ],
    )
    current = templates.get_active_templates_for_offerings([offering_id]).get(offering_id)
    if current is not None and current.id == template.id:
        return
    templates.activate_template(
        lab_offering_id=offering_id,
        template_id=template.id,
        expected_current_template_id=current.id if current else None,
    )
