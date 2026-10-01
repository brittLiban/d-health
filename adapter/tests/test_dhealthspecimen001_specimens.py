"""DHEALTH-SPECIMEN-001: the physical specimen record, over HTTP and at the domain boundary.

What this file exists to establish, and what it deliberately does not claim.

── The one distinction the ticket turns on ──

A ``LabOrder`` is requested diagnostic work. A ``LabSpecimen`` is a physical sample instance
collected in service of that work. Before this ticket DHealth could not tell those apart: a
collection was one ``LabSampleCollection`` row per order carrying only ``recorded_at``, with
``unique(lab_order_id)`` asserting as *schema* that an order has at most one physical sample
forever. ``test_a_second_physical_collection_preserves_the_first`` and
``test_the_schema_does_not_assert_one_specimen_per_order`` are the two tests that would have been
impossible to write before, and they are the point of the file.

── The source-of-truth invariant, tested rather than asserted in prose ──

``LabSampleCollection`` stays the order-level workflow milestone and stays what
``ACCEPTED -> IN_PROGRESS`` is gated on; ``LabSpecimen`` is the physical-sample authority. From this
ticket onward a milestone is never created without a first specimen, from one ``utc_now()``
evaluated once, so the two instants are the *same value*. That is
``test_milestone_and_first_specimen_share_one_instant``, and it is a real test rather than a comment
because two separate clock reads would have differed by microseconds and falsified the invariant in
the most confusing possible way.

The one permitted asymmetry is historical:
``test_a_legacy_milestone_with_no_specimen_is_never_repaired_by_invention``.

── Retry versus a second sample ──

These are different facts and this file keeps them apart:

* ``test_retrying_the_same_collection_creates_no_second_specimen`` -- one key, one specimen.
* ``test_a_second_physical_collection_preserves_the_first`` -- a different key, two specimens.

Idempotency is deliberately **not** enforced by "one specimen per order": that would make retries
easy by making the domain false, which is why ``NC-SP-4`` exists.

── What this file does NOT establish ──

Nothing here is evidence of human usability. No real patient data appears anywhere; every patient,
practitioner, organization and identifier is synthetic. The specimen *type* question, the
human-facing accession number, bedside identity confirmation (**D-4**) and whether a mistaken
collection record can be voided are all open and are not answered by any test here.

All patient data is synthetic. No real patient data is used anywhere in this file.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import LabSpecimenResponse, build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.capabilities import SYSTEM_ROLE_TEMPLATES, Capability
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.lab_workflow import LabSpecimenRecord
from daher_adapter.exchange.models import (
    BLOCKABLE_ORDER_STATUSES,
    SPECIMEN_RECORDABLE_ORDER_STATUSES,
    ExchangeSecurityAuditEvent,
    LabOrder,
    LabOrderStatus,
    LabSampleCollection,
    LabSpecimen,
    OrganizationRole,
)
from daher_adapter.exchange.organizations import OrganizationRepository

TTL_HOURS = 168


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, tmp_path: Path) -> TestClient:
    _, public_key = keypair
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
        audit_writer=ExchangeAuditWriter(tmp_path / "exchange-audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
        patient_access_ttl_hours=TTL_HOURS,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def catalog(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> CatalogRepository:
    return CatalogRepository(sessions, organizations)


@pytest.fixture
def directory(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> LabDirectoryProfileRepository:
    return LabDirectoryProfileRepository(sessions, organizations)


@pytest.fixture
def patients(sessions: sessionmaker[Session]) -> PatientReferenceRepository:
    return PatientReferenceRepository(sessions)


# -- harness ---------------------------------------------------------------------------------------
#
# This file's own, following `test_dhealthlabreception001_intake.py`'s precedent rather than
# importing from it: importing one test module into another couples two files' fixtures and is not a
# pattern this package uses.


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _instant(value: str) -> datetime:
    """Parse an ISO-8601 instant, tolerating the trailing offset SQLite drops.

    ``DateTime(timezone=True)`` is not honoured by SQLite, so a row created in this process
    serializes with an offset while the *same row* read back serializes without one. That is a
    test-backend artifact and not product behaviour -- PostgreSQL, the only backend this
    migration chain can actually run on, preserves it -- so the tests here compare instants
    rather than strings, and the PostgreSQL qualification suite asserts the stored values
    directly instead."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _key() -> str:
    return str(uuid.uuid4())


def _bootstrap(client: TestClient, private_key, subject: str, **kwargs) -> str:
    token = make_token(private_key, subject=subject, **kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _attach(client, private_key, org_id, identity_id, subject: str, roles: list[str]) -> str:
    admin = make_token(private_key, subject=f"setup-{subject}", roles=[PLATFORM_ADMIN_ROLE])
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(admin),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["membership_id"]


def _set_capability(client, private_key, org_id, membership_id, capability: str, effect: str):
    admin = make_token(private_key, subject="setup-cap-admin", roles=[PLATFORM_ADMIN_ROLE])
    return client.put(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(admin),
        json={"effect": effect},
    )


def _clear_capability(client, private_key, org_id, membership_id, capability: str):
    admin = make_token(private_key, subject="setup-cap-admin", roles=[PLATFORM_ADMIN_ROLE])
    return client.delete(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(admin),
    )


def _priced_lab(catalog, directory, organizations, *, name: str, entries):
    lab = make_lab(organizations, name=name)
    for index, entry in enumerate(entries):
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("1000") * (index + 1),
            currency="DJF",
            expected_current_price_id=None,
        )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    return lab


