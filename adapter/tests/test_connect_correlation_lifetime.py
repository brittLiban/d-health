"""DH-CONNECT-AGENT-001 correction — an external identifier outlives the connector that reported it.

The bug these tests exist for was invisible while only one connector had ever existed. Correlation
identity was ``(connector_installation_id, correlation_kind, external_id)``, which quietly asserted
that two lifetimes are one:

    a connector installation   one DHealth adapter build and configuration. Retired and replaced
                               whenever a version moves or a fingerprint changes -- routine.

    an external identifier     a name in the *partner's* system. ``ORDER-123`` means what MedSys
                               has always meant by it, and DHealth upgrading an adapter is not an
                               event in MedSys's world at all.

So a facility that upgraded its connector lost the ability to file results for every order placed
before the upgrade. Not with an error -- the correlation simply stopped resolving, and the report
went nowhere, for a reason the partner could not have caused or noticed.

The scope is now ``(organization_id, source_system, correlation_kind, external_id)``. What these
tests pin is that the two halves of the durable scope are *both* load-bearing (CL4, CL5), that the
installation is provenance rather than identity (CL1, CL2), that nothing falls back to whatever
connector happens to be registered (CL6), and — the separation that is easiest to lose — that
carrying an identifier's meaning across a replacement is not carrying its **qualification** (CL3).
"""

from __future__ import annotations

import uuid

import pytest
from exchange_auth_support import ISSUER
from exchange_support import make_patient_reference, make_practitioner_reference, make_provider
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import fingerprint
from daher_adapter.connect.correlation import (
    CorrelationNamespace,
    CorrelationRepository,
    CorrelationUnknown,
)
from daher_adapter.connect.qualification import qualification_verdict_in_session
from daher_adapter.connect.repository import ConnectorInstallationRepository
from daher_adapter.connect.runtime_models import ConnectorCorrelation
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import OrganizationType, ParticipationMode
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository

MEDSYS = "medsys"
EPIC = "epic"
EXTERNAL_ORDER = "ORDER-123"


@pytest.fixture
def world(sessions: sessionmaker[Session]):
    """One CONNECT provider, its catalog, and the machinery to make real orders and installations.

    Real orders because ``lab_order_id`` is a RESTRICT foreign key, and real installations because
    the point of these tests is what happens when one is retired and another registered.
    """
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    provider = make_provider(organizations, "Clinique de la Paix", operational=False)
    FacilityEnrollmentRepository(sessions).create(
        organization_id=provider.id, participation_mode=ParticipationMode.CONNECT
    )
    entry, _ = catalog.create_entry(code="GLU", display_name="Glycémie à jeun")
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer=ISSUER, subject="platform-admin", display_name="Platform", email=None
    )
    return {
        "sessions": sessions,
        "organizations": organizations,
        "catalog": catalog,
        "provider_id": provider.id,
        "entry_id": entry.id,
        "actor_id": identity.id,
        "installations": ConnectorInstallationRepository(sessions),
    }


def _register(world, *, source_system: str, version: str = "1.0", client: str | None = None):
    """Register an installation for this facility. At most one may be live at a time."""
    record = world["installations"].register(
        organization_id=world["provider_id"],
        connector_key="synthetic-emr",
        connector_version=version,
        source_system=source_system,
        interface_type="FHIR",
        configuration_fingerprint=fingerprint({"endpoint": f"https://{source_system}.invalid"}),
        mapping_fingerprint=fingerprint({"mapping_revision": [[source_system, "GLU"]]}),
        actor_identity_id=world["actor_id"],
    )
    if client is not None:
        world["installations"].set_service_identity(
            installation_id=record.id,
            service_client_id=client,
            actor_identity_id=world["actor_id"],
        )
    return record


def _order(world, provider_id: uuid.UUID | None = None) -> uuid.UUID:
    sessions = world["sessions"]
    organizations = world["organizations"]
    org = provider_id or world["provider_id"]
    patient = make_patient_reference(
        PatientReferenceRepository(sessions, organizations), provider_org_id=org
    )
    practitioner = make_practitioner_reference(
        PractitionerReferenceRepository(sessions), provider_org_id=org
    )
    created = LabOrderRepository(sessions, organizations, world["catalog"]).create(
        idempotency_key=f"cl-{uuid.uuid4()}",
        provider_organization_id=org,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=world["entry_id"])],
    )
    return created.order.id


