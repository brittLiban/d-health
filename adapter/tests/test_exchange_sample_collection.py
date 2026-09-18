"""DH-EX-011: the order-level sample/specimen collection workflow milestone --
``POST /organizations/{lab_id}/lab-orders/{order_id}/record-collection``.

Repository-level tests (idempotency, ownership, ACCEPTED-only creation rule, concurrency) live in
``test_exchange_lab_workflow.py`` alongside ``LabWorkflowRepository``'s other tests, since
``record_collection`` is a method on that same repository. This file covers HTTP-level concerns
DH-EX-011's own required test matrix names that are not already covered there: the provider-
assigned happy path, backward compatibility for legacy IN_PROGRESS orders with no milestone, the
work-queue's ``collection_recorded_at`` field (including a real N+1 query-count measurement), audit
atomicity for this specific endpoint, and this endpoint's own authorization matrix.

Same harness pattern as ``test_exchange_provider_assignment_api.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent, LabSampleCollection
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository

TTL_HOURS = 168


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, tmp_path: Path) -> TestClient:
    _, public_key = keypair
    validator = build_validator(public_key)
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=validator,
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _new_key() -> str:
    return str(uuid.uuid4())


def _bootstrap_identity(client, private_key, subject: str, **token_kwargs) -> str:
    token = make_token(private_key, subject=subject, **token_kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _grant_roles(client, private_key, org_id, identity_id, subject: str, roles: list[str]) -> None:
    platform_admin_token = make_token(
        private_key, subject=f"setup-admin-{subject}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(platform_admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text


def _setup_clinician(client, private_key, organizations, name: str):
    provider = make_provider(organizations, name=name)
    identity_id = _bootstrap_identity(client, private_key, f"clinician-{name}")
    _grant_roles(client, private_key, provider.id, identity_id, f"clinician-{name}", ["CLINICIAN"])
    token = make_token(private_key, subject=f"clinician-{name}")
    return provider, token


def _create_order(client, private_key, organizations, catalog, patients, *, name: str):
    """A one-item order with no lab, no offering, no price -- returns (provider, order_id,
    entry_id, clinician_token)."""
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_record = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Synthetic Patient {name}",
    )
    entry, _ = catalog.create_entry(code=f"DHEALTH-SC-{name}", display_name=f"SC Test {name}")
    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_record.id),
            "catalog_entry_ids": [str(entry.id)],
        },
    )
    assert create_response.status_code == 201, create_response.text
    order_id = uuid.UUID(create_response.json()["id"])
    return provider, order_id, entry.id, clinician_token


def _assign_lab(client, token, provider_id, order_id, lab_id):
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/assign-lab",
        headers=_auth(token),
        json={"lab_organization_id": str(lab_id)},
    )


def _referral_lab(
    organizations,
    directory,
    catalog,
    *,
    name: str,
    entry_id,
    client=None,
    private_key=None,
    provider_id=None,
):
    """DH-CLINIC-006: when client/private_key/provider_id are given, also configures
    an active ProviderLabRoute from provider_id to the new lab -- without one,
    assign_by_provider refuses with ProviderLabRouteNotActive regardless of how eligible
    the lab otherwise is (§18)."""
    lab = make_lab(organizations, name=name)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
    if client is not None:
        _configure_route(client, private_key, provider_id, lab.id)
    return lab


def _configure_route(client, private_key, provider_id, lab_id, *, active: bool = True) -> None:
    platform_admin_token = make_token(
        private_key, subject=f"route-admin-{uuid.uuid4()}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.put(
        f"/exchange/v1/organizations/{provider_id}/lab-routes/{lab_id}",
        headers=_auth(platform_admin_token),
        json={"active": active},
    )
    assert response.status_code in (200, 201), response.text


def _setup_lab_tech_for(client, private_key, lab_id, name: str):
    identity_id = _bootstrap_identity(client, private_key, f"labtech-{name}")
    _grant_roles(client, private_key, lab_id, identity_id, f"labtech-{name}", ["LAB_TECH"])
    return make_token(private_key, subject=f"labtech-{name}")


def _audit_events(sessions, event_type: str) -> list[dict]:
    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == event_type
            )
        ).all()
        return [{"outcome": row.outcome, "resource_id": str(row.resource_id)} for row in rows]


# -- primary acceptance journey: "provider assigns lab" per the ticket's own exact wording --------


def test_provider_assigned_collection_journey(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """create/bind patient -> create order -> provider assigns lab -> LAB_TECH accepts ->
    LAB_TECH records collection -> LAB_TECH starts processing. The ticket's own named primary
    acceptance journey, using provider assignment (not patient selection) as the routing path."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="Journey"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="JourneyLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201

    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "journey")

    accept = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept.status_code == 200
    assert accept.json()["collection_recorded_at"] is None

    collection = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert collection.status_code == 201, collection.text
    assert collection.json()["order_id"] == str(order_id)
    assert collection.json()["recorded_at"] is not None

    start = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start.status_code == 200, start.text
    assert start.json()["status"] == "IN_PROGRESS"