def _route_order(
    client, private_key, organizations, catalog, directory, patients, *, name: str, lab=None
):
    """A clinic order routed to ``lab`` (or a fresh one), left in ``SELECTED``.

    Returns ``(provider, lab, order_id, clinician_token)``. Routing goes through the real patient
    selection path rather than a direct database write, so every downstream fact -- the selection,
    the frozen price, the routing instant -- is the one production would produce.
    """
    provider = make_provider(organizations, name=f"Prov-{name}")
    clinician_identity = _bootstrap(client, private_key, f"clin-{name}")
    _attach(client, private_key, provider.id, clinician_identity, f"clin-{name}", ["CLINICIAN"])
    clinician_token = make_token(private_key, subject=f"clin-{name}")

    patient_id = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Patient Synthetique {name}",
    ).id

    entry, _ = catalog.create_entry(code=f"DHSP-{name}", display_name=f"Analyse {name}")
    if lab is None:
        lab = _priced_lab(catalog, directory, organizations, name=f"Lab-{name}", entries=[entry])
    else:
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("1500"),
            currency="DJF",
            expected_current_price_id=None,
        )

    created = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(entry.id)]},
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    access = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access.status_code == 201, access.text
    access_token = access.json()["access_token"]

    options = client.get("/exchange/v1/patient/order/lab-options", headers=_auth(access_token))
    assert options.status_code == 200, options.text
    matching = [o for o in options.json()["options"] if o["lab"]["id"] == str(lab.id)]
    assert matching, f"lab {lab.id} was not offered: {options.json()}"
    price_ids = [line["current_price_id"] for line in matching[0]["prices"]]

    selection = client.post(
        "/exchange/v1/patient/order/selection",
        headers={**_auth(access_token), "Idempotency-Key": _key()},
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selection.status_code in (200, 201), selection.text
    return provider, lab, order_id, clinician_token


def _lab_worker(client, private_key, lab, name: str, roles: list[str] | None = None):
    identity = _bootstrap(client, private_key, f"worker-{name}")
    membership = _attach(
        client,
        private_key,
        lab.id,
        identity,
        f"worker-{name}",
        roles if roles is not None else ["LAB_TECH"],
    )
    return make_token(private_key, subject=f"worker-{name}"), identity, membership


def _accept(client, token, lab_id, order_id):
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/accept", headers=_auth(token)
    )


def _collect(client, token, lab_id, order_id, *, idempotency_key: str | None = None):
    """Record a collection. ``idempotency_key=None`` sends **no body at all**, which is DH-EX-011's
    original keyless request -- not the same thing as sending ``{"idempotency_key": null}``."""
    kwargs = {} if idempotency_key is None else {"json": {"idempotency_key": idempotency_key}}
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/record-collection",
        headers=_auth(token),
        **kwargs,
    )


def _specimens(client, token, lab_id, order_id):
    return client.get(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/specimens", headers=_auth(token)
    )


def _start(client, token, lab_id, order_id):
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/start-processing",
        headers=_auth(token),
    )


def _block(client, token, lab_id, order_id, *, reason: str, note: str | None = None):
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/block",
        headers=_auth(token),
        json={"reason": reason, "note": note},
    )


def _resolve(client, token, lab_id, order_id, block_id, *, note: str | None = None):
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/blocks/{block_id}/resolve",
        headers=_auth(token),
        json={"resolution_note": note},
    )


def _accepted_order(client, private_key, organizations, catalog, directory, patients, *, name: str):
    """The common arrangement: a routed order this laboratory has accepted, plus a worker token."""
    _provider, lab, order_id, _clinician = _route_order(
        client, private_key, organizations, catalog, directory, patients, name=name
    )
    token, identity, membership = _lab_worker(client, private_key, lab, name)
    assert _accept(client, token, lab.id, order_id).status_code == 200
    return lab, order_id, token, identity, membership


# ── the specimen exists, and is its own durable record ───────────────────────────────────────────


def test_recording_a_collection_creates_a_durable_specimen(
    client, keypair, organizations, catalog, directory, patients
):
    """The success condition of the whole ticket: DHealth can now distinguish "an order exists" from
    "a physical specimen was collected for that order", and the second has durable identity."""
    private_key, _ = keypair
    lab, order_id, token, identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="durable"
    )

    response = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert response.status_code == 201, response.text
    specimen = response.json()["specimen"]

    # A server-generated durable identifier -- not a client-chosen one, and not derived from the
    # order id (which would make it guessable from an order a rival laboratory already knew).
    assert uuid.UUID(specimen["id"])
    assert specimen["id"] != order_id
    # An authoritative relationship to exactly one LabOrder.
    assert specimen["order_id"] == order_id
    # Collection provenance: when, and on whose authenticated authority.
    assert specimen["recorded_at"] is not None
    assert specimen["recorded_by_identity_id"] == identity

    # And it survives as a readable history, not only as a response body.
    history = _specimens(client, token, lab.id, order_id)
    assert history.status_code == 200, history.text
    assert [row["id"] for row in history.json()] == [specimen["id"]]


def test_milestone_and_first_specimen_share_one_instant(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The source-of-truth invariant, as an equality rather than an approximation.

    Two ``utc_now()`` calls would differ by microseconds, and the resulting "the milestone and the
    specimen disagree by 40 microseconds" is the worst possible shape for this to fail in --
    it looks like noise, and it means the two rows are no longer one fact. One clock read,
    written twice."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="oneinstant"
    )

    response = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["recorded_at"] == body["specimen"]["recorded_at"]

    with sessions() as session:
        milestone = session.scalar(
            select(LabSampleCollection).where(
                LabSampleCollection.lab_order_id == uuid.UUID(order_id)
            )
        )
        specimen = session.scalar(
            select(LabSpecimen).where(LabSpecimen.lab_order_id == uuid.UUID(order_id))
        )
    assert milestone is not None
    assert specimen is not None
    assert milestone.recorded_at == specimen.recorded_at


def test_no_milestone_is_ever_created_without_a_specimen(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The half of the invariant that makes it enforceable: there is no code path that creates a
    milestone alone, so the equality above cannot be bypassed by some other caller.

    Checked over the *keyless* request specifically -- DH-EX-011's original bodyless POST, which is
    still what `connect/apply.py` sends. If that path had been left creating milestone-only rows the
    asymmetry would have leaked into new data forever, and the invariant would have been false for a
    live population while every keyed test still passed."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="keyless"
    )

    assert _collect(client, token, lab.id, order_id).status_code == 201

    with sessions() as session:
        milestones = session.scalars(
            select(LabSampleCollection).where(
                LabSampleCollection.lab_order_id == uuid.UUID(order_id)
            )
        ).all()
        specimens = session.scalars(
            select(LabSpecimen).where(LabSpecimen.lab_order_id == uuid.UUID(order_id))
        ).all()
    assert len(milestones) == 1
    assert len(specimens) == 1
    assert specimens[0].idempotency_key is None