def _namespace(world, source_system: str, organization_id: uuid.UUID | None = None):
    return CorrelationNamespace(
        organization_id=organization_id or world["provider_id"], source_system=source_system
    )


def test_CL1_a_binding_is_recorded_under_the_organization_and_source_system(world, sessions):
    """The durable scope, asserted on the row itself rather than only through a lookup.

    The installation is still recorded -- provenance is worth having -- and is deliberately *not*
    what the identifier is filed under.
    """
    v1 = _register(world, source_system=MEDSYS)
    order_id = _order(world)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=_namespace(world, MEDSYS),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=order_id,
            created_by_installation_id=v1.id,
        )

    with sessions() as session:
        row = session.scalar(select(ConnectorCorrelation))
    assert row.organization_id == world["provider_id"]
    assert row.source_system == MEDSYS
    assert row.external_id == EXTERNAL_ORDER
    assert row.created_by_connector_installation_id == v1.id, "provenance is kept"


def test_CL2_a_replacement_installation_resolves_the_predecessors_identifiers(world, sessions):
    """**The bug.** Retire v1, register v2 for the same facility and the same EMR, and ``ORDER-123``
    still means what it always meant.

    Under the old installation-scoped key this lookup returned nothing, so a result released after a
    routine connector upgrade could not be filed back into the system that ordered it. Nothing the
    partner did caused that and nothing they could do would reveal it.
    """
    v1 = _register(world, source_system=MEDSYS, version="1.0")
    order_id = _order(world)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=_namespace(world, MEDSYS),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=order_id,
            created_by_installation_id=v1.id,
        )

    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["actor_id"])
    v2 = _register(world, source_system=MEDSYS, version="2.0")
    assert v2.id != v1.id

    with sessions() as session:
        resolved = CorrelationRepository().resolve_order(
            session, namespace=_namespace(world, MEDSYS), external_order_id=EXTERNAL_ORDER
        )
        reverse = CorrelationRepository().order_correlation_for(
            session, organization_id=world["provider_id"], lab_order_id=order_id
        )
    assert resolved == order_id
    assert reverse.external_order_id == EXTERNAL_ORDER
    assert reverse.namespace.source_system == MEDSYS
    assert reverse.created_by_connector_installation_id == v1.id, (
        "the retired installation remains the provenance; it is simply not the key any more"
    )


def test_CL3_a_replacement_does_not_inherit_its_predecessors_qualification(world, sessions):
    """Correlation continuity and qualification continuity are **separate facts**.

    This is the half that would be dangerous to get wrong in the other direction. v2 inherits the
    meaning of the source system's identifiers -- that is a fact about the partner's world. It does
    not inherit evidence that *it* works, which is a fact about v2 and has to be earned.
    """
    v1 = _register(world, source_system=MEDSYS, version="1.0")
    order_id = _order(world)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=_namespace(world, MEDSYS),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=order_id,
            created_by_installation_id=v1.id,
        )

    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["actor_id"])
    _register(world, source_system=MEDSYS, version="2.0")

    with sessions() as session:
        # The identifier carried across...
        assert (
            CorrelationRepository().resolve_order(
                session, namespace=_namespace(world, MEDSYS), external_order_id=EXTERNAL_ORDER
            )
            == order_id
        )
        # ...and the qualification did not.
        verdict = qualification_verdict_in_session(
            session, world["provider_id"], OrganizationType.PROVIDER.value
        )
    assert not verdict.qualified, "a new installation starts unqualified, whatever it inherited"


