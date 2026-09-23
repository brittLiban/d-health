"""DH-CONNECT-AGENT-001 §36–§37, §43 — exact correlation, and the absence of anything fuzzy.

Correlation is tested here directly rather than only through the applier, because it carries a
guarantee the applier's tests cannot express: **there is no matching in this system, and the way
that is enforced is that there is nothing to match on.** An external identifier either has a row
or it does not; "does not" is a refusal, never a search for the nearest thing.

The conflict cases matter most. A partner identifier that arrives bound to a different DHealth order
is the one mistake that moves a clinical result onto another patient's chart, and every path that
could do it is refused below.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from exchange_support import make_patient_reference, make_practitioner_reference, make_provider
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import fingerprint
from daher_adapter.connect.correlation import (
    CorrelationConflict,
    CorrelationNamespace,
    CorrelationRepository,
    CorrelationUnknown,
)
from daher_adapter.connect.models import ConnectorInstallation, ConnectorInstallationStatus
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import Organization, OrganizationStatus, OrganizationType
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository


@dataclass(frozen=True)
class _Installation:
    """An installation *and* the namespace its identifiers live in.

    Two things rather than one because 0031 separated them: the installation is provenance and the
    namespace is scope, and a fixture that returned only an id would make every test below read as
    though the two were still the same fact.
    """

    id: uuid.UUID
    organization_id: uuid.UUID
    source_system: str

    @property
    def namespace(self) -> CorrelationNamespace:
        return CorrelationNamespace(
            organization_id=self.organization_id, source_system=self.source_system
        )


@pytest.fixture
def installation(sessions: sessionmaker[Session]) -> _Installation:
    with sessions.begin() as session:
        org = Organization(
            name="Laboratoire Central",
            org_type=OrganizationType.LAB.value,
            status=OrganizationStatus.ACTIVE.value,
        )
        session.add(org)
        session.flush()
        row = ConnectorInstallation(
            organization_id=org.id,
            connector_key="synthetic-lis",
            connector_version="0.1",
            contract_version="1.1",
            source_system="synthetic-lis",
            interface_type="FHIR",
            configuration_fingerprint=fingerprint({"c": 1}),
            mapping_fingerprint=fingerprint({"m": 1}),
            status=ConnectorInstallationStatus.REGISTERED.value,
        )
        session.add(row)
        session.flush()
        return _Installation(id=row.id, organization_id=org.id, source_system="synthetic-lis")


@pytest.fixture
def orders(sessions: sessionmaker[Session]):
    """Two real laboratory orders. Real because ``lab_order_id`` is a RESTRICT foreign key."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    provider = make_provider(organizations, f"Clinique {uuid.uuid4().hex[:6]}", operational=True)
    patient = make_patient_reference(
        PatientReferenceRepository(sessions, organizations), provider_org_id=provider.id
    )
    practitioner = make_practitioner_reference(
        PractitionerReferenceRepository(sessions), provider_org_id=provider.id
    )
    entry, _ = catalog.create_entry(code="GLU", display_name="Glycémie")
    repository = LabOrderRepository(sessions, organizations, catalog)
    made = []
    for _ in range(2):
        created = repository.create(
            idempotency_key=f"corr-{uuid.uuid4()}",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        )
        made.append(created.order.id)
    return made


def test_CR1_an_accession_resolves_to_the_order_it_was_bound_to(sessions, installation, orders):
    """The happy path, and the only path: an exact recorded binding."""
    repository = CorrelationRepository()
    with sessions.begin() as session:
        repository.bind_order(
            session,
            namespace=installation.namespace,
            external_order_id="ACC-1",
            lab_order_id=orders[0],
            created_by_installation_id=installation.id,
        )
    with sessions() as session:
        assert (
            repository.resolve_order(
                session, namespace=installation.namespace, external_order_id="ACC-1"
            )
            == orders[0]
        )


def test_CR2_an_unknown_accession_is_refused_not_guessed(sessions, installation):
    """§43. There is no similarity function, no threshold, and no demographic fallback.

    A laboratory publishing a result for an accession DHealth never issued is either talking about
    somebody else's order or has lost track of its own. Both are worth a human looking.
    """
    with sessions() as session, pytest.raises(CorrelationUnknown):
        CorrelationRepository().resolve_order(
            session, namespace=installation.namespace, external_order_id="ACC-NEVER-SEEN"
        )


def test_CR3_rebinding_the_same_pair_is_idempotent(sessions, installation, orders):
    """The message that asserts a binding is delivered at-least-once; repeating it must be free."""
    repository = CorrelationRepository()
    with sessions.begin() as session:
        first = repository.bind_order(
            session,
            namespace=installation.namespace,
            external_order_id="ACC-1",
            lab_order_id=orders[0],
            created_by_installation_id=installation.id,
        )
        second = repository.bind_order(
            session,
            namespace=installation.namespace,
            external_order_id="ACC-1",
            lab_order_id=orders[0],
            created_by_installation_id=installation.id,
        )
        assert first.id == second.id