def test_the_keyless_path_never_creates_a_second_specimen(
    client, keypair, organizations, catalog, directory, patients
):
    """DH-EX-011's contract, preserved byte-for-byte, **and the gap it leaves, pinned.**

    A keyless caller can record a first collection and can never record a second: the second call is
    a replay. That is right for a retry and wrong for a laboratory that genuinely took another
    sample, so it is a real limitation of the keyless path rather than a property to be proud of.
    `connect/apply.py` is the caller it currently affects -- a source LIS emitting two genuine
    collection events gets a replay on the second -- and closing it needs a Connect envelope change
    this ticket does not make. Recorded here so the gap is visible in the test suite, not only in a
    docstring."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="keylesstwice"
    )

    first = _collect(client, token, lab.id, order_id)
    assert first.status_code == 201
    second = _collect(client, token, lab.id, order_id)
    # 200, not 201: nothing was created.
    assert second.status_code == 200, second.text
    assert second.json()["specimen"]["id"] == first.json()["specimen"]["id"]

    assert len(_specimens(client, token, lab.id, order_id).json()) == 1


# ── retry versus a second physical sample ────────────────────────────────────────────────────────


def test_retrying_the_same_collection_creates_no_second_specimen(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """A double click, or a retry after an uncertain response: one logical collection, one specimen,
    one order transition's worth of state, one audit row."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="retry"
    )
    key = _key()

    first = _collect(client, token, lab.id, order_id, idempotency_key=key)
    assert first.status_code == 201, first.text
    for _ in range(4):
        replay = _collect(client, token, lab.id, order_id, idempotency_key=key)
        assert replay.status_code == 200, replay.text
        assert replay.json()["specimen"]["id"] == first.json()["specimen"]["id"]
        # The instant is never rewritten by a replay.
        assert _instant(replay.json()["specimen"]["recorded_at"]) == _instant(
            first.json()["specimen"]["recorded_at"]
        )

    assert len(_specimens(client, token, lab.id, order_id).json()) == 1

    with sessions() as session:
        specimen_events = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "lab_specimen_recorded",
                ExchangeSecurityAuditEvent.resource_id == uuid.UUID(order_id),
            )
        ).all()
        milestone_events = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "lab_sample_collection_recorded",
                ExchangeSecurityAuditEvent.resource_id == uuid.UUID(order_id),
            )
        ).all()
    # No duplicate audit event: four replays wrote nothing.
    assert len(specimen_events) == 1
    assert len(milestone_events) == 1


def test_a_second_physical_collection_preserves_the_first(
    client, keypair, organizations, catalog, directory, patients
):
    """The test that could not have been written before this ticket.

    A different idempotency key is a *different physical collection*, and the earlier specimen is
    still there afterwards -- not updated, not replaced, not shadowed. The repository can
    answer "how many physical collection records exist for this order" and "when was each
    recorded" for both.

    Note what is **not** asserted: nothing here says the second sample replaced the first, or that
    the first was rejected. The domain does not know that, so no test claims it."""
    private_key, _ = keypair
    lab, order_id, token, identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="second"
    )

    first = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert first.status_code == 201, first.text
    second = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert second.status_code == 201, second.text
    assert second.json()["specimen"]["id"] != first.json()["specimen"]["id"]

    history = _specimens(client, token, lab.id, order_id).json()
    assert [row["id"] for row in history] == [
        first.json()["specimen"]["id"],
        second.json()["specimen"]["id"],
    ], "specimen history must be oldest-first and must contain both"
    assert all(row["recorded_by_identity_id"] == identity for row in history)

    # The *milestone* is untouched by the second collection: it records when the collection step
    # began for this order, which a later sample does not change.
    assert _instant(second.json()["recorded_at"]) == _instant(first.json()["recorded_at"])


def test_the_schema_does_not_assert_one_specimen_per_order(sessions):
    """Structural: ``exchange_lab_specimens.lab_order_id`` must not be unique, alone.

    A unique constraint there would encode "an order has at most one physical sample, forever" --
    false about laboratory medicine, and the thing that would force every future recollection to
    either overwrite the earlier row or go unrecorded. The only uniqueness is
    ``(lab_order_id, idempotency_key)``, which is retry safety and not cardinality.

    Asserted against the mapped table rather than through behaviour, because a behavioural test
    would pass for the wrong reason the day somebody added the constraint but also relaxed the
    service layer."""
    table = LabSpecimen.__table__
    single_column_unique = [
        constraint
        for constraint in table.constraints
        if hasattr(constraint, "columns")
        and {column.name for column in constraint.columns} == {"lab_order_id"}
        and type(constraint).__name__ == "UniqueConstraint"
    ]
    assert single_column_unique == [], (
        "lab_order_id must not be unique on its own -- that would make the schema claim an order "
        "has at most one physical specimen forever"
    )
    assert not table.c.lab_order_id.unique

    unique_columns = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in table.constraints
        if type(constraint).__name__ == "UniqueConstraint"
    }
    assert unique_columns == {("idempotency_key", "lab_order_id")}

    # And the sibling milestone table keeps its own one-per-order constraint, which is correct for
    # a workflow milestone -- the two tables mean different things and this is where that shows.
    assert LabSampleCollection.__table__.c.lab_order_id.unique is True


# ── historical data ──────────────────────────────────────────────────────────────────────────────