def test_CL4_the_same_identifier_in_two_source_systems_does_not_collide(world, sessions):
    """§6. Epic's ``ORDER-123`` is not MedSys's ``ORDER-123``, and there is no handoff between them.

    A facility that migrates from one EMR to another gets a new namespace, and the old bindings stay
    exactly where they are. The two rows coexist because the source system is part of the key --
    which is also why a migration cannot silently inherit a predecessor's numbering.
    """
    v1 = _register(world, source_system=MEDSYS)
    medsys_order = _order(world)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=_namespace(world, MEDSYS),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=medsys_order,
            created_by_installation_id=v1.id,
        )

    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["actor_id"])
    v2 = _register(world, source_system=EPIC)
    epic_order = _order(world)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=_namespace(world, EPIC),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=epic_order,
            created_by_installation_id=v2.id,
        )

    repository = CorrelationRepository()
    with sessions() as session:
        assert (
            repository.resolve_order(
                session, namespace=_namespace(world, MEDSYS), external_order_id=EXTERNAL_ORDER
            )
            == medsys_order
        )
        assert (
            repository.resolve_order(
                session, namespace=_namespace(world, EPIC), external_order_id=EXTERNAL_ORDER
            )
            == epic_order
        )
    assert medsys_order != epic_order, "two different orders, one string, no confusion"


def test_CL5_two_organizations_may_use_the_same_system_name_and_identifier(world, sessions):
    """Tenant scoping survived the rescope, and is tested rather than assumed.

    Two clinics both running MedSys, both numbering from the same series, is not a hypothetical --
    it is what happens when a vendor ships the same default configuration twice. The organization is
    the other half of the key precisely so that one clinic's ``ORDER-123`` is never the other's.
    """
    organizations = world["organizations"]
    other = make_provider(organizations, "Clinique du Port", operational=False)
    FacilityEnrollmentRepository(world["sessions"]).create(
        organization_id=other.id, participation_mode=ParticipationMode.CONNECT
    )
    mine = _register(world, source_system=MEDSYS)
    theirs = world["installations"].register(
        organization_id=other.id,
        connector_key="synthetic-emr",
        connector_version="1.0",
        source_system=MEDSYS,
        interface_type="FHIR",
        configuration_fingerprint=fingerprint({"endpoint": "https://medsys.invalid/2"}),
        mapping_fingerprint=fingerprint({"mapping_revision": [[MEDSYS, "GLU"]]}),
        actor_identity_id=world["actor_id"],
    )

    my_order = _order(world)
    their_order = _order(world, provider_id=other.id)
    repository = CorrelationRepository()
    with sessions.begin() as session:
        repository.bind_order(
            session,
            namespace=_namespace(world, MEDSYS),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=my_order,
            created_by_installation_id=mine.id,
        )
        repository.bind_order(
            session,
            namespace=_namespace(world, MEDSYS, organization_id=other.id),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=their_order,
            created_by_installation_id=theirs.id,
        )

    with sessions() as session:
        assert (
            repository.resolve_order(
                session, namespace=_namespace(world, MEDSYS), external_order_id=EXTERNAL_ORDER
            )
            == my_order
        )
        assert (
            repository.resolve_order(
                session,
                namespace=_namespace(world, MEDSYS, organization_id=other.id),
                external_order_id=EXTERNAL_ORDER,
            )
            == their_order
        )


def test_CL6_an_unknown_identifier_is_refused_rather_than_resolved_against_a_live_connector(
    world, sessions
):
    """§43, restated for the new scope: having *a* registered connector resolves nothing.

    The failure this forbids is a lookup that, finding no binding, falls back to whichever
    installation the facility currently has -- which is how a report reaches a system that never
    ordered it. There is no fallback here because there is nothing to fall back *to*: the query is
    an equality on a namespace and an identifier, and it either matches a row or raises.
    """
    _register(world, source_system=MEDSYS)
    with sessions() as session, pytest.raises(CorrelationUnknown):
        CorrelationRepository().resolve_order(
            session, namespace=_namespace(world, MEDSYS), external_order_id="ORDER-NEVER-SEEN"
        )

    # And the reverse direction: an order this facility never named through any connector.
    with sessions() as session:
        assert (
            CorrelationRepository().order_correlation_for(
                session, organization_id=world["provider_id"], lab_order_id=_order(world)
            )
            is None
        )


