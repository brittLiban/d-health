from exchange_support import make_patient_reference, make_practitioner_reference, make_provider
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.organizations import OrganizationRepository


def test_patient_source_identity_exact(sessions: sessionmaker[Session]) -> None:
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    provider = make_provider(organizations)

    record = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="P-1"
    )

    assert record.source_identity_system == "urn:daher-health:provider-portal-patient-id"
    assert record.source_identity_value == "P-1"
    assert patients.get(record.id).id == record.id


def test_practitioner_source_identity_exact(sessions: sessionmaker[Session]) -> None:
    organizations = OrganizationRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    provider = make_provider(organizations)

    record = make_practitioner_reference(
        practitioners, provider_org_id=provider.id, source_identity_value="D-1"
    )

    assert record.source_identity_value == "D-1"
    assert practitioners.get(record.id).id == record.id


def test_no_fuzzy_matching_two_similar_patients_stay_distinct(
    sessions: sessionmaker[Session],
) -> None:
    """Two different exact source identities, even with an identical display name, must never be
    merged -- there is no demographic/fuzzy matching code path in this repository."""
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    provider = make_provider(organizations)

    first = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="P-100",
        display_name="Amina Hassan",
    )
    second = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="P-200",
        display_name="Amina Hassan",
    )

    assert first.id != second.id


def test_same_source_identity_returns_existing_reference_not_a_duplicate(
    sessions: sessionmaker[Session],
) -> None:
    """Same source identity, called twice, must resolve to exactly one reference -- never an
    ambiguous second row for the identical exact identity."""
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    provider = make_provider(organizations)

    first = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="P-1"
    )
    second = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="P-1"
    )

    assert first.id == second.id


def test_no_source_identity_collision_across_organizations_patient(
    sessions: sessionmaker[Session],
) -> None:
    """DH-EX-001R: Org A and Org B both using source_system X, identity_system S, value '123'
    must NOT collapse into one DHealth patient reference. The MVP explicitly has no cross-org MPI
    -- a source identity must not accidentally become a global patient identity merely because two
    independent organizations reuse the same identifier namespace/value."""
    organizations = OrganizationRepository(sessions)
    patients = PatientReferenceRepository(sessions)
    org_a = make_provider(organizations, name="Org A")
    org_b = make_provider(organizations, name="Org B")

    from_a = patients.get_or_create(
        source_organization_id=org_a.id,
        source_system="X",
        source_identity_system="S",
        source_identity_value="123",
    )
    from_b = patients.get_or_create(
        source_organization_id=org_b.id,
        source_system="X",
        source_identity_system="S",
        source_identity_value="123",
    )

    assert from_a.id != from_b.id
    assert from_a.source_organization_id == org_a.id
    assert from_b.source_organization_id == org_b.id
    # Same org, same tuple, called again -> still resolves back to its own org's existing row.
    replay_a = patients.get_or_create(
        source_organization_id=org_a.id,
        source_system="X",
        source_identity_system="S",
        source_identity_value="123",
    )
    assert replay_a.id == from_a.id


def test_no_source_identity_collision_across_organizations_practitioner(
    sessions: sessionmaker[Session],
) -> None:
    organizations = OrganizationRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    org_a = make_provider(organizations, name="Org A")
    org_b = make_provider(organizations, name="Org B")

    from_a = practitioners.get_or_create(
        source_organization_id=org_a.id,
        source_system="X",
        source_identity_system="S",
        source_identity_value="123",
    )
    from_b = practitioners.get_or_create(
        source_organization_id=org_b.id,
        source_system="X",
        source_identity_system="S",
        source_identity_value="123",
    )

    assert from_a.id != from_b.id