def test_a_legacy_milestone_with_no_specimen_is_never_repaired_by_invention(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """A pre-0039 order: the collection milestone exists, and how many physical samples it stood for
    is unknown. DHealth must say so rather than manufacture one.

    Simulated by writing the milestone row directly, which is exactly the shape migration 0039
    leaves behind for real historical data -- and the shape it deliberately does **not** backfill,
    because a fabricated specimen would assert both a cardinality of one and a collector identity
    that the DH-EX-011 milestone never recorded."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="legacy"
    )

    with sessions() as session:
        session.add(
            LabSampleCollection(lab_order_id=uuid.UUID(order_id), lab_organization_id=lab.id)
        )
        session.commit()

    # The history is honestly empty -- not one invented row.
    assert _specimens(client, token, lab.id, order_id).json() == []

    # A keyless replay reports the milestone and `specimen: null`, which means "unknown", never
    # "collection did not happen".
    replay = _collect(client, token, lab.id, order_id)
    assert replay.status_code == 200, replay.text
    assert replay.json()["specimen"] is None
    assert replay.json()["recorded_at"] is not None

    # Nothing was written by that replay.
    with sessions() as session:
        assert (
            session.scalars(
                select(LabSpecimen).where(LabSpecimen.lab_order_id == uuid.UUID(order_id))
            ).all()
            == []
        )

    # And a *keyed* call can still record this laboratory's first real specimen for that order
    # going forward -- the legacy gap is not a permanent block on the order.
    forward = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert forward.status_code == 201, forward.text
    assert len(_specimens(client, token, lab.id, order_id).json()) == 1


# ── lifecycle ────────────────────────────────────────────────────────────────────────────────────


def test_the_recordable_statuses_match_the_blockable_ones(sessions):
    """``SPECIMEN_RECORDABLE_ORDER_STATUSES`` and ``BLOCKABLE_ORDER_STATUSES`` are the same two
    values, and that equality is asserted here rather than expressed by sharing one object.

    Both mean "work this laboratory has genuinely taken and has not yet released". They are separate
    frozensets because they answer different questions, and a future change to either must be a
    deliberate decision that reddens this test rather than a silent change to the other."""
    assert SPECIMEN_RECORDABLE_ORDER_STATUSES == BLOCKABLE_ORDER_STATUSES
    assert SPECIMEN_RECORDABLE_ORDER_STATUSES == frozenset({"ACCEPTED", "IN_PROGRESS"})


def test_a_first_specimen_is_refused_before_the_laboratory_accepts(
    client, keypair, organizations, catalog, directory, patients
):
    """``SELECTED`` is work routed here and not yet taken. DH-EX-011's rule, unchanged."""
    private_key, _ = keypair
    _provider, lab, order_id, _clinician = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="presel"
    )
    token, _identity, _membership = _lab_worker(client, private_key, lab, "presel")

    refused = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert refused.status_code == 409, refused.text
    assert _specimens(client, token, lab.id, order_id).json() == []


def test_a_legacy_in_progress_order_cannot_retroactively_acquire_a_first_milestone(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The test that discriminates "a second physical sample" from "retroactive first collection".

    An ``IN_PROGRESS`` order with *no* milestone is a pre-DH-EX-011 legacy row. Allowing a first
    milestone to be created for it would fabricate physical-event history DHealth never observed --
    so ``IN_PROGRESS`` appearing in ``SPECIMEN_RECORDABLE_ORDER_STATUSES`` must not have weakened
    this, and this test is what proves it did not."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="legacyinprog"
    )
    with sessions() as session:
        session.execute(
            LabOrder.__table__.update()
            .where(LabOrder.id == uuid.UUID(order_id))
            .values(status=LabOrderStatus.IN_PROGRESS.value)
        )
        session.commit()

    refused = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert refused.status_code == 409, refused.text
    assert _specimens(client, token, lab.id, order_id).json() == []


def test_an_additional_specimen_is_recordable_while_in_progress(
    client, keypair, organizations, catalog, directory, patients
):
    """And the other side of the same boundary: once a milestone exists, an additional physical
    sample may be recorded while the laboratory is working the order.

    This is the case ``docs/NATIVE-OPERATING-PRODUCT.md`` §16's own worked example produces -- an
    order blocked *from* ``IN_PROGRESS`` for an unusable specimen, resolved back to ``IN_PROGRESS``.
    There is no path from ``IN_PROGRESS`` back to ``ACCEPTED``, so without this a second
    sample would have been unrecordable for exactly the situation §16 describes."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="inprog"
    )
    first = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert first.status_code == 201
    assert _start(client, token, lab.id, order_id).status_code == 200

    second = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert second.status_code == 201, second.text
    assert len(_specimens(client, token, lab.id, order_id).json()) == 2


def test_no_specimen_may_be_recorded_while_the_order_is_blocked(
    client, keypair, organizations, catalog, directory, patients
):
    """``BLOCKED`` means this laboratory cannot currently continue. DHEALTH-LAB-BLOCKED-001 made
    every continuation refuse through the status checks that already existed, and this ticket must
    not punch a hole in that by accepting a specimen while blocked."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="blocked"
    )
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201

    blocked = _block(client, token, lab.id, order_id, reason="SPECIMEN_UNUSABLE")
    assert blocked.status_code == 200, blocked.text

    refused = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert refused.status_code == 409, refused.text
    assert len(_specimens(client, token, lab.id, order_id).json()) == 1


def test_the_blocked_recollection_journey_preserves_every_specimen(
    client, keypair, organizations, catalog, directory, patients
):
    """The ticket's target operational story, end to end at the API, using only existing semantics.

    collect -> block (SPECIMEN_UNUSABLE, the existing reason) -> resolve -> collect again. Both
    specimens remain, in order, with their own instants and actors.

    **No new vocabulary is introduced anywhere in this flow.** The operational reason lives in the
    block episode, exactly where DHEALTH-LAB-BLOCKED-001 put it; there is no "recollection reason",
    no specimen status, and nothing anywhere records that specimen B replaced specimen A -- because
    the workflow input never said so. D-11 is untouched: the four provisional block reasons are the
    same four, still unvalidated by any laboratory."""
    private_key, _ = keypair
    lab, order_id, token, identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="journey"
    )

    first = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert first.status_code == 201

    blocked = _block(
        client, token, lab.id, order_id, reason="SPECIMEN_UNUSABLE", note="Tube reçu inutilisable"
    )
    assert blocked.status_code == 200, blocked.text
    block_id = blocked.json()["active_block"]["id"]

    # Resolving the block does NOT erase the specimen that was there when it happened.
    assert len(_specimens(client, token, lab.id, order_id).json()) == 1

    resolved = _resolve(client, token, lab.id, order_id, block_id, note="Nouveau prélèvement prévu")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "ACCEPTED"

    second = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert second.status_code == 201, second.text

    history = _specimens(client, token, lab.id, order_id).json()
    assert len(history) == 2
    assert history[0]["id"] == first.json()["specimen"]["id"]
    assert history[1]["id"] == second.json()["specimen"]["id"]
    assert history[0]["recorded_at"] <= history[1]["recorded_at"]
    assert {row["recorded_by_identity_id"] for row in history} == {identity}

    # No specimen field anywhere says *why* the second one exists, and none should.
    for row in history:
        assert set(row) == {
            "id",
            "order_id",
            "recorded_at",
            "recorded_by_identity_id",
            "idempotency_key",
        }

    # The workflow continues normally from here.
    assert _start(client, token, lab.id, order_id).status_code == 200