def test_CL7_provenance_survives_retirement_without_authorising_anything(world, sessions):
    """The retired installation is still named on the row, and is still refused as a transport.

    Both halves matter. Keeping the provenance is how "who told us this, under which configuration"
    stays answerable years later; refusing it as a carrier is what stops a retired credential moving
    clinical data. ``_eligible_installation`` is what enforces the second, and it is tested here
    through its own module rather than through a delivery, so the reason is legible.
    """
    from daher_adapter.connect.result_hook import eligible_installation

    v1 = _register(world, source_system=MEDSYS)
    order_id = _order(world)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=_namespace(world, MEDSYS),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=order_id,
            created_by_installation_id=v1.id,
        )
    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["actor_id"])

    with sessions() as session:
        row = session.scalar(select(ConnectorCorrelation))
        assert row.created_by_connector_installation_id == v1.id
        assert eligible_installation(session, _namespace(world, MEDSYS)) is None, (
            "a retired installation never carries traffic again"
        )


def test_CL8_a_suspended_installation_is_still_an_eligible_carrier(world, sessions):
    """Suspension is a pause, not an erasure -- and this is where that distinction is decided.

    The delivery is queued against the suspended installation and waits there: nothing can claim it,
    because ``resolve_service_principal`` refuses a suspended installation, and the moment it is
    restored the agent drains it. Excluding it here instead would mean a result that became due
    during a suspension was never queued at all, and nothing would remember it was owed.
    """
    from daher_adapter.connect.result_hook import eligible_installation

    v1 = _register(world, source_system=MEDSYS)
    world["installations"].suspend(installation_id=v1.id, actor_identity_id=world["actor_id"])
    with sessions() as session:
        eligible = eligible_installation(session, _namespace(world, MEDSYS))
    assert eligible is not None and eligible.id == v1.id


def test_CL9_an_installation_for_a_different_source_system_is_not_an_eligible_carrier(
    world, sessions
):
    """§6 again, at the transport layer rather than the lookup layer.

    A clinic that replaced MedSys with Epic has an Epic connector and MedSys correlations. Handing
    the Epic connector a report for a MedSys order would file it against an identifier Epic has
    never issued -- or worse, against one it has issued for something else entirely.
    """
    from daher_adapter.connect.result_hook import eligible_installation

    v1 = _register(world, source_system=MEDSYS)
    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["actor_id"])
    _register(world, source_system=EPIC)

    with sessions() as session:
        assert eligible_installation(session, _namespace(world, MEDSYS)) is None
        assert eligible_installation(session, _namespace(world, EPIC)) is not None


def test_CL10_one_dhealth_order_may_be_named_by_a_provider_and_a_laboratory_at_once(
    world, sessions
):
    """Both ends of one order, both CONNECT, and neither binding is a duplicate of the other.

    Worth pinning because the reverse-uniqueness check is the one most likely to be "simplified"
    later into a check on ``lab_order_id`` alone -- which would make the laboratory's accession look
    like a contradiction of the provider's order id and refuse the second message every time both
    ends of a route are connected.
    """
    provider_installation = _register(world, source_system=MEDSYS)
    order_id = _order(world)

    organizations = world["organizations"]
    from exchange_support import make_lab

    lab = make_lab(organizations, "Laboratoire Central", operational=False)
    FacilityEnrollmentRepository(world["sessions"]).create(
        organization_id=lab.id, participation_mode=ParticipationMode.CONNECT
    )
    lab_installation = world["installations"].register(
        organization_id=lab.id,
        connector_key="synthetic-lis",
        connector_version="1.0",
        source_system="openelis",
        interface_type="FHIR",
        configuration_fingerprint=fingerprint({"endpoint": "https://lis.invalid"}),
        mapping_fingerprint=fingerprint({"mapping_revision": [["openelis", "GLU"]]}),
        actor_identity_id=world["actor_id"],
    )

    repository = CorrelationRepository()
    with sessions.begin() as session:
        repository.bind_order(
            session,
            namespace=_namespace(world, MEDSYS),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=order_id,
            created_by_installation_id=provider_installation.id,
        )
        repository.bind_order(
            session,
            namespace=CorrelationNamespace(organization_id=lab.id, source_system="openelis"),
            external_order_id="ACCESSION-77",
            lab_order_id=order_id,
            created_by_installation_id=lab_installation.id,
        )

    with sessions() as session:
        provider_side = repository.order_correlation_for(
            session, organization_id=world["provider_id"], lab_order_id=order_id
        )
        lab_side = repository.order_correlation_for(
            session, organization_id=lab.id, lab_order_id=order_id
        )
    assert provider_side.external_order_id == EXTERNAL_ORDER
    assert lab_side.external_order_id == "ACCESSION-77"

    # And the rest of the chain the result hook walks: namespace -> carrier. The hook asks for the
    # correlation with ``order.provider_organization_id``, so the two sides are separated by tenant
    # first and by source system second; asserting only the repository would leave the routing step
    # -- the one that actually decides which connector receives a clinical report -- untested here.
    from daher_adapter.connect.result_hook import eligible_installation

    with sessions() as session:
        provider_carrier = eligible_installation(session, provider_side.namespace)
        lab_carrier = eligible_installation(session, lab_side.namespace)
    assert provider_carrier.id == provider_installation.id
    assert lab_carrier.id == lab_installation.id
    assert provider_side.namespace.source_system == MEDSYS
    assert lab_side.namespace.source_system == "openelis"


