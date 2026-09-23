"""DH-CONNECT-AGENT-001 — a provider's own system asking DHealth for diagnostic work.

``ORDER_SUBMISSION`` is the only contract message that **creates** rather than advances, which makes
it the only one where getting identity wrong invents a patient instead of misfiling a result. The
tests below are mostly about refusals for that reason.

Two rules carry most of the weight:

* **The whole submission fails, or none of it does.** One unrecognised test code refuses the order
  rather than creating it with the codes DHealth did recognise — a laboratory that runs four of five
  requested tests and reports four results has produced a clinically misleading report, and nothing
  downstream would know a test had been dropped.
* **An identity is taken exactly as asserted, and only for the asserting facility.** No inference,
  no demographic matching, and no accepting a patient scoped to somebody else's organization.

The codes on the wire are **DHealth catalog codes**: the connector holds the qualified mapping and
applies it on the partner's own hardware, exactly as it does for a released result's ``source_code``
in the other direction. DHealth stores the mapping's fingerprint and not the mapping, so a design
where DHealth translated would have nothing to translate with.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from exchange_support import make_lab, make_provider
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.apply import ConnectApplyError, ConnectBridge
from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    Envelope,
    FailureCategory,
    InMemoryReceiptStore,
    OrderSubmission,
    ScopedExternalId,
    SourcePractitionerRef,
)
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import LabOrder, LabOrderItem, TestCatalogEntry
from daher_adapter.exchange.orders import LabOrderRepository
from daher_adapter.exchange.organizations import OrganizationRepository


@pytest.fixture
def world(sessions: sessionmaker[Session]):
    """A provider facility, a catalog with two entries, and a bridge wired for the provider side."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    provider = make_provider(organizations, "Clinique de la Paix", operational=True)
    glucose, _ = catalog.create_entry(code="GLU", display_name="Glycémie à jeun")
    crp, _ = catalog.create_entry(code="CRP", display_name="Protéine C-réactive")
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://issuer.example",
        subject="connect-runtime",
        display_name="Runtime",
        email=None,
    )
    return {
        "sessions": sessions,
        "provider_id": provider.id,
        "identity_id": identity.id,
        "glucose_id": glucose.id,
        "crp_id": crp.id,
        "organizations": organizations,
        "catalog": catalog,
    }


def _bridge(world, *, provider_side: bool = True, organization_id=None) -> ConnectBridge:
    sessions = world["sessions"]
    kwargs = {}
    if provider_side:
        kwargs = {
            "orders": LabOrderRepository(sessions, world["organizations"], world["catalog"]),
            "patients": PatientReferenceRepository(sessions, world["organizations"]),
            "practitioners": PractitionerReferenceRepository(sessions),
        }
    return ConnectBridge(
        workflow=None,
        results=None,
        releases=None,
        amendments=None,
        receipts=InMemoryReceiptStore(),
        installation_organization_id=organization_id or world["provider_id"],
        service_identity_id=world["identity_id"],
        **kwargs,
    )


#: Fixed, because ``content_fingerprint`` covers ``source_occurred_at``. A helper that stamped
#: ``now()`` per call would make two envelopes for the *same* source event look like two
#: contradictory claims about it -- which the contract correctly refuses as a CONFLICT, and which
#: would make every replay test below fail for a reason that has nothing to do with replay.
SOURCE_OCCURRED_AT = datetime(2026, 9, 23, 7, 30, tzinfo=UTC)


def _submission(
    world, *, codes=("GLU",), patient_org=None, event="emr-evt-1", organization_id=None
) -> Envelope:
    org = organization_id or world["provider_id"]
    moment = SOURCE_OCCURRED_AT
    return Envelope(
        contract_version=CONTRACT_VERSION,
        message_id=uuid.uuid4(),
        connector_installation_id=uuid.uuid4(),
        organization_id=org,
        source_system="emr",
        source_event_id=event,
        source_occurred_at=moment,
        sent_at=moment,
        lab_order_id=None,
        payload=OrderSubmission(
            patient=ScopedExternalId(
                source_organization_id=patient_org or org,
                source_system="emr",
                source_identity_system="urn:emr:mrn",
                source_identity_value="MRN-42",
            ),
            practitioner=SourcePractitionerRef(
                source_organization_id=org,
                source_system="emr",
                source_identity_system="urn:emr:staff",
                source_identity_value="DR-7",
            ),
            external_order_id="EMR-ORDER-1",
            source_test_codes=codes,
        ),
    )