def test_start_processing_still_requires_a_collection(
    client, keypair, organizations, catalog, directory, patients
):
    """DH-EX-011's gate, unweakened. The milestone is still what ``ACCEPTED -> IN_PROGRESS`` asks
    for, and modelling specimens separately must not have made the gate bypassable."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="gate"
    )

    refused = _start(client, token, lab.id, order_id)
    assert refused.status_code == 409, refused.text

    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201
    assert _start(client, token, lab.id, order_id).status_code == 200


# ── immutability ─────────────────────────────────────────────────────────────────────────────────


def test_there_is_no_route_that_can_rewrite_collection_provenance(
    sessions, keypair, tmp_path: Path
):
    """Collection provenance is historical, so no endpoint may rewrite it.

    Asserted over the router's real route table rather than by probing URLs: a probe proves
    that one guessed path is absent, and this proves no mutating specimen route exists at
    all -- including one a future author adds under a name nobody thought to guess.

    The router is built directly rather than reached through the ``client`` fixture, because
    ``TestClient`` exposes no ``app`` attribute in this httpx version."""
    _, public_key = keypair
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
        audit_writer=ExchangeAuditWriter(tmp_path / "route-audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
        patient_access_ttl_hours=TTL_HOURS,
    )
    routes = [
        (route.path, sorted(route.methods))
        for route in router.routes
        if getattr(route, "methods", None) and "specimen" in route.path
    ]
    assert routes, "expected at least the specimen history route to exist"
    for path, methods in routes:
        assert set(methods) <= {"GET", "HEAD"}, (
            f"{path} exposes {methods}: a specimen's recorded_at/recorded_by/lab_order_id are "
            f"write-once history, and no PATCH/PUT/DELETE may exist to rewrite them"
        )


def test_the_specimen_record_carries_no_mutable_clinical_field(sessions):
    """Structural guard against the fields this ticket refused to invent.

    Not "not yet supported" -- absent. `TestCatalogEntry` carries no specimen type, so a type could
    not even be derived truthfully, and `AGENTS.md`'s "never invent clinical content" rule puts the
    rest behind a clinical decision nobody has made. If a future ticket adds one of these it should
    have to delete a line here and say why."""
    columns = {column.name for column in LabSpecimen.__table__.columns}
    assert columns == {
        "id",
        "lab_order_id",
        "lab_organization_id",
        "recorded_at",
        "recorded_by_identity_id",
        "idempotency_key",
    }
    forbidden = {
        "specimen_type",
        "collected_at",
        "container",
        "container_type",
        "tube_type",
        "tube_colour",
        "anticoagulant",
        "volume",
        "volume_ml",
        "anatomical_source",
        "collection_site",
        "accession_number",
        "storage_location",
        "quality_grade",
        "rejection_reason",
        "status",
        "state",
    }
    assert columns & forbidden == set()

    # No `recollection_of_specimen_id` either: the workflow input cannot say whether a later sample
    # replaced an earlier one, and inferring it from two timestamps would be fabricated provenance.
    assert not any("recollection" in name for name in columns)


def test_the_response_shape_has_no_invented_specimen_attribute():
    """The same guard at the HTTP boundary, over the Pydantic model rather than one response
    body."""
    assert set(LabSpecimenResponse.model_fields) == {
        "id",
        "order_id",
        "recorded_at",
        "recorded_by_identity_id",
        "idempotency_key",
    }
    assert LabSpecimenResponse.model_config.get("extra") == "forbid"


def test_the_domain_record_cannot_be_reassigned_to_another_order():
    """``LabSpecimenRecord`` is frozen, so no caller can repoint a specimen at a different order
    between reading it and rendering it."""
    record = LabSpecimenRecord(
        id=uuid.uuid4(),
        lab_order_id=uuid.uuid4(),
        lab_organization_id=uuid.uuid4(),
        recorded_at=None,
        recorded_by_identity_id=uuid.uuid4(),
        idempotency_key=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.lab_order_id = uuid.uuid4()  # type: ignore[misc]


# ── authorization ────────────────────────────────────────────────────────────────────────────────


def test_collection_authority_is_the_queue_work_capability(
    client, keypair, organizations, catalog, directory, patients
):
    """No new capability. Recording a physical specimen is laboratory queue work, performed by the
    same person who accepts the order and blocks it. A `lab.specimen.collect` capability would
    be the speculative seam DHEALTH-ACCESS-CAPABILITIES-001 §3A forbids."""
    assert Capability.LAB_QUEUE_WORK in SYSTEM_ROLE_TEMPLATES[OrganizationRole.LAB_TECH]
    assert not any("specimen" in capability.value.lower() for capability in Capability), (
        "no specimen-specific capability may exist"
    )


def test_a_direct_grant_of_queue_work_is_enough_to_collect(
    client, keypair, organizations, catalog, directory, patients
):
    """Capabilities, not job titles. A member with no laboratory role at all, given
    `lab.queue.work` directly, can record a collection and read the history."""
    private_key, _ = keypair
    _provider, lab, order_id, _clinician = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="grant"
    )
    # ORG_ADMIN holds no `lab.queue.work` of its own -- the grant below is the whole authority.
    token, _identity, membership = _lab_worker(
        client, private_key, lab, "grant", roles=["ORG_ADMIN"]
    )
    assert _accept(client, token, lab.id, order_id).status_code == 403

    granted = _set_capability(
        client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value, "GRANT"
    )
    assert granted.status_code in (200, 201), granted.text

    assert _accept(client, token, lab.id, order_id).status_code == 200
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201
    assert _specimens(client, token, lab.id, order_id).status_code == 200


def test_an_explicit_deny_beats_the_role_template(
    client, keypair, organizations, catalog, directory, patients
):
    """A DENY always wins, including over a template grant -- and it must close collection and the
    specimen read together, not one of the two."""
    private_key, _ = keypair
    lab, order_id, token, _identity, membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="deny"
    )
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201

    denied = _set_capability(
        client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value, "DENY"
    )
    assert denied.status_code in (200, 201), denied.text

    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 403
    assert _specimens(client, token, lab.id, order_id).status_code == 403


def test_revoking_the_grant_takes_effect_on_the_same_token(
    client, keypair, organizations, catalog, directory, patients
):
    """No re-login required. The same bearer token that worked a moment ago is refused once the
    capability is cleared -- authority is evaluated per request, never cached in the token."""
    private_key, _ = keypair
    _provider, lab, order_id, _clinician = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="revoke"
    )
    token, _identity, membership = _lab_worker(
        client, private_key, lab, "revoke", roles=["ORG_ADMIN"]
    )
    assert _set_capability(
        client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value, "GRANT"
    ).status_code in (200, 201)
    assert _accept(client, token, lab.id, order_id).status_code == 200
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201

    cleared = _clear_capability(
        client, private_key, lab.id, membership, Capability.LAB_QUEUE_WORK.value
    )
    assert cleared.status_code in (200, 204), cleared.text

    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 403
    assert _specimens(client, token, lab.id, order_id).status_code == 403


def test_release_authority_alone_cannot_collect(
    client, keypair, organizations, catalog, directory, patients
):
    """`lab.result.release` is a separate authority and is not a weaker form of queue work. A
    releaser may publish a result and may not record a physical specimen -- the separation ADR-044
    drew, which this ticket must not blur."""
    private_key, _ = keypair
    _provider, lab, order_id, _clinician = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="releaser"
    )
    tech_token, _ti, _tm = _lab_worker(client, private_key, lab, "releasertech")
    assert _accept(client, tech_token, lab.id, order_id).status_code == 200

    releaser_token, _identity, _membership = _lab_worker(
        client, private_key, lab, "releaser", roles=["LAB_RESULT_RELEASER"]
    )
    assert (
        _collect(client, releaser_token, lab.id, order_id, idempotency_key=_key()).status_code
        == 403
    )
    assert _specimens(client, releaser_token, lab.id, order_id).status_code == 403


def test_billing_authority_alone_cannot_collect(
    client, keypair, organizations, catalog, directory, patients
):
    """`billing.operate` stays entirely separate. A cashier at the same laboratory may take payment
    and may not record a specimen."""
    private_key, _ = keypair
    _provider, lab, order_id, _clinician = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="billing"
    )
    tech_token, _ti, _tm = _lab_worker(client, private_key, lab, "billingtech")
    assert _accept(client, tech_token, lab.id, order_id).status_code == 200

    cashier_token, _identity, _membership = _lab_worker(
        client, private_key, lab, "billing", roles=["BILLING_CLERK"]
    )
    assert (
        _collect(client, cashier_token, lab.id, order_id, idempotency_key=_key()).status_code == 403
    )
    assert _specimens(client, cashier_token, lab.id, order_id).status_code == 403


def test_the_ordering_clinician_cannot_collect_for_the_destination_laboratory(
    client, keypair, organizations, catalog, directory, patients
):
    """The clinic raised the order and has no membership at the laboratory. 404, not 403 -- the
    anti-enumeration convention every tenant-scoped route in this package already uses, so a
    non-member cannot learn that an organization id is real."""
    private_key, _ = keypair
    _provider, lab, order_id, clinician_token = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="clinician"
    )
    tech_token, _ti, _tm = _lab_worker(client, private_key, lab, "clinteched")
    assert _accept(client, tech_token, lab.id, order_id).status_code == 200

    assert (
        _collect(client, clinician_token, lab.id, order_id, idempotency_key=_key()).status_code
        == 404
    )
    assert _specimens(client, clinician_token, lab.id, order_id).status_code == 404


def test_a_platform_administrator_alone_cannot_collect(
    client, keypair, organizations, catalog, directory, patients
):
    """Platform admin is not a laboratory worker. `_require_lab_tech` deliberately does not
    admit it, mirroring `_require_clinician` -- performing clinical work is not an
    administrative power."""
    private_key, _ = keypair
    lab, order_id, _token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="platadmin"
    )
    admin = make_token(private_key, subject="lonely-platform-admin", roles=[PLATFORM_ADMIN_ROLE])

    assert _collect(client, admin, lab.id, order_id, idempotency_key=_key()).status_code in (
        403,
        404,
    )
    assert _specimens(client, admin, lab.id, order_id).status_code in (403, 404)


def test_collecting_needs_no_patient_directory_authority(
    client, keypair, organizations, catalog, directory, patients
):
    """A laboratory worker holding only `lab.queue.work` -- no `patient.directory.read` anywhere --
    can record a specimen for work already routed to them.

    Requiring directory authority to collect would mean every phlebotomist needed cross-facility
    patient-directory access to do their job, which is a much larger privacy surface than the task
    needs. `LAB_TECH`'s template is asserted to not carry it, so the collection above succeeded
    without it rather than merely alongside it."""
    private_key, _ = keypair
    assert Capability.PATIENT_DIRECTORY_READ not in SYSTEM_ROLE_TEMPLATES[OrganizationRole.LAB_TECH]

    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="nodirectory"
    )
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201
    assert _specimens(client, token, lab.id, order_id).status_code == 200


# ── tenant isolation ─────────────────────────────────────────────────────────────────────────────


def test_a_rival_laboratory_can_neither_read_nor_create_a_specimen(
    client, keypair, organizations, catalog, directory, patients
):
    """A second laboratory with a real membership of its own, probing another laboratory's order
    every way it can: by the history read, by a collection attempt, by a guessed specimen UUID, and
    by naming its own organization in the path.

    Every answer is the identical 404 an unknown order id gets, so nothing distinguishes "not yours"
    from "does not exist"."""
    private_key, _ = keypair
    lab_a, order_id, token_a, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="owner"
    )
    first = _collect(client, token_a, lab_a.id, order_id, idempotency_key=_key())
    assert first.status_code == 201
    specimen_id = first.json()["specimen"]["id"]

    lab_b = _priced_lab(catalog, directory, organizations, name="RivalLab", entries=[])
    token_b, _ib, _mb = _lab_worker(client, private_key, lab_b, "rival")

    # Reading the victim's specimen history, through the rival's own organization path.
    rival_read = _specimens(client, token_b, lab_b.id, order_id)
    assert rival_read.status_code == 404
    # Creating one against the victim's order.
    assert _collect(client, token_b, lab_b.id, order_id, idempotency_key=_key()).status_code == 404
    # Naming the *victim's* organization in the path -- refused on membership, not on the order.
    assert _specimens(client, token_b, lab_a.id, order_id).status_code in (403, 404)
    assert _collect(client, token_b, lab_a.id, order_id, idempotency_key=_key()).status_code in (
        403,
        404,
    )

    # Indistinguishable from an order that does not exist at all.
    unknown = _specimens(client, token_b, lab_b.id, str(uuid.uuid4()))
    assert unknown.status_code == 404
    assert unknown.json() == rival_read.json()

    # The guessed specimen UUID has no route of its own to fetch it by, which is the strongest
    # available answer: there is nothing to enumerate.
    # (The absence of any fetch-specimen-by-id route is asserted structurally in
    # `test_there_is_no_route_that_can_rewrite_collection_provenance`; there is therefore no
    # URL for a guessed specimen UUID to be aimed at in the first place.)
    assert specimen_id  # the victim's specimen still exists, untouched by any of the above
    assert len(_specimens(client, token_a, lab_a.id, order_id).json()) == 1


def test_a_client_cannot_choose_the_owning_organization(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """``lab_organization_id`` is derived from the URL *after* ``LabSelection`` proves that
    laboratory owns the order -- never supplied, and not overridable through the body."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="ownership"
    )
    other = _priced_lab(catalog, directory, organizations, name="OtherOwner", entries=[])

    attempt = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(token),
        json={"idempotency_key": _key(), "lab_organization_id": str(other.id)},
    )
    assert attempt.status_code == 422, attempt.text

    ok = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert ok.status_code == 201
    with sessions() as session:
        specimen = session.scalar(
            select(LabSpecimen).where(LabSpecimen.lab_order_id == uuid.UUID(order_id))
        )
    assert specimen.lab_organization_id == lab.id