def test_CR4_an_accession_cannot_be_repointed_to_another_order(sessions, installation, orders):
    """The mistake that moves a clinical result between patients. Refused, never updated."""
    repository = CorrelationRepository()
    with sessions.begin() as session:
        repository.bind_order(
            session,
            namespace=installation.namespace,
            external_order_id="ACC-1",
            lab_order_id=orders[0],
            created_by_installation_id=installation.id,
        )
    with sessions.begin() as session, pytest.raises(CorrelationConflict):
        repository.bind_order(
            session,
            namespace=installation.namespace,
            external_order_id="ACC-1",
            lab_order_id=orders[1],
            created_by_installation_id=installation.id,
        )


def test_CR5_one_order_cannot_have_two_accessions(sessions, installation, orders):
    """The reverse direction, checked explicitly as well as by the partial unique index.

    One order has one accession at one laboratory; a second means their system duplicated it, which
    is a real integration fault and not something to absorb silently.
    """
    repository = CorrelationRepository()
    with sessions.begin() as session:
        repository.bind_order(
            session,
            namespace=installation.namespace,
            external_order_id="ACC-1",
            lab_order_id=orders[0],
            created_by_installation_id=installation.id,
        )
    with sessions.begin() as session, pytest.raises(CorrelationConflict) as error:
        repository.bind_order(
            session,
            namespace=installation.namespace,
            external_order_id="ACC-2",
            lab_order_id=orders[0],
            created_by_installation_id=installation.id,
        )
    assert "ACC-1" in str(error.value), "the refusal should name the accession already bound"


def test_CR6_one_order_may_have_many_reports(sessions, installation, orders):
    """The original release and every amendment are separate reports about the same order.

    A unique constraint here would make publishing a correction impossible, which is why the reverse
    uniqueness is deliberately order-only.
    """
    repository = CorrelationRepository()
    with sessions.begin() as session:
        for report in ("RPT-1", "RPT-2", "RPT-3"):
            repository.bind_report(
                session,
                namespace=installation.namespace,
                external_report_id=report,
                lab_order_id=orders[0],
                created_by_installation_id=installation.id,
            )
    with sessions() as session:
        for report in ("RPT-1", "RPT-2", "RPT-3"):
            assert (
                repository.resolve_report(
                    session, namespace=installation.namespace, external_report_id=report
                )
                == orders[0]
            )


def test_CR7_a_report_still_names_exactly_one_order(sessions, installation, orders):
    """Many reports per order, and never one report for two orders.

    This is what makes the amendment check meaningful: an amendment claiming to correct a report
    from a different order is refused rather than applied across the boundary.
    """
    repository = CorrelationRepository()
    with sessions.begin() as session:
        repository.bind_report(
            session,
            namespace=installation.namespace,
            external_report_id="RPT-1",
            lab_order_id=orders[0],
            created_by_installation_id=installation.id,
        )
    with sessions.begin() as session, pytest.raises(CorrelationConflict):
        repository.bind_report(
            session,
            namespace=installation.namespace,
            external_report_id="RPT-1",
            lab_order_id=orders[1],
            created_by_installation_id=installation.id,
        )


def test_CR8_the_accession_for_an_order_is_none_until_the_laboratory_says_one(
    sessions, installation, orders
):
    """ "Not yet" is an ordinary state, not a fault.

    DHealth queues an order notification *before* the laboratory has any accession for it, so the
    reverse lookup has to be able to answer "none" without raising.
    """
    repository = CorrelationRepository()
    with sessions() as session:
        assert (
            repository.order_correlation_for(
                session, organization_id=installation.organization_id, lab_order_id=orders[0]
            )
            is None
        )
    with sessions.begin() as session:
        repository.bind_order(
            session,
            namespace=installation.namespace,
            external_order_id="ACC-9",
            lab_order_id=orders[0],
            created_by_installation_id=installation.id,
        )
    with sessions() as session:
        assert (
            repository.order_correlation_for(
                session, organization_id=installation.organization_id, lab_order_id=orders[0]
            ).external_order_id
            == "ACC-9"
        )


def test_CR9_there_is_no_demographic_field_to_match_on(sessions, installation):
    """The strongest form of "no fuzzy matching": a model that cannot express the inputs.

    Anyone wanting to add similarity matching would have to add a field first, which is a visible
    change rather than a quiet one.
    """
    from daher_adapter.connect.runtime_models import ConnectorCorrelation

    columns = set(ConnectorCorrelation.__table__.columns.keys())
    for forbidden in ("patient_name", "display_name", "date_of_birth", "similarity", "score"):
        assert forbidden not in columns
    assert columns == {
        "id",
        # 0031: the durable scope. The installation moved to a provenance column and stopped being
        # part of any key, because an external identifier outlives the adapter build that reported
        # it -- see the module docstring on ``ConnectorCorrelation``.
        "organization_id",
        "source_system",
        "created_by_connector_installation_id",
        "correlation_kind",
        "external_id",
        "lab_order_id",
        "created_at",
    }