def test_OS1_a_submission_creates_a_dhealth_order(world, sessions):
    """The path that did not exist until now: a provider connector's order becomes a DHealth one."""
    bridge = _bridge(world)
    with sessions.begin() as session:
        outcome = bridge.apply(session, _submission(world))
    assert outcome.applied

    with sessions() as session:
        orders = session.scalars(select(LabOrder)).all()
        items = session.scalars(select(LabOrderItem)).all()
    assert len(orders) == 1
    assert orders[0].provider_organization_id == world["provider_id"]
    assert [item.catalog_entry_id for item in items] == [world["glucose_id"]]


def test_OS2_every_requested_code_reaches_the_order(world, sessions):
    """Two codes in, two items out. A submission is not a suggestion to run some of it."""
    bridge = _bridge(world)
    with sessions.begin() as session:
        bridge.apply(session, _submission(world, codes=("GLU", "CRP")))
    with sessions() as session:
        items = session.scalars(select(LabOrderItem)).all()
    assert {item.catalog_entry_id for item in items} == {world["glucose_id"], world["crp_id"]}


def test_OS3_one_unrecognised_code_refuses_the_whole_submission(world, sessions):
    """§42, and the assertion that matters most in this file.

    A partially created order is worse than no order: the laboratory runs what it was given, the
    report comes back complete-looking, and the dropped test is invisible to everybody.
    """
    bridge = _bridge(world)
    with pytest.raises(ConnectApplyError) as error, sessions.begin() as session:
        bridge.apply(session, _submission(world, codes=("GLU", "UNKNOWN-CODE")))
    assert error.value.category is FailureCategory.MAPPING
    assert "UNKNOWN-CODE" in str(error.value), "the refusal must name the code the partner must fix"

    with sessions() as session:
        assert session.scalars(select(LabOrder)).all() == [], "nothing may be created"


def test_OS4_a_deactivated_catalog_entry_is_not_orderable(world, sessions):
    """An inactive entry is refused exactly like a code that never existed.

    Resolution is an exact lookup against the *active* catalog — never a nearest match, never a
    superseded entry. A code DHealth has retired is a code the connector's mapping has gone stale
    on, and quietly ordering the retired test instead is the fuzzy behaviour §43 forbids.
    """
    with sessions.begin() as session:
        entry = session.get(TestCatalogEntry, world["glucose_id"])
        entry.active = False

    with pytest.raises(ConnectApplyError) as error, sessions.begin() as session:
        _bridge(world).apply(session, _submission(world))
    assert error.value.category is FailureCategory.MAPPING
    with sessions() as session:
        assert session.scalars(select(LabOrder)).all() == []


def test_OS5_a_laboratory_installation_cannot_submit_an_order(world, sessions):
    """A *laboratory* connector submitting an order, through the production wiring.

    The refusal comes from the Exchange rather than from this file: ``LabOrderRepository.create``
    calls ``require_type(PROVIDER)``, so the check that matters is one no Connect code can forget to
    make. Reported as UNSUPPORTED_WORKFLOW, which dead-letters in front of an operator -- retrying
    is pointless, because no amount of it turns a laboratory into a provider.

    Earlier this test asserted that a bridge *built without* the provider repositories refused.
    That stopped being the production shape when the default factory started wiring them
    unconditionally, so it was testing an arrangement that no longer exists anywhere.
    """
    lab = make_lab(world["organizations"], "Laboratoire Central", operational=False)
    bridge = _bridge(world, organization_id=lab.id)
    with pytest.raises(ConnectApplyError) as error, sessions.begin() as session:
        bridge.apply(session, _submission(world, organization_id=lab.id))
    assert error.value.category is FailureCategory.UNSUPPORTED_WORKFLOW

    with sessions() as session:
        assert session.scalars(select(LabOrder)).all() == []