def test_a_client_cannot_choose_the_collector(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """``recorded_by_identity_id`` comes from the authenticated principal. A body that could name a
    collector would let one worker attribute a sample to a colleague who never touched it."""
    private_key, _ = keypair
    lab, order_id, token, identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="collector"
    )
    someone_else = _bootstrap(client, private_key, "innocent-bystander")

    attempt = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(token),
        json={"idempotency_key": _key(), "recorded_by_identity_id": someone_else},
    )
    assert attempt.status_code == 422, attempt.text

    ok = _collect(client, token, lab.id, order_id, idempotency_key=_key())
    assert ok.status_code == 201
    assert ok.json()["specimen"]["recorded_by_identity_id"] == identity
    with sessions() as session:
        specimen = session.scalar(
            select(LabSpecimen).where(LabSpecimen.lab_order_id == uuid.UUID(order_id))
        )
    assert str(specimen.recorded_by_identity_id) == identity
    assert str(specimen.recorded_by_identity_id) != someone_else


# ── privacy ──────────────────────────────────────────────────────────────────────────────────────


def test_the_specimen_read_is_not_a_side_door_into_the_clinic_record(
    client, keypair, organizations, catalog, directory, patients
):
    """An exact projection, asserted by equality rather than by absence of a list of names.

    A set-equality assertion is the stronger form: a future field added to the response fails this
    test automatically, where a hand-maintained forbidden list only fails for the names somebody
    thought to add. The forbidden names are then checked as well, as documentation of what
    specifically must never appear."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="privacy"
    )
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201

    rows = _specimens(client, token, lab.id, order_id).json()
    assert len(rows) == 1
    assert set(rows[0]) == {
        "id",
        "order_id",
        "recorded_at",
        "recorded_by_identity_id",
        "idempotency_key",
    }

    serialized = _specimens(client, token, lab.id, order_id).text.lower()
    for forbidden in (
        "reason_for_visit",
        "visit",
        "note",
        "diagnosis",
        "doctor_review",
        "review",
        "observation",
        "result",
        "invoice",
        "payment",
        "balance",
        "patient",
        "date_of_birth",
        "display_name",
        "phone",
        "practitioner",
        "catalog",
        "price",
        "amount",
    ):
        assert forbidden not in serialized, f"{forbidden!r} must not appear in a specimen response"


# ── audit ────────────────────────────────────────────────────────────────────────────────────────


def test_audit_distinguishes_a_first_collection_from_an_additional_specimen(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """Three outcomes, three different audit results, and none of them inferred from a timestamp.

    The first collection creates two rows and writes two events -- the milestone was advanced, and a
    specimen was recorded. An additional specimen writes only the specimen event: claiming the
    milestone had been advanced again would be false. A replay writes neither, which is what makes
    "no duplicate audit event" testable rather than aspirational."""
    private_key, _ = keypair
    lab, order_id, token, identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="audit"
    )

    def events(event_type: str):
        with sessions() as session:
            return session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.event_type == event_type,
                    ExchangeSecurityAuditEvent.resource_id == uuid.UUID(order_id),
                )
            ).all()

    key = _key()
    assert _collect(client, token, lab.id, order_id, idempotency_key=key).status_code == 201
    assert len(events("lab_sample_collection_recorded")) == 1
    specimen_events = events("lab_specimen_recorded")
    assert len(specimen_events) == 1
    # Every fact the ticket requires an audit row to establish.
    assert str(specimen_events[0].actor_identity_id) == identity
    assert specimen_events[0].organization_id == lab.id
    assert specimen_events[0].resource_type == "lab_order"
    assert specimen_events[0].resource_id == uuid.UUID(order_id)
    assert specimen_events[0].outcome == "created"
    assert specimen_events[0].created_at is not None

    # A replay: nothing created, nothing audited.
    assert _collect(client, token, lab.id, order_id, idempotency_key=key).status_code == 200
    assert len(events("lab_sample_collection_recorded")) == 1
    assert len(events("lab_specimen_recorded")) == 1

    # An additional specimen: the specimen event only.
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201
    assert len(events("lab_sample_collection_recorded")) == 1, (
        "an additional specimen must not claim the collection milestone was advanced again"
    )
    assert len(events("lab_specimen_recorded")) == 2


def test_a_refused_collection_writes_no_specimen_and_no_audit(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """Transactional: a refusal is not a partial write. The specimen, the milestone and the
    audit row commit together or not at all."""
    private_key, _ = keypair
    _provider, lab, order_id, _clinician = _route_order(
        client, private_key, organizations, catalog, directory, patients, name="atomic"
    )
    token, _identity, _membership = _lab_worker(client, private_key, lab, "atomic")

    # SELECTED: refused.
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 409

    with sessions() as session:
        assert (
            session.scalars(
                select(LabSpecimen).where(LabSpecimen.lab_order_id == uuid.UUID(order_id))
            ).all()
            == []
        )
        assert (
            session.scalars(
                select(LabSampleCollection).where(
                    LabSampleCollection.lab_order_id == uuid.UUID(order_id)
                )
            ).all()
            == []
        )
        assert (
            session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.event_type.in_(
                        ("lab_specimen_recorded", "lab_sample_collection_recorded")
                    ),
                    ExchangeSecurityAuditEvent.resource_id == uuid.UUID(order_id),
                )
            ).all()
            == []
        )


# ── idempotency-key handling ─────────────────────────────────────────────────────────────────────


def test_a_whitespace_only_key_is_treated_as_no_key(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """`""` and `"   "` mean "no key supplied", so a key that round-tripped through a form field
    cannot become a second distinct key for the same logical request -- which would have created a
    duplicate specimen, the exact failure idempotency exists to prevent."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="blankkey"
    )

    first = _collect(client, token, lab.id, order_id, idempotency_key="   ")
    assert first.status_code == 201, first.text
    second = _collect(client, token, lab.id, order_id, idempotency_key="")
    assert second.status_code == 200, second.text
    assert second.json()["specimen"]["id"] == first.json()["specimen"]["id"]
    assert len(_specimens(client, token, lab.id, order_id).json()) == 1

    with sessions() as session:
        specimen = session.scalar(
            select(LabSpecimen).where(LabSpecimen.lab_order_id == uuid.UUID(order_id))
        )
    assert specimen.idempotency_key is None