def test_CL11_the_installations_registered_source_system_is_authoritative(world, sessions):
    """An agent does not get to choose which namespace it writes into.

    §8's principle applied to the namespace: the envelope's ``source_system`` is a claim, the
    installation's is authority. Were the envelope trusted, a connector registered for MedSys could
    write bindings into the Epic namespace at its own facility -- and then a released Epic result
    would resolve to an order MedSys placed. Refused at the applier, visibly, rather than recorded
    under a value the partner did not intend.
    """
    from daher_adapter.connect.inbound import _namespace_for

    installation = _register(world, source_system=MEDSYS)

    class _Delivery:
        connector_installation_id = installation.id

    class _Envelope:
        source_system = EPIC

    from daher_adapter.connect.apply import ConnectApplyError

    with sessions() as session, pytest.raises(ConnectApplyError) as error:
        _namespace_for(session, _Delivery(), world["provider_id"], _Envelope())
    assert MEDSYS in str(error.value) and EPIC in str(error.value)

    class _Matching:
        source_system = MEDSYS

    with sessions() as session:
        namespace = _namespace_for(session, _Delivery(), world["provider_id"], _Matching())
    assert namespace == _namespace(world, MEDSYS)


def test_CL12_the_reverse_lookup_refuses_to_choose_between_two_claiming_systems(world, sessions):
    """Two source systems at one facility both claiming to have ordered the same work.

    Structurally hard to reach -- one live installation per organization -- but reachable across a
    migration, and the answer must not be "pick one". There is no rule that makes either more likely
    to be right, and choosing would file a clinical report into a system that did not ask for it.
    """
    from daher_adapter.connect.correlation import CorrelationConflict

    v1 = _register(world, source_system=MEDSYS)
    order_id = _order(world)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=_namespace(world, MEDSYS),
            external_order_id=EXTERNAL_ORDER,
            lab_order_id=order_id,
            created_by_installation_id=v1.id,
        )
    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["actor_id"])
    v2 = _register(world, source_system=EPIC)
    with sessions.begin() as session:
        CorrelationRepository().bind_order(
            session,
            namespace=_namespace(world, EPIC),
            external_order_id="EPIC-9",
            lab_order_id=order_id,
            created_by_installation_id=v2.id,
        )

    with sessions() as session, pytest.raises(CorrelationConflict):
        CorrelationRepository().order_correlation_for(
            session, organization_id=world["provider_id"], lab_order_id=order_id
        )


def test_CL13_nothing_in_the_model_permits_a_fuzzy_lookup(world, sessions):
    """The structural form of §43, re-asserted after the rescope moved every key.

    A rescope is exactly when a "convenience" lookup gets added -- resolve by installation, resolve
    by organization alone, resolve by prefix. The repository's public surface is enumerated here so
    adding one is a visible change to this test rather than a quiet change to a module.
    """
    public = {name for name in dir(CorrelationRepository) if not name.startswith("_")}
    assert public == {
        "bind_order",
        "bind_report",
        "resolve_order",
        "resolve_report",
        "order_correlation_for",
    }

    # And every resolver takes a namespace, so none of them *can* answer "which order is this,
    # anywhere?" -- the question that has no safe answer.
    import inspect

    for name in ("resolve_order", "resolve_report"):
        parameters = inspect.signature(getattr(CorrelationRepository, name)).parameters
        assert "namespace" in parameters, name