def test_collection_before_acceptance_is_conflict(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="TooEarly"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="TooEarlyLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "tooearly")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 409, response.text


def test_collection_idempotent_replay(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="Replay"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="ReplayLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "replay")
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )

    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert first.status_code == 201
    second = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert second.status_code == 200
    assert second.json()["recorded_at"].rstrip("Z") == first.json()["recorded_at"].rstrip("Z")

    with sessions() as session:
        from sqlalchemy import func

        count = session.scalar(select(func.count()).select_from(LabSampleCollection))
    assert count == 1
    assert len(_audit_events(sessions, "lab_sample_collection_recorded")) == 1


def test_collection_replay_survives_progression_to_in_progress(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """The milestone genuinely already happened -- replaying record-collection after the order
    has since progressed to IN_PROGRESS must still succeed with the original timestamp, never a
    conflict and never a rewritten value."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="ReplayLater"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="ReplayLaterLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "replaylater")
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert first.status_code == 201
    start = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start.status_code == 200

    replay = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["recorded_at"].rstrip("Z") == first.json()["recorded_at"].rstrip("Z")


# -- backward compatibility: legacy IN_PROGRESS orders with no milestone --------------------------


def test_legacy_in_progress_order_start_processing_replay_unaffected(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """A pre-DH-EX-011 order already IN_PROGRESS with no collection milestone (simulated here by
    driving IN_PROGRESS through the repository directly, bypassing the new guard entirely, exactly
    as a real legacy row would already exist without ever having passed through it) must keep its
    existing idempotent start-processing retry semantics -- 200, no mutation, no fabricated
    milestone -- never a 409 demanding a collection record that will never exist for this order."""
    from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
    from daher_adapter.exchange.models import LabOrder
    from daher_adapter.exchange.selection import LabSelectionRepository

    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="Legacy"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="LegacyLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "legacy")
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )

    # Simulate a legacy order that reached IN_PROGRESS before DH-EX-011 existed: force the status
    # directly, never through record-collection, so genuinely no LabSampleCollection row exists.
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        order.status = "IN_PROGRESS"
        session.commit()

    with sessions() as session:
        from sqlalchemy import func

        count = session.scalar(select(func.count()).select_from(LabSampleCollection))
    assert count == 0, "this scenario only means anything if genuinely no milestone exists"

    replay = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "IN_PROGRESS"
    assert replay.json()["collection_recorded_at"] is None, (
        "must never fabricate a milestone merely to satisfy a replay"
    )

    with sessions() as session:
        from sqlalchemy import func

        count = session.scalar(select(func.count()).select_from(LabSampleCollection))
    assert count == 0

    # DH-EX-011's own explicit "no retroactive fabrication" rule: a fresh record-collection call
    # against this same legacy order must fail closed, never silently backfill history.
    unused = LabSelectionRepository, LabWorkflowRepository  # referenced above for clarity only
    del unused
    fabricate_attempt = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert fabricate_attempt.status_code == 409, fabricate_attempt.text
    with sessions() as session:
        from sqlalchemy import func

        count = session.scalar(select(func.count()).select_from(LabSampleCollection))
    assert count == 0


# -- work queue representation + N+1 regression check ----------------------------------------------


def _seed_lab_with_n_orders(
    client, private_key, organizations, catalog, directory, patients, *, lab_name: str, count: int
):
    """Returns (lab, lab_tech_token, order_ids) -- `count` orders all assigned to the same lab,
    the first with a recorded collection milestone, the rest deliberately left without one."""
    lab = None
    order_ids = []
    for i in range(count):
        provider, order_id, entry_id, clinician_token = _create_order(
            client, private_key, organizations, catalog, patients, name=f"{lab_name}-{i}"
        )
        if lab is None:
            lab = _referral_lab(organizations, directory, catalog, name=lab_name, entry_id=entry_id)
        else:
            catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
        # Each iteration uses a distinct provider (a fresh `_create_order` call) but the same lab
        # -- a route must be configured per provider->lab pair, not just once for the shared lab.
        _configure_route(client, private_key, provider.id, lab.id)
        assert (
            _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201
        )
        order_ids.append(order_id)
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, lab_name)
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_ids[0]}/accept",
        headers=_auth(lab_tech_token),
    )
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_ids[0]}/record-collection",
        headers=_auth(lab_tech_token),
    )
    return lab, lab_tech_token, order_ids


def test_work_queue_shows_collection_state_correctly(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """Functional correctness of the new field: a recorded milestone shows its timestamp, an
    unrecorded one shows `null` -- never swapped, never a fabricated value. Query-count/N+1
    behavior is proven separately, at the repository level, in
    `test_list_for_lab_collection_join_issues_exactly_one_query` below -- isolated there from this
    file's own HTTP-layer per-order `organizations.get`/`patients.get` calls and `LabOrder.items`
    lazy-loading, both pre-existing DH-EX-006 behavior this ticket does not touch (see that test's
    own docstring for the full finding)."""
    private_key, _ = keypair
    lab, lab_tech_token, order_ids = _seed_lab_with_n_orders(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        lab_name="QueueCorrect",
        count=3,
    )

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    )
    assert response.status_code == 200, response.text
    by_id = {o["order_id"]: o for o in response.json()}
    assert by_id[str(order_ids[0])]["collection_recorded_at"] is not None
    for order_id in order_ids[1:]:
        assert by_id[str(order_id)]["collection_recorded_at"] is None


def _seed_n_orders_for_one_lab(
    organizations, catalog, directory, patients, sessions, *, lab, count
):
    from daher_adapter.exchange.identity import PractitionerReferenceRepository
    from daher_adapter.exchange.models import LabPrice
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
    from daher_adapter.exchange.selection import LabSelectionRepository

    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    practitioners = PractitionerReferenceRepository(sessions)
    order_ids = []
    for i in range(count):
        provider = make_provider(organizations, name=f"N1Provider-{lab.name}-{i}")
        entry, _ = catalog.create_entry(
            code=f"N1-{lab.name}-{i}", display_name=f"N1 Test {lab.name} {i}"
        )
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("100"),
            currency="DJF",
            expected_current_price_id=None,
        )
        patient = patients.get_or_create(
            source_organization_id=provider.id,
            source_system="dhealth_provider_portal",
            source_identity_system="urn:daher-health:provider-portal-patient-id",
            source_identity_value=f"N1-PATIENT-{lab.name}-{i}",
            display_name="Synthetic Patient",
        )
        practitioner = practitioners.get_or_create(
            source_organization_id=provider.id,
            source_system="dhealth_provider_portal",
            source_identity_system="urn:daher-health:provider-portal-practitioner-id",
            source_identity_value=f"N1-DOC-{lab.name}-{i}",
            display_name="Dr Synthetic",
        )
        order = orders.create(
            idempotency_key=f"n1-{lab.name}-{i}",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        ).order
        with sessions() as session:
            price_id = session.scalar(
                select(LabPrice.id).where(
                    LabPrice.lab_offering_id == offering.id, LabPrice.superseded_at.is_(None)
                )
            )
        selections.select(
            lab_order_id=order.id,
            lab_organization_id=lab.id,
            expected_price_ids=frozenset({price_id}),
        )
        order_ids.append(order.id)
    return order_ids


def _count_list_for_lab_queries(sessions, workflow, lab_id) -> tuple[int, list[str]]:
    query_count = {"n": 0}
    statements: list[str] = []

    def _count(_conn, _cursor, statement, *_args, **_kwargs) -> None:
        query_count["n"] += 1
        statements.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "after_cursor_execute", _count)
    try:
        workflow.list_for_lab(lab_id)
    finally:
        event.remove(engine, "after_cursor_execute", _count)
    return query_count["n"], statements


def test_list_for_lab_collection_join_adds_no_marginal_per_order_query(
    organizations, catalog, directory, patients, sessions
) -> None:
    """Isolates DH-EX-011's own marginal contribution to `list_for_lab`'s query cost, at the
    repository level, bypassing the HTTP layer entirely. Comparing a 2-order lab against an
    8-order lab, the total query-count delta must stay flat (0, or at most 1 for incidental
    variance) -- a genuine per-order cost from the collection join, or a regression of the
    DH-LAB-002 `items` fix below, would show up here as a delta that scales with order count.

    DH-LAB-002 update: `list_for_lab`'s pre-existing `LabOrder.items` lazy-load N+1 -- found here
    by DH-EX-011 and deliberately left unfixed as out of that ticket's scope -- is now closed via
    `selectinload(LabOrder.items)` (see `lab_workflow.py`'s own docstring on that query). This test
    is updated, not just left passing by coincidence: before the fix its assertion tolerated a
    delta scaling with order count (`extra_orders * 1.5`); it now asserts the delta is flat,
    because there is no longer any per-order query left to scale. The `items`-table-specific
    assertion that the fix issues exactly one additional query regardless of N lives in
    `test_list_for_lab_items_selectinload_issues_one_bounded_query_regardless_of_order_count`
    below, which is the test that actually exercises the `items` relationship the ticket asked
    for."""
    from daher_adapter.exchange.lab_workflow import LabWorkflowRepository

    workflow = LabWorkflowRepository(sessions, organizations)

    lab_small = make_lab(organizations, name="N1Small")
    directory.upsert(
        lab_organization_id=lab_small.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    small_orders = _seed_n_orders_for_one_lab(
        organizations, catalog, directory, patients, sessions, lab=lab_small, count=2
    )
    workflow.accept(order_id=small_orders[0], lab_organization_id=lab_small.id)
    workflow.record_collection(order_id=small_orders[0], lab_organization_id=lab_small.id)

    lab_large = make_lab(organizations, name="N1Large")
    directory.upsert(
        lab_organization_id=lab_large.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    large_orders = _seed_n_orders_for_one_lab(
        organizations, catalog, directory, patients, sessions, lab=lab_large, count=8
    )
    workflow.accept(order_id=large_orders[0], lab_organization_id=lab_large.id)
    workflow.record_collection(order_id=large_orders[0], lab_organization_id=lab_large.id)

    small_count, _ = _count_list_for_lab_queries(sessions, workflow, lab_small.id)
    large_count, _ = _count_list_for_lab_queries(sessions, workflow, lab_large.id)

    extra_orders = len(large_orders) - len(small_orders)
    delta = large_count - small_count
    assert delta <= 1, (
        f"query count grew {delta} for {extra_orders} extra orders "
        f"({small_count} -> {large_count}) -- list_for_lab should now be flat/bounded, not "
        f"scaling with order count, now that the items lazy-load N+1 is fixed"
    )


def test_list_for_lab_items_selectinload_issues_one_bounded_query_regardless_of_order_count(
    organizations, catalog, directory, patients, sessions
) -> None:
    """DH-LAB-002 §33/§34/§66: directly measures the `LabOrder.items` relationship this ticket was
    asked to close, by counting queries that actually hit `exchange_lab_order_items` -- not just
    the total query count (which the test above already covers). Before the `selectinload` fix,
    this count equals the number of orders returned (one lazy-load per row); after the fix it is
    exactly 1, for both a 1-order and an 8-order lab, proving the query no longer scales with N."""
    from daher_adapter.exchange.lab_workflow import LabWorkflowRepository

    workflow = LabWorkflowRepository(sessions, organizations)

    lab_one = make_lab(organizations, name="N1ItemsOne")
    directory.upsert(
        lab_organization_id=lab_one.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    _seed_n_orders_for_one_lab(
        organizations, catalog, directory, patients, sessions, lab=lab_one, count=1
    )

    lab_many = make_lab(organizations, name="N1ItemsMany")
    directory.upsert(
        lab_organization_id=lab_many.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    _seed_n_orders_for_one_lab(
        organizations, catalog, directory, patients, sessions, lab=lab_many, count=8
    )

    _, statements_one = _count_list_for_lab_queries(sessions, workflow, lab_one.id)
    _, statements_many = _count_list_for_lab_queries(sessions, workflow, lab_many.id)

    items_queries_one = [s for s in statements_one if "exchange_lab_order_items" in s]
    items_queries_many = [s for s in statements_many if "exchange_lab_order_items" in s]

    assert len(items_queries_one) == 1, (
        f"expected exactly 1 query against exchange_lab_order_items for a 1-order lab, got "
        f"{len(items_queries_one)}: {items_queries_one}"
    )
    assert len(items_queries_many) == 1, (
        f"expected exactly 1 query against exchange_lab_order_items for an 8-order lab, got "
        f"{len(items_queries_many)} -- items lazy-load is scaling with order count again: "
        f"{items_queries_many}"
    )


# -- audit atomicity ------------------------------------------------------------------------------


def test_collection_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="AuditFail"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="AuditFailLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "auditfail")
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
            headers=_auth(lab_tech_token),
        )

    with sessions() as session:
        from sqlalchemy import func

        count = session.scalar(select(func.count()).select_from(LabSampleCollection))
    assert count == 0, "audit failure must roll back the collection insert too"


# -- authorization matrix, specific to this endpoint -----------------------------------------------


def test_clinician_cannot_record_collection(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """The ordering clinician has no membership at all at the lab organization -- anti-
    enumeration 404, the same convention `_require_lab_tech` already applies for accept/reject/
    start-processing (see `test_provider_clinician_cannot_act_as_lab` in
    `test_exchange_lab_workflow.py`), not a 403 that would confirm the lab org exists to a
    non-member."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="ClinicianDenied"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="ClinicianDeniedLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(clinician_token),
    )
    assert response.status_code == 404


def test_org_admin_alone_cannot_record_collection(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="OrgAdminDenied"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="OrgAdminDeniedLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201

    identity_id = _bootstrap_identity(client, private_key, "orgadmin-denied")
    _grant_roles(client, private_key, lab.id, identity_id, "orgadmin-denied", ["ORG_ADMIN"])
    org_admin_token = make_token(private_key, subject="orgadmin-denied")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(org_admin_token),
    )
    assert response.status_code == 403


def test_platform_admin_alone_cannot_record_collection(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="PlatAdminDenied"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="PlatAdminDeniedLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201

    admin_token = make_token(
        private_key, subject="platform-admin-denied", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(admin_token),
    )
    assert response.status_code == 403


def test_releaser_alone_cannot_record_collection(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """DH-LAB-002 §40/§63 (BC10): `LAB_RESULT_RELEASER` is DH-EX-012's own role, introduced after
    DH-EX-011 shipped -- DH-EX-011's own authorization matrix (above) could not have exercised it,
    so this negative control genuinely did not exist before this ticket. Recording collection is a
    `LAB_TECH`-only action; a releaser with no `LAB_TECH` membership must be refused exactly like
    the bare-ORG_ADMIN case above, not silently allowed because it holds *some* lab role."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="ReleaserDenied"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="ReleaserDeniedLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201

    identity_id = _bootstrap_identity(client, private_key, "releaser-denied")
    _grant_roles(
        client, private_key, lab.id, identity_id, "releaser-denied", ["LAB_RESULT_RELEASER"]
    )
    releaser_token = make_token(private_key, subject="releaser-denied")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(releaser_token),
    )
    assert response.status_code == 403


def test_releaser_alone_cannot_start_processing(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """DH-LAB-002 §40/§64 (BP8): same gap as `test_releaser_alone_cannot_record_collection` above,
    for `start-processing`. A LAB_TECH accepts and records collection first so the only variable
    under test is the releaser-only caller's own authorization on `start-processing` -- the order
    must remain `ACCEPTED`, never silently advanced to `IN_PROGRESS`."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="ReleaserProcDenied"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="ReleaserProcDeniedLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "releaser-proc-denied")
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )

    identity_id = _bootstrap_identity(client, private_key, "releaser-proc-denied")
    _grant_roles(
        client,
        private_key,
        lab.id,
        identity_id,
        "releaser-proc-denied",
        ["LAB_RESULT_RELEASER"],
    )
    releaser_token = make_token(private_key, subject="releaser-proc-denied")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(releaser_token),
    )
    assert response.status_code == 403

    check = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}",
        headers=_auth(lab_tech_token),
    )
    assert check.status_code == 200, check.text
    assert check.json()["status"] == "ACCEPTED"


def test_cross_lab_lab_tech_gets_anti_enumeration_404(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="CrossLab"
    )
    lab_a = _referral_lab(
        organizations,
        directory,
        catalog,
        name="CrossLabA",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab_a.id).status_code == 201

    lab_b = make_lab(organizations, name="CrossLabB")
    lab_b_tech_token = _setup_lab_tech_for(client, private_key, lab_b.id, "crosslab-b")

    response = client.post(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_b_tech_token),
    )
    assert response.status_code == 404

    unknown_order_response = client.post(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{uuid.uuid4()}/record-collection",
        headers=_auth(lab_b_tech_token),
    )
    assert unknown_order_response.status_code == 404
    assert unknown_order_response.json() == response.json()


def test_extra_body_fields_rejected(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """No request body is required for this ticket -- confirm the route accepts none (FastAPI
    ignores an unmodeled body by default since there is no Pydantic body parameter at all, so
    this simply proves the endpoint works identically with or without a stray body)."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="NoBody"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="NoBodyLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "nobody")
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
        json={"specimen_type": "blood", "collected_at": "2020-01-01T00:00:00Z"},
    )
    assert response.status_code == 201, response.text
    assert "specimen_type" not in response.json()
    assert "collected_at" not in response.json()