def test_OS5b_a_bridge_with_no_provider_repositories_refuses_rather_than_half_creating(
    world, sessions
):
    """The other refusal, kept: a bridge that was handed no order repository says so.

    Not the production wiring any more, but the guard is what makes the optional constructor
    arguments safe -- without it a mis-wired bridge would raise ``AttributeError`` on ``None``
    somewhere inside the submission, which is a TEMPORARY failure that retries forever rather than
    a permanent one an operator sees.
    """
    bridge = _bridge(world, provider_side=False)
    with pytest.raises(ConnectApplyError) as error, sessions.begin() as session:
        bridge.apply(session, _submission(world))
    assert error.value.category is FailureCategory.UNSUPPORTED_WORKFLOW


def test_OS6_a_patient_scoped_to_another_organization_is_refused(world, sessions):
    """`_guard_binding` checks the envelope; this checks what is *inside* it.

    A submission whose patient identity is scoped to a different facility is a cross-tenant identity
    assertion — and since this is the one message that creates a patient reference, accepting it
    would mint an identity for an organization that never asserted it.
    """
    bridge = _bridge(world)
    stranger = uuid.uuid4()
    with pytest.raises(ConnectApplyError) as error, sessions.begin() as session:
        bridge.apply(session, _submission(world, patient_org=stranger))
    assert error.value.category is FailureCategory.VALIDATION
    with sessions() as session:
        assert session.scalars(select(LabOrder)).all() == []


def test_OS7_the_same_source_event_creates_one_order(world, sessions):
    """At-least-once transport means an honest retry arrives twice.

    Caught by the replay receipt on the second call; the idempotency key is the belt underneath it,
    for the case where the receipt store is fresh but the order already exists.
    """
    bridge = _bridge(world)
    with sessions.begin() as session:
        first = bridge.apply(session, _submission(world))
    with sessions.begin() as session:
        second = bridge.apply(session, _submission(world))
    assert first.applied
    assert second.replayed and not second.applied
    with sessions() as session:
        assert len(session.scalars(select(LabOrder)).all()) == 1


def test_OS8_a_fresh_receipt_store_still_does_not_duplicate_the_order(world, sessions):
    """The idempotency key doing its job with the replay check out of the way.

    This is the restart case: a runtime that lost its receipts must not turn one clinical order into
    two, and the key is scoped by ``replay_scope`` so two facilities numbering events from 1 cannot
    collide.
    """
    with sessions.begin() as session:
        _bridge(world).apply(session, _submission(world))
    with sessions.begin() as session:
        outcome = _bridge(world).apply(session, _submission(world))
    assert outcome.replayed and not outcome.applied
    with sessions() as session:
        assert len(session.scalars(select(LabOrder)).all()) == 1


def test_OS9_a_contradicting_resubmission_is_refused_not_applied(world, sessions):
    """Same source event, different content. Refused, and no second order.

    Found by accident: an early version of ``_submission`` stamped ``now()`` per call, so two
    envelopes for one event carried different ``source_occurred_at`` values and the bridge correctly
    called it a conflict. Worth keeping as a deliberate test, because ``ORDER_SUBMISSION`` is the
    one message type where "resolve the conflict by applying the newer one" would create a second
    clinical order rather than overwrite a field.
    """
    bridge = _bridge(world)
    with sessions.begin() as session:
        bridge.apply(session, _submission(world, codes=("GLU",)))

    with pytest.raises(ConnectApplyError) as error, sessions.begin() as session:
        bridge.apply(session, _submission(world, codes=("CRP",)))
    assert error.value.category is FailureCategory.CONFLICT

    with sessions() as session:
        assert len(session.scalars(select(LabOrder)).all()) == 1
