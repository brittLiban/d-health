import pytest
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.organizations import InvalidOrganizationType, OrganizationRepository


def test_provider_org_can_exist(sessions: sessionmaker[Session]) -> None:
    repo = OrganizationRepository(sessions)
    record = repo.create(org_type="PROVIDER", name="Clinic A")
    assert record.org_type == "PROVIDER"
    assert repo.get(record.id).name == "Clinic A"


def test_lab_org_can_exist(sessions: sessionmaker[Session]) -> None:
    repo = OrganizationRepository(sessions)
    record = repo.create(org_type="LAB", name="Lab B")
    assert record.org_type == "LAB"
    assert repo.get(record.id).name == "Lab B"


def test_invalid_organization_type_refused(sessions: sessionmaker[Session]) -> None:
    repo = OrganizationRepository(sessions)
    with pytest.raises(InvalidOrganizationType):
        repo.create(org_type="HOSPITAL", name="Not A Valid Type")