def test_an_oversized_key_is_refused_at_the_boundary(
    client, keypair, organizations, catalog, directory, patients
):
    """``max_length`` matches the real ``VARCHAR(128)``, so an oversized key is a 422 here rather
    than a ``DataError`` surfacing as an uncontrolled 500 from PostgreSQL. The lesson
    ``NativeObservationRequest`` already records, applied at the same boundary."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="longkey"
    )

    refused = _collect(client, token, lab.id, order_id, idempotency_key="k" * 129)
    assert refused.status_code == 422, refused.text
    assert _specimens(client, token, lab.id, order_id).json() == []

    accepted = _collect(client, token, lab.id, order_id, idempotency_key="k" * 128)
    assert accepted.status_code == 201, accepted.text


def test_two_orders_may_reuse_the_same_key(
    client, keypair, organizations, catalog, directory, patients
):
    """Uniqueness is scoped ``(lab_order_id, idempotency_key)``, not to the key alone.

    Two orders whose clients independently chose the same key value -- a counter starting at "1",
    say -- must each get their own specimen. A globally unique key would have made one laboratory's
    caller silently receive the other order's specimen as if it were theirs."""
    private_key, _ = keypair
    lab_x, order_x, token_x, _ix, _mx = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="keyreuse1"
    )
    lab_y, order_y, token_y, _iy, _my = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="keyreuse2"
    )
    shared = "1"

    a = _collect(client, token_x, lab_x.id, order_x, idempotency_key=shared)
    b = _collect(client, token_y, lab_y.id, order_y, idempotency_key=shared)
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text
    assert a.json()["specimen"]["id"] != b.json()["specimen"]["id"]
    assert a.json()["specimen"]["order_id"] == order_x
    assert b.json()["specimen"]["order_id"] == order_y


# ── neighbouring workflow, unchanged ────────────────────────────────────────────────────────────


def test_the_queue_projection_gains_no_specimen_field(
    client, keypair, organizations, catalog, directory, patients
):
    """Specimen history is its own bounded read, not a field on the work queue.

    The queue is a fixed-width view and specimen count per order is unbounded in principle, so
    folding the history in would have made one listing's cost depend on how many samples its orders
    happened to have -- the N+1 shape DH-EX-011 and DH-LAB-002 both went out of their way to avoid.
    ``collection_recorded_at`` stays exactly what it was."""
    private_key, _ = keypair
    lab, order_id, token, _identity, _membership = _accepted_order(
        client, private_key, organizations, catalog, directory, patients, name="queueshape"
    )
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201
    assert _collect(client, token, lab.id, order_id, idempotency_key=_key()).status_code == 201

    queue = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders?status=ACCEPTED", headers=_auth(token)
    )
    assert queue.status_code == 200, queue.text
    entry = next(row for row in queue.json() if row["order_id"] == order_id)
    assert "specimens" not in entry
    assert "specimen_count" not in entry
    # The milestone field is unchanged and still carries the milestone's own instant.
    assert entry["collection_recorded_at"] is not None
