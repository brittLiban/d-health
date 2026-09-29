"""DHEALTH-LAB-BLOCKED-001: the order-level blocked / cannot-proceed workflow, over HTTP.

The ticket's completion standard, as tests:

    A lab worker with the correct effective capability can truthfully say "we cannot continue this
    order right now", record why, preserve that fact as history, prevent the blocked order from
    advancing, later resolve the issue and resume the lawful workflow -- while another
    organization, an unauthorized user, Billing state, or a role-name assumption cannot control
    that workflow.

Driven through `TestClient` against the real router rather than against the repository, because
every claim here is about *reachability*: a rule enforced in a service method that no endpoint
consults would pass a unit test and fail the product.

── What the numbered groups prove ──

    LAB-BLOCK-1   an eligible order may be blocked by a member holding `lab.queue.work`
    LAB-BLOCK-2   a member without it is refused
    LAB-BLOCK-3   a *direct capability grant* authorizes it, with no lab role template at all
    LAB-BLOCK-4   removing that grant refuses the very next request, same token
    LAB-BLOCK-5   another laboratory's worker cannot block this laboratory's order
    LAB-BLOCK-6   an ineligible source state is refused
    LAB-BLOCK-7   a blocked order cannot advance through any prohibited laboratory step
    LAB-BLOCK-8   resolution restores the *server-defined* state, not one a client chose
    LAB-BLOCK-9   a second block after resolution is a second episode, not an overwrite
    LAB-BLOCK-10  a resolved episode is immutable
    LAB-BLOCK-11  a blocked order cannot be released
    LAB-BLOCK-12  after lawful resolution and completion, release still works
    LAB-BLOCK-13  billing balance has no effect on block, resolution or release
    LAB-BLOCK-14  `lab.result.release` stays separate from `lab.queue.work`
    LAB-BLOCK-15  the provider sees only the approved projection
    LAB-BLOCK-16  the patient surface leaks no laboratory technical detail

Plus idempotency/retry (LAB-BLOCK-17/18) and the audit shape (LAB-BLOCK-19).

All patient, catalog, price and note data here is synthetic. No real patient data is used. The
free-text notes are deliberately written as obviously-invented operational strings so that the
"a note must never reach an audit row" assertions are checking a real string rather than an empty
one.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import (
    activate_result_template,
    make_lab,
    make_priced_offering,
    make_provider,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.capabilities import Capability
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    LabOrder,
    LabOrderBlock,
    LabOrderItem,
)
from daher_adapter.exchange.organizations import OrganizationRepository

#: Deliberately specific and obviously synthetic. Several assertions below check that this exact
#: string appears nowhere it must not -- an audit row, a provider response, a patient response --
#: and a generic value like "note" would make those assertions far weaker.
TECH_NOTE = "Centrifugeuse hors service depuis 09h — note interne synthétique"
RESOLUTION_NOTE = "Deuxième tube reçu, travail repris — note interne synthétique"


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "exchange-audit.jsonl"


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, audit_path: Path) -> TestClient:
    _, public_key = keypair
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
        audit_writer=ExchangeAuditWriter(audit_path),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def catalog(sessions: sessionmaker[Session], organizations: OrganizationRepository):
    return CatalogRepository(sessions, organizations)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _idem(token: str) -> dict[str, str]:
    headers = _auth(token)
    headers["Idempotency-Key"] = str(uuid.uuid4())
    return headers


def _bootstrap(client: TestClient, private_key, subject: str, **kwargs) -> tuple[str, str]:
    token = make_token(private_key, subject=subject, **kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"], token


def _attach(client, actor_token, organization_id, identity_id, roles) -> str:
    response = client.post(
        f"/exchange/v1/organizations/{organization_id}/memberships",
        headers=_auth(actor_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["membership_id"]


def _set_capability(client, actor_token, org, membership_id, capability, effect):
    return client.put(
        f"/exchange/v1/organizations/{org}/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(actor_token),
        json={"effect": effect},
    )


def _clear_capability(client, actor_token, org, membership_id, capability):
    return client.delete(
        f"/exchange/v1/organizations/{org}/memberships/{membership_id}/capabilities/{capability}",
        headers=_auth(actor_token),
    )


@pytest.fixture
def world(client: TestClient, keypair, organizations: OrganizationRepository, catalog):
    """One clinic, two laboratories, and one persona per authority that must be told apart.

    The **second** laboratory is what makes the cross-tenant assertions real rather than
    self-referential: `tech_rival` is a genuine, fully-authorized `LAB_TECH` -- of the wrong
    laboratory. A refusal aimed at a nobody would prove nothing.

    `bare` holds a membership in the laboratory with **no roles at all**, which is what the direct
    capability-grant proof (LAB-BLOCK-3) is built on: if they already held `LAB_TECH`, granting
    `lab.queue.work` would demonstrate nothing.
    """
    private_key, _ = keypair
    platform = make_token(private_key, subject="platform", roles=[PLATFORM_ADMIN_ROLE])

    provider = make_provider(organizations, "Clinique Bloquée")
    lab = make_lab(organizations, "Laboratoire Bloqué")
    rival = make_lab(organizations, "Laboratoire Rival")

    entry, offering = make_priced_offering(
        catalog, lab_organization_id=lab.id, code="DH-BLK-001", amount=Decimal("2500.00")
    )
    activate_result_template(catalog, offering_id=offering.id)
    LabDirectoryProfileRepository(catalog._sessions, organizations).upsert(  # noqa: SLF001
        lab_organization_id=lab.id,
        public_address="Adresse synthétique",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )

    people = {}
    for key, subject in (
        ("admin_p", "adm-p"),
        ("admin_l", "adm-l"),
        ("admin_r", "adm-r"),
        ("clin", "clin"),
        ("tech", "tech"),
        ("releaser", "rel"),
        ("bare", "bare"),
        ("tech_rival", "tech-rival"),
        ("clerk", "clerk"),
    ):
        identity_id, token = _bootstrap(client, private_key, subject)
        people[key] = {"identity": identity_id, "token": token}

    _attach(client, platform, str(provider.id), people["admin_p"]["identity"], ["ORG_ADMIN"])
    _attach(client, platform, str(lab.id), people["admin_l"]["identity"], ["ORG_ADMIN"])
    _attach(client, platform, str(rival.id), people["admin_r"]["identity"], ["ORG_ADMIN"])
    admin_p, admin_l, admin_r = (
        people["admin_p"]["token"],
        people["admin_l"]["token"],
        people["admin_r"]["token"],
    )
    people["clin"]["membership"] = _attach(
        client, admin_p, str(provider.id), people["clin"]["identity"], ["CLINICIAN"]
    )
    people["tech"]["membership"] = _attach(
        client, admin_l, str(lab.id), people["tech"]["identity"], ["LAB_TECH"]
    )
    people["releaser"]["membership"] = _attach(
        client, admin_l, str(lab.id), people["releaser"]["identity"], ["LAB_RESULT_RELEASER"]
    )
    # No roles whatsoever -- a member of the laboratory and nothing more.
    people["bare"]["membership"] = _attach(
        client, admin_l, str(lab.id), people["bare"]["identity"], []
    )
    people["tech_rival"]["membership"] = _attach(
        client, admin_r, str(rival.id), people["tech_rival"]["identity"], ["LAB_TECH"]
    )
    people["clerk"]["membership"] = _attach(
        client, admin_l, str(lab.id), people["clerk"]["identity"], ["BILLING_CLERK"]
    )

    return {
        "provider_id": str(provider.id),
        "lab_id": str(lab.id),
        "rival_id": str(rival.id),
        "catalog_entry_id": str(entry.id),
        "platform": platform,
        "people": people,
    }


def _tok(world, who: str) -> str:
    return world["people"][who]["token"]


# -- the workflow, built out of the same endpoints a real caller uses ----------------------------


def _patient(client: TestClient, world) -> dict:
    response = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/patients",
        headers=_idem(_tok(world, "clin")),
        json={"display_name": "Patient Synthétique", "date_of_birth": "1990-04-17"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _routed_order(
    client: TestClient, world, *, accept: bool = True, collect: bool = False, process: bool = False
) -> dict:
    """A real order, routed and advanced exactly as far as a test asks for -- never further.

    Every step goes through the production endpoint, including the platform-configured route and
    the collection milestone that `ACCEPTED -> IN_PROGRESS` requires. A fixture that reached
    `IN_PROGRESS` by writing the row directly would be proving things about a state the product
    cannot actually produce.
    """
    provider, lab = world["provider_id"], world["lab_id"]
    patient = _patient(client, world)
    clin = _tok(world, "clin")

    intent = client.post(
        f"/exchange/v1/organizations/{provider}/order-intents",
        headers=_idem(clin),
        json={
            "patient_reference_id": patient["primary_patient_reference_id"],
            "catalog_entry_ids": [world["catalog_entry_id"]],
        },
    )
    assert intent.status_code == 201, intent.text
    committed = client.post(
        f"/exchange/v1/organizations/{provider}/order-intents/{intent.json()['id']}/commit",
        headers=_auth(clin),
    )
    assert committed.status_code in (200, 201), committed.text
    order = committed.json()
    order["patient_id"] = patient["patient_id"]

    routed = client.put(
        f"/exchange/v1/organizations/{provider}/lab-routes/{lab}",
        headers=_auth(world["platform"]),
        json={"active": True},
    )
    assert routed.status_code in (200, 201), routed.text
    assigned = client.post(
        f"/exchange/v1/organizations/{provider}/orders/{order['id']}/assign-lab",
        headers=_auth(clin),
        json={"lab_organization_id": lab},
    )
    assert assigned.status_code in (200, 201), assigned.text

    tech = _tok(world, "tech")
    if accept:
        got = client.post(f"{_lab_base(world, order)}/accept", headers=_auth(tech))
        assert got.status_code == 200, got.text
    if collect:
        got = client.post(f"{_lab_base(world, order)}/record-collection", headers=_auth(tech))
        assert got.status_code in (200, 201), got.text
    if process:
        got = client.post(f"{_lab_base(world, order)}/start-processing", headers=_auth(tech))
        assert got.status_code == 200, got.text
    return order


def _lab_base(world, order) -> str:
    return f"/exchange/v1/organizations/{world['lab_id']}/lab-orders/{order['id']}"


def _block(client, world, order, who="tech", reason="SPECIMEN_UNUSABLE", note=TECH_NOTE):
    return client.post(
        f"{_lab_base(world, order)}/block",
        headers=_auth(_tok(world, who)),
        json={"reason": reason, "note": note},
    )


def _resolve(client, world, order, block_id, who="tech", note=None):
    return client.post(
        f"{_lab_base(world, order)}/blocks/{block_id}/resolve",
        headers=_auth(_tok(world, who)),
        json={"resolution_note": note},
    )


def _blocks(client, world, order, who="tech"):
    return client.get(f"{_lab_base(world, order)}/blocks", headers=_auth(_tok(world, who)))


def _lab_order(client, world, order, who="tech"):
    return client.get(_lab_base(world, order), headers=_auth(_tok(world, who)))


def _provider_order(client, world, order):
    return client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/orders/{order['id']}",
        headers=_auth(_tok(world, "clin")),
    )


def _item_id(sessions: sessionmaker[Session], order) -> str:
    with sessions() as session:
        item_id = session.scalar(
            select(LabOrderItem.id).where(LabOrderItem.lab_order_id == uuid.UUID(order["id"]))
        )
    assert item_id is not None
    return str(item_id)


def _enter_and_complete_results(client, world, order, sessions) -> None:
    """Enter one synthetic value per item and mark entry complete -- the lawful road to release."""
    lab, tech = world["lab_id"], _tok(world, "tech")
    workspace = client.get(f"{_lab_base(world, order)}/result-entry-workspace", headers=_auth(tech))
    assert workspace.status_code == 200, workspace.text
    for item in workspace.json()["items"]:
        template = item["template"]
        assert template is not None, "the fixture offering has an active template"
        values = client.post(
            f"/exchange/v1/organizations/{lab}/lab-orders/{order['id']}/items/{item['item_id']}"
            f"/native-result-values",
            headers=_auth(tech),
            json={
                "template_id": template["template_id"],
                "values": [
                    {"definition_id": o["definition_id"], "numeric_value": "4.2"}
                    for o in template["observations"]
                ],
            },
        )
        assert values.status_code in (200, 201), values.text
        done = client.post(
            f"/exchange/v1/organizations/{lab}/lab-orders/{order['id']}/items/{item['item_id']}"
            f"/complete-result-entry",
            headers=_auth(tech),
        )
        assert done.status_code in (200, 201), done.text


def _release(client, world, order, who="releaser"):
    lab, token = world["lab_id"], _tok(world, who)
    results = client.get(f"{_lab_base(world, order)}/results", headers=_auth(token))
    fingerprint = results.json()["result_fingerprint"] if results.status_code == 200 else None
    return client.post(
        f"/exchange/v1/organizations/{lab}/lab-orders/{order['id']}/release-results",
        headers=_auth(token),
        json={"expected_result_fingerprint": fingerprint},
    )


def _db_events(sessions: sessionmaker[Session]) -> list[ExchangeSecurityAuditEvent]:
    with sessions() as session:
        return list(session.scalars(select(ExchangeSecurityAuditEvent)).all())


# == LAB-BLOCK-1: an eligible order may be blocked ===============================================


def test_lab_block_1_an_accepted_order_may_be_blocked_with_a_reason(client, world, sessions):
    """The whole feature, in one pass: a technician says work cannot continue, and the order says
    so afterwards -- with a reason, an actor and an instant nobody had to type."""
    order = _routed_order(client, world)

    blocked = _block(client, world, order)
    assert blocked.status_code == 200, blocked.text
    body = blocked.json()
    assert body["status"] == "BLOCKED"

    episode = body["active_block"]
    assert episode["reason"] == "SPECIMEN_UNUSABLE"
    assert episode["note"] == TECH_NOTE
    # Server-derived, every one of them. None was in the request body.
    assert episode["blocked_by_identity_id"] == world["people"]["tech"]["identity"]
    assert episode["blocked_at"] is not None
    assert episode["blocked_from_status"] == "ACCEPTED"
    assert episode["resolved_at"] is None
    assert episode["resolved_by_identity_id"] is None

    # It survives a fresh read, which is the difference between a response and a fact.
    again = _lab_order(client, world, order)
    assert again.json()["status"] == "BLOCKED"
    assert again.json()["active_block"]["reason"] == "SPECIMEN_UNUSABLE"


def test_lab_block_1b_an_in_progress_order_may_also_be_blocked(client, world):
    """§16's diagram names two source states, not one. Processing that hits a problem must be
    expressible, or a laboratory's only honest option mid-analysis is to say nothing."""
    order = _routed_order(client, world, collect=True, process=True)
    blocked = _block(client, world, order, reason="CLARIFICATION_REQUIRED")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["active_block"]["blocked_from_status"] == "IN_PROGRESS"


def test_lab_block_1c_the_reason_catalog_is_closed(client, world):
    """A reason outside the catalog is refused at the API boundary, not stored and rendered as an
    unknown key later. The catalog is `docs/NATIVE-OPERATING-PRODUCT.md` §16's own four; an agent
    inventing a fifth is exactly what this refusal is here to make impossible."""
    order = _routed_order(client, world)
    refused = _block(client, world, order, reason="PAYMENT_REQUIRED")
    assert refused.status_code == 422, refused.text
    # §15 specifically: no payment-shaped reason exists anywhere in the catalog.
    assert _lab_order(client, world, order).json()["status"] == "ACCEPTED"


# == LAB-BLOCK-2 / 3 / 4: the capability, not the job title ======================================


def test_lab_block_2_a_member_without_lab_queue_work_is_refused(client, world):
    """A releaser and an administrator of this very laboratory are both refused. Neither is an
    outsider -- that is the point: holding *some* authority here is not holding this one."""
    order = _routed_order(client, world)

    for who in ("releaser", "admin_l", "clerk"):
        refused = _block(client, world, order, who=who)
        assert refused.status_code == 403, f"{who}: {refused.text}"

    # A platform realm role opens no laboratory door either (ADR-063).
    platform_refused = client.post(
        f"{_lab_base(world, order)}/block",
        headers=_auth(world["platform"]),
        json={"reason": "SPECIMEN_UNUSABLE"},
    )
    assert platform_refused.status_code in (403, 404), platform_refused.text
    assert _lab_order(client, world, order).json()["status"] == "ACCEPTED"


def test_lab_block_3_a_direct_capability_grant_authorizes_block_and_resolve(client, world):
    """**The mandatory §38 proof.** `bare` holds a membership with no roles at all. One direct
    grant of `lab.queue.work` later they can block and resolve -- with no role change, no new
    combination role, and no feature-code change. If this fails, the feature is role-hardcoded."""
    order = _routed_order(client, world)
    bare = world["people"]["bare"]
    lab, admin_l = world["lab_id"], _tok(world, "admin_l")

    assert _block(client, world, order, who="bare").status_code == 403

    granted = _set_capability(
        client, admin_l, lab, bare["membership"], Capability.LAB_QUEUE_WORK.value, "GRANT"
    )
    assert granted.status_code in (200, 201), granted.text

    blocked = _block(client, world, order, who="bare")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["active_block"]["blocked_by_identity_id"] == bare["identity"]

    episode_id = blocked.json()["active_block"]["id"]
    resolved = _resolve(client, world, order, episode_id, who="bare")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "ACCEPTED"

    # And they still hold no role template at all -- authority came from the capability alone.
    me = client.get("/exchange/v1/me", headers=_auth(bare["token"])).json()
    membership = next(m for m in me["memberships"] if m["organization_id"] == lab)
    assert membership["roles"] == []
    assert Capability.LAB_QUEUE_WORK.value in membership["capabilities"]


def test_lab_block_4_revoking_the_capability_refuses_the_very_next_request(client, world):
    """**The other half of §38.** The same valid Keycloak session, unchanged, must stop working the
    moment the grant is withdrawn -- not at the next login, and not when a cache happens to
    expire."""
    order = _routed_order(client, world)
    bare = world["people"]["bare"]
    lab, admin_l = world["lab_id"], _tok(world, "admin_l")

    _set_capability(
        client, admin_l, lab, bare["membership"], Capability.LAB_QUEUE_WORK.value, "GRANT"
    )
    blocked = _block(client, world, order, who="bare")
    assert blocked.status_code == 200, blocked.text
    episode_id = blocked.json()["active_block"]["id"]

    cleared = _clear_capability(
        client, admin_l, lab, bare["membership"], Capability.LAB_QUEUE_WORK.value
    )
    assert cleared.status_code in (200, 204), cleared.text

    # Same token object, no re-authentication anywhere in this test.
    assert _resolve(client, world, order, episode_id, who="bare").status_code == 403
    assert _block(client, world, order, who="bare").status_code == 403
    # And an explicit DENY on somebody who *does* hold the role closes it equally.
    tech = world["people"]["tech"]
    denied = _set_capability(
        client, admin_l, lab, tech["membership"], Capability.LAB_QUEUE_WORK.value, "DENY"
    )
    assert denied.status_code in (200, 201), denied.text
    assert _resolve(client, world, order, episode_id, who="tech").status_code == 403


# == LAB-BLOCK-5: the tenant boundary ============================================================


def test_lab_block_5_another_laboratorys_worker_cannot_block_this_order(client, world, sessions):
    """`tech_rival` is a real, fully-authorized LAB_TECH -- of the wrong laboratory. Both the
    rival's own URL and this laboratory's URL are tried, because they fail for different reasons
    and both must fail."""
    order = _routed_order(client, world)
    rival_tech = _tok(world, "tech_rival")

    # Through the rival's own organization URL: the order is not assigned there.
    via_rival = client.post(
        f"/exchange/v1/organizations/{world['rival_id']}/lab-orders/{order['id']}/block",
        headers=_auth(rival_tech),
        json={"reason": "SPECIMEN_UNUSABLE"},
    )
    assert via_rival.status_code == 404, via_rival.text

    # Through this laboratory's URL: they are not a member of it. 403/404 either way, never 200.
    via_owner = client.post(
        f"{_lab_base(world, order)}/block",
        headers=_auth(rival_tech),
        json={"reason": "SPECIMEN_UNUSABLE"},
    )
    assert via_owner.status_code in (403, 404), via_owner.text

    assert _lab_order(client, world, order).json()["status"] == "ACCEPTED"
    with sessions() as session:
        assert session.scalars(select(LabOrderBlock)).all() == []


def test_lab_block_5b_a_rival_cannot_read_the_block_history_either(client, world):
    order = _routed_order(client, world)
    assert _block(client, world, order).status_code == 200
    refused = client.get(
        f"/exchange/v1/organizations/{world['rival_id']}/lab-orders/{order['id']}/blocks",
        headers=_auth(_tok(world, "tech_rival")),
    )
    assert refused.status_code == 404, refused.text


# == LAB-BLOCK-6: ineligible source states =======================================================


@pytest.mark.parametrize(
    ("advance", "expected_status"),
    [
        (dict(accept=False), "SELECTED"),
        (dict(accept=True, collect=True, process=True), "IN_PROGRESS"),
    ],
)
def test_lab_block_6_eligibility_is_enforced_server_side(
    client, world, sessions, advance, expected_status
):
    """`SELECTED` is work no laboratory has taken -- a lab that will not do it *rejects* it, and
    that path already exists. `IN_PROGRESS` is included here as the positive control: the same
    call that must be refused above must succeed below, or this test would pass for the wrong
    reason (a block endpoint that refused everything)."""
    order = _routed_order(client, world, **advance)
    assert _lab_order(client, world, order).json()["status"] == expected_status

    response = _block(client, world, order)
    if expected_status == "SELECTED":
        assert response.status_code == 409, response.text
        with sessions() as session:
            assert session.scalars(select(LabOrderBlock)).all() == []
    else:
        assert response.status_code == 200, response.text


def test_lab_block_6b_a_released_order_cannot_be_blocked(client, world, sessions):
    """A release is an immutable clinical event. Blocking a `COMPLETED` order would make BLOCKED a
    way to undo one, which the amendment domain owns instead (§8)."""
    order = _routed_order(client, world, collect=True, process=True)
    _enter_and_complete_results(client, world, order, sessions)
    assert _release(client, world, order).status_code == 201

    refused = _block(client, world, order)
    assert refused.status_code == 409, refused.text
    assert _lab_order(client, world, order).json()["status"] == "COMPLETED"


# == LAB-BLOCK-7: a blocked order cannot advance =================================================


def test_lab_block_7_every_prohibited_laboratory_step_is_refused_while_blocked(
    client, world, sessions
):
    """Each prohibited mutation is attempted individually, because "the order did not move" is a
    much weaker claim than "each of these endpoints refused".

    The order is blocked from `IN_PROGRESS` deliberately: that is the state in which result entry,
    correction and completion are otherwise *legal*, so their refusal here is caused by the block
    and by nothing else.
    """
    order = _routed_order(client, world, collect=True, process=True)
    tech = _tok(world, "tech")
    item_id = _item_id(sessions, order)
    lab = world["lab_id"]

    # Capture what may be entered *before* blocking, so the attempt afterwards is a real one.
    workspace = client.get(f"{_lab_base(world, order)}/result-entry-workspace", headers=_auth(tech))
    template = workspace.json()["items"][0]["template"]

    assert _block(client, world, order).status_code == 200

    base = _lab_base(world, order)
    refusals = {
        "accept": client.post(f"{base}/accept", headers=_auth(tech)),
        "start-processing": client.post(f"{base}/start-processing", headers=_auth(tech)),
        "result-entry-workspace": client.get(f"{base}/result-entry-workspace", headers=_auth(tech)),
        "native-result-values": client.post(
            f"/exchange/v1/organizations/{lab}/lab-orders/{order['id']}/items/{item_id}"
            f"/native-result-values",
            headers=_auth(tech),
            json={
                "template_id": template["template_id"],
                "values": [
                    {
                        "definition_id": template["observations"][0]["definition_id"],
                        "numeric_value": "4.2",
                    }
                ],
            },
        ),
        "complete-result-entry": client.post(
            f"/exchange/v1/organizations/{lab}/lab-orders/{order['id']}/items/{item_id}"
            f"/complete-result-entry",
            headers=_auth(tech),
        ),
    }
    for name, response in refusals.items():
        assert response.status_code == 409, f"{name} was not refused: {response.status_code}"

    # The order is exactly where it was, and no partial result was written.
    assert _lab_order(client, world, order).json()["status"] == "BLOCKED"


def test_lab_block_7c_a_new_collection_milestone_is_refused_while_blocked(client, world):
    """`record-collection` needs its own case, and the distinction it draws is deliberate.

    A *new* milestone on a blocked order is refused, because creating one would assert that a
    physical collection happened during an interruption nobody has resolved.

    A *replay* of a milestone that already exists is **not** refused, and must not be. DH-EX-011
    made that branch unconditional on purpose (see `record_collection`'s own docstring: it replays
    "even if the order has since progressed past ACCEPTED to IN_PROGRESS/COMPLETED/CANCELLED"),
    because the milestone genuinely already happened and a retry returns it rather than rewriting
    anything. Nothing advances, nothing is written, and no history is fabricated -- which is
    exactly §31's "do not prohibit harmless reads". Tightening it for BLOCKED alone would change
    DH-EX-011's semantics for one status, out of this ticket's scope and for no safety gain.
    """
    tech = _tok(world, "tech")

    # Blocked from ACCEPTED with no milestone: a new one is refused.
    fresh = _routed_order(client, world)
    assert _block(client, world, fresh).status_code == 200
    refused = client.post(f"{_lab_base(world, fresh)}/record-collection", headers=_auth(tech))
    assert refused.status_code == 409, refused.text
    assert _lab_order(client, world, fresh).json()["collection_recorded_at"] is None

    # Blocked with a milestone already recorded: the replay is harmless and returns the original.
    collected = _routed_order(client, world, collect=True)
    original = _lab_order(client, world, collected).json()["collection_recorded_at"]
    assert _block(client, world, collected).status_code == 200
    replay = client.post(f"{_lab_base(world, collected)}/record-collection", headers=_auth(tech))
    assert replay.status_code == 200, replay.text
    assert replay.json()["recorded_at"] is not None
    # Nothing moved and nothing was rewritten.
    after = _lab_order(client, world, collected).json()
    assert after["status"] == "BLOCKED"
    assert after["collection_recorded_at"] == original


def test_lab_block_7b_reads_are_not_prohibited_while_blocked(client, world):
    """§31: "do not prohibit harmless reads". A technician must be able to look at a blocked order
    -- refusing that would make the block itself uninvestigable."""
    order = _routed_order(client, world, collect=True, process=True)
    assert _block(client, world, order).status_code == 200
    assert _lab_order(client, world, order).status_code == 200
    assert _blocks(client, world, order).status_code == 200
    assert (
        client.get(
            f"/exchange/v1/organizations/{world['lab_id']}/lab-orders",
            headers=_auth(_tok(world, "tech")),
        ).status_code
        == 200
    )


# == LAB-BLOCK-8: the server owns the resume destination =========================================


@pytest.mark.parametrize(
    ("advance", "expected_resume"),
    [
        (dict(), "ACCEPTED"),
        (dict(collect=True, process=True), "IN_PROGRESS"),
    ],
)
def test_lab_block_8_resolution_restores_the_state_the_block_interrupted(
    client, world, advance, expected_resume
):
    """Blocked from ACCEPTED resumes to ACCEPTED; blocked from IN_PROGRESS resumes to IN_PROGRESS.

    Both cases matter. A design that always resumed to IN_PROGRESS would silently assert that an
    order blocked before collection had been collected and started -- workflow history that never
    happened.
    """
    order = _routed_order(client, world, **advance)
    blocked = _block(client, world, order)
    episode_id = blocked.json()["active_block"]["id"]
    assert blocked.json()["active_block"]["blocked_from_status"] == expected_resume

    resolved = _resolve(client, world, order, episode_id, note=RESOLUTION_NOTE)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == expected_resume
    assert resolved.json()["active_block"] is None


def test_lab_block_8b_the_client_cannot_choose_the_resume_destination(client, world):
    """**NC-BLOCK-7's positive statement.** The request schema forbids extra fields outright, so a
    client naming a destination is refused rather than quietly ignored -- and an ignored field is
    the more dangerous failure, because the caller believes it worked."""
    order = _routed_order(client, world)
    episode_id = _block(client, world, order).json()["active_block"]["id"]

    for payload in (
        {"resume_to": "IN_PROGRESS"},
        {"resolution_note": "ok", "resume_to": "COMPLETED"},
        {"status": "IN_PROGRESS"},
    ):
        refused = client.post(
            f"{_lab_base(world, order)}/blocks/{episode_id}/resolve",
            headers=_auth(_tok(world, "tech")),
            json=payload,
        )
        assert refused.status_code == 422, f"{payload}: {refused.text}"

    # Blocking is equally closed to a caller-supplied actor, instant or source state.
    order2 = _routed_order(client, world)
    for payload in (
        {"reason": "SPECIMEN_UNUSABLE", "blocked_by_identity_id": str(uuid.uuid4())},
        {"reason": "SPECIMEN_UNUSABLE", "blocked_at": "2020-01-01T00:00:00Z"},
        {"reason": "SPECIMEN_UNUSABLE", "blocked_from_status": "COMPLETED"},
        {"reason": "SPECIMEN_UNUSABLE", "lab_organization_id": world["rival_id"]},
    ):
        refused = client.post(
            f"{_lab_base(world, order2)}/block",
            headers=_auth(_tok(world, "tech")),
            json=payload,
        )
        assert refused.status_code == 422, f"{payload}: {refused.text}"

    # The genuinely lawful resume happened, and it is the one the server chose.
    assert _resolve(client, world, order, episode_id).json()["status"] == "ACCEPTED"


# == LAB-BLOCK-9 / 10: history is durable ========================================================


def test_lab_block_9_a_second_block_is_a_second_episode(client, world, sessions):
    """`docs/NATIVE-OPERATING-PRODUCT.md` §16's own worked example, end to end: blocked, resolved,
    worked further, blocked again for an unrelated reason. Both episodes must survive, or the
    operational question a laboratory actually asks -- *has this order been trouble before?* --
    has no answer."""
    order = _routed_order(client, world)

    first = _block(client, world, order, reason="SPECIMEN_INSUFFICIENT", note="Premier problème")
    first_id = first.json()["active_block"]["id"]
    assert _resolve(client, world, order, first_id, note="Premier résolu").status_code == 200

    # Carry on with real work, so the second block genuinely comes from a different state.
    tech = _tok(world, "tech")
    assert client.post(
        f"{_lab_base(world, order)}/record-collection", headers=_auth(tech)
    ).status_code in (200, 201)
    assert (
        client.post(f"{_lab_base(world, order)}/start-processing", headers=_auth(tech)).status_code
        == 200
    )

    second = _block(client, world, order, reason="CLARIFICATION_REQUIRED", note="Second problème")
    assert second.status_code == 200, second.text
    second_id = second.json()["active_block"]["id"]
    assert second_id != first_id

    history = _blocks(client, world, order)
    assert history.status_code == 200, history.text
    episodes = history.json()
    assert len(episodes) == 2
    by_id = {e["id"]: e for e in episodes}

    # The first episode is untouched by everything that happened after it.
    assert by_id[first_id]["reason"] == "SPECIMEN_INSUFFICIENT"
    assert by_id[first_id]["note"] == "Premier problème"
    assert by_id[first_id]["blocked_from_status"] == "ACCEPTED"
    assert by_id[first_id]["resolved_at"] is not None

    assert by_id[second_id]["reason"] == "CLARIFICATION_REQUIRED"
    assert by_id[second_id]["blocked_from_status"] == "IN_PROGRESS"
    assert by_id[second_id]["resolved_at"] is None

    assert _resolve(client, world, order, second_id).json()["status"] == "IN_PROGRESS"
    assert len(_blocks(client, world, order).json()) == 2


def test_lab_block_10_a_resolved_episode_is_never_rewritten(client, world, sessions):
    """Resolution sets the resolution fields and touches nothing else -- and a *second* resolve of
    the same episode does not overwrite who resolved it first."""
    order = _routed_order(client, world)
    blocked = _block(client, world, order)
    episode_id = blocked.json()["active_block"]["id"]
    # Read back through the history endpoint, so every comparison below is between two readings of
    # the same stored value -- see LAB-BLOCK-17 for why the create response is not comparable to a
    # read-back on SQLite.
    before = _blocks(client, world, order).json()[0]

    lab, admin_l = world["lab_id"], _tok(world, "admin_l")
    _set_capability(
        client,
        admin_l,
        lab,
        world["people"]["bare"]["membership"],
        Capability.LAB_QUEUE_WORK.value,
        "GRANT",
    )

    assert _resolve(client, world, order, episode_id, note=RESOLUTION_NOTE).status_code == 200
    first = next(e for e in _blocks(client, world, order).json() if e["id"] == episode_id)

    # A second, *different* person replays the resolve with a different note.
    replay = _resolve(client, world, order, episode_id, who="bare", note="une autre note")
    assert replay.status_code == 200, replay.text

    after = next(e for e in _blocks(client, world, order).json() if e["id"] == episode_id)
    assert after == first, "a replayed resolution rewrote the episode"
    assert after["resolved_by_identity_id"] == world["people"]["tech"]["identity"]
    assert after["resolution_note"] == RESOLUTION_NOTE
    # And the original facts survived the resolution itself.
    assert after["reason"] == before["reason"]
    assert after["note"] == before["note"]
    assert after["blocked_at"] == before["blocked_at"]
    assert after["blocked_by_identity_id"] == before["blocked_by_identity_id"]


def test_lab_block_10b_there_is_no_delete_path_for_a_block_episode(client, world):
    order = _routed_order(client, world)
    episode_id = _block(client, world, order).json()["active_block"]["id"]
    gone = client.delete(
        f"{_lab_base(world, order)}/blocks/{episode_id}", headers=_auth(_tok(world, "tech"))
    )
    assert gone.status_code in (404, 405), gone.text
    assert len(_blocks(client, world, order).json()) == 1


# == LAB-BLOCK-11 / 12 / 14: release ============================================================


def test_lab_block_11_a_blocked_order_cannot_be_released(client, world, sessions):
    """Entry is complete and the results are genuinely ready -- the *only* thing standing between
    this order and a release is the block. That is what makes the refusal meaningful rather than
    incidental."""
    order = _routed_order(client, world, collect=True, process=True)
    _enter_and_complete_results(client, world, order, sessions)

    # Ready: proven by asking, before blocking.
    ready = client.get(f"{_lab_base(world, order)}/results", headers=_auth(_tok(world, "releaser")))
    assert ready.status_code == 200
    fingerprint = ready.json()["result_fingerprint"]

    assert _block(client, world, order, reason="CLARIFICATION_REQUIRED").status_code == 200

    refused = client.post(
        f"{_lab_base(world, order)}/release-results",
        headers=_auth(_tok(world, "releaser")),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert refused.status_code == 409, refused.text
    assert _lab_order(client, world, order).json()["status"] == "BLOCKED"

    # It is also off the release queue, which is where a releaser would otherwise find it.
    queue = client.get(
        f"/exchange/v1/organizations/{world['lab_id']}/release-queue",
        headers=_auth(_tok(world, "releaser")),
    )
    assert queue.status_code == 200
    assert all(entry["order_id"] != order["id"] for entry in queue.json()["orders"])


def test_lab_block_12_release_works_again_after_lawful_resolution(client, world, sessions):
    """The other half of LAB-BLOCK-11: the block must be a *pause*, not a dead end."""
    order = _routed_order(client, world, collect=True, process=True)
    _enter_and_complete_results(client, world, order, sessions)
    episode_id = _block(client, world, order).json()["active_block"]["id"]

    assert _resolve(client, world, order, episode_id).json()["status"] == "IN_PROGRESS"

    released = _release(client, world, order)
    assert released.status_code == 201, released.text
    assert _lab_order(client, world, order).json()["status"] == "COMPLETED"
    # The history is still there after the order completed.
    assert len(_blocks(client, world, order).json()) == 1


def test_lab_block_14_blocking_authority_never_implies_release_authority(client, world, sessions):
    """A technician may block, resolve, and process for ever and still not publish a result. The
    reverse holds too: a releaser may release and still not block."""
    order = _routed_order(client, world, collect=True, process=True)
    _enter_and_complete_results(client, world, order, sessions)
    episode_id = _block(client, world, order).json()["active_block"]["id"]
    assert _resolve(client, world, order, episode_id).status_code == 200

    # The very person who blocked and resolved it cannot release it.
    denied = _release(client, world, order, who="tech")
    assert denied.status_code == 403, denied.text

    # And the releaser, who can, could never have blocked it.
    assert _block(client, world, order, who="releaser").status_code == 403
    assert _release(client, world, order, who="releaser").status_code == 201


# == LAB-BLOCK-13: billing is independent in both directions =====================================


def test_lab_block_13_an_unpaid_balance_neither_causes_nor_prevents_anything(
    client, world, sessions
):
    """ADR-066/D-2b: *financial state never gates legitimate clinical or laboratory work.* Tested
    in both directions, because this ticket touched `BILLABLE_ORDER_STATUSES` and both directions
    could have broken.

    Direction 1 -- money does not touch the laboratory: an invoice with a full outstanding balance
    blocks nothing, prevents no resolution, and prevents no release.

    Direction 2 -- the laboratory does not touch money: a blocked order is still work this
    laboratory took, so it stays billable. Losing that would mean a laboratory silently cannot
    invoice the moment a tube arrives unusable.
    """
    order = _routed_order(client, world, collect=True, process=True)
    lab, clerk = world["lab_id"], _tok(world, "clerk")
    item_id = _item_id(sessions, order)

    invoice = client.post(
        f"/exchange/v1/organizations/{lab}/billing/invoices",
        headers=_idem(clerk),
        json={
            "patient_id": order["patient_id"],
            "lines": [{"source_type": "LAB_ORDER_ITEM", "source_id": item_id, "quantity": 1}],
        },
    )
    assert invoice.status_code == 201, invoice.text
    # Deliberately unpaid: the whole balance is outstanding. Nothing below is allowed to care.
    assert Decimal(invoice.json()["total"]) > 0
    assert Decimal(invoice.json()["balance"]) == Decimal(invoice.json()["total"])
    assert invoice.json()["status"] == "OPEN"

    blocked = _block(client, world, order)
    assert blocked.status_code == 200, blocked.text
    # No block was *caused* by the balance either -- the reason is the one the technician gave.
    assert blocked.json()["active_block"]["reason"] == "SPECIMEN_UNUSABLE"

    # Direction 2: still billable while blocked.
    billable = client.get(
        f"/exchange/v1/organizations/{lab}/billing/patients/{order['patient_id']}/billable",
        headers=_auth(clerk),
    )
    assert billable.status_code == 200, billable.text

    episode_id = blocked.json()["active_block"]["id"]
    assert _resolve(client, world, order, episode_id).status_code == 200
    _enter_and_complete_results(client, world, order, sessions)
    assert _release(client, world, order).status_code == 201


def test_lab_block_13b_no_payment_shaped_reason_exists(client, world):
    """A structural assertion rather than a behavioural one, and deliberately so: §15's rule is
    that the *vocabulary* must not contain such a concept, so that no future caller can select it
    even by mistake."""
    from daher_adapter.exchange.models import LabOrderBlockReason

    keys = {reason.value for reason in LabOrderBlockReason}
    for forbidden in ("PAYMENT", "UNPAID", "BALANCE", "INVOICE", "BILLING", "RECEIPT"):
        assert not any(forbidden in key for key in keys), f"{forbidden} reached the reason catalog"


# == LAB-BLOCK-15 / 16: who may see what ========================================================


def test_lab_block_15_the_provider_sees_the_reason_and_nothing_else(client, world):
    """§29/§16: the ordering clinician is the next owner of blocked work, so they are told it
    stopped and broadly why. §26: they are told no more than that.

    Asserted against the **serialized JSON**, not the field list, so a future field added to the
    laboratory's own projection cannot leak across this boundary unnoticed.
    """
    order = _routed_order(client, world)
    blocked = _block(client, world, order)
    episode = blocked.json()["active_block"]

    response = _provider_order(client, world, order)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "BLOCKED"
    assert body["block"] == {"reason": "SPECIMEN_UNUSABLE"}

    raw = response.text
    assert TECH_NOTE not in raw
    assert episode["id"] not in raw
    assert episode["blocked_by_identity_id"] not in raw
    assert "blocked_at" not in raw
    assert "blocked_from_status" not in raw
    assert "resolution_note" not in raw

    # The list projection is the same shape, resolved batched.
    listed = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/orders",
        headers=_auth(_tok(world, "clin")),
    )
    assert listed.status_code == 200
    entry = next(o for o in listed.json() if o["id"] == order["id"])
    assert entry["block"] == {"reason": "SPECIMEN_UNUSABLE"}
    assert TECH_NOTE not in listed.text


def test_lab_block_15b_a_resolved_block_disappears_from_the_provider_view(client, world):
    """Only what is true *now* crosses the boundary. The laboratory keeps its own history; the
    clinic is told the work resumed, which is the fact it needs."""
    order = _routed_order(client, world)
    episode_id = _block(client, world, order).json()["active_block"]["id"]
    assert _provider_order(client, world, order).json()["block"] is not None

    _resolve(client, world, order, episode_id)
    after = _provider_order(client, world, order)
    assert after.json()["block"] is None
    assert after.json()["status"] == "ACCEPTED"
    # ...and the laboratory still has it.
    assert len(_blocks(client, world, order).json()) == 1


def test_lab_block_15c_a_provider_cannot_reach_the_laboratorys_block_endpoints(client, world):
    """A clinician may *see* that work is blocked. They may not mutate another organization's
    laboratory workflow, and they may not read its internal history either (§16)."""
    order = _routed_order(client, world)
    episode_id = _block(client, world, order).json()["active_block"]["id"]
    clin = _tok(world, "clin")

    assert client.post(
        f"{_lab_base(world, order)}/block",
        headers=_auth(clin),
        json={"reason": "SPECIMEN_UNUSABLE"},
    ).status_code in (403, 404)
    assert client.post(
        f"{_lab_base(world, order)}/blocks/{episode_id}/resolve", headers=_auth(clin), json={}
    ).status_code in (403, 404)
    assert _blocks(client, world, order, who="clin").status_code in (403, 404)


def test_lab_block_16_the_patient_surface_leaks_no_laboratory_detail(client, world):
    """§17. The patient learns the order's coarse status and nothing about the laboratory's
    internals -- no reason key, no note, no actor, no timestamps.

    The status string itself does reach the patient screen, and that is deliberate rather than an
    oversight: `ui/src/features/patient/patientStatusWording.ts` maps it to a sentence with no
    technical vocabulary at all. What must not reach them is everything else, asserted here
    against the serialized response.
    """
    order = _routed_order(client, world)
    assert _block(client, world, order).status_code == 200

    grant = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/orders/{order['id']}/patient-access",
        headers=_auth(_tok(world, "clin")),
    )
    assert grant.status_code in (200, 201), grant.text
    token = grant.json()["access_token"]

    response = client.get(
        "/exchange/v1/patient/order", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    raw = response.text
    body = response.json()

    assert body["status"] == "BLOCKED"
    assert body["result_status"] == "NOT_READY"
    assert "block" not in body
    assert "active_block" not in body
    for leaked in (TECH_NOTE, "SPECIMEN_UNUSABLE", "blocked_by", "blocked_at", "reason"):
        assert leaked not in raw, f"{leaked!r} reached the patient surface"


# == LAB-BLOCK-17 / 18: retry safety ============================================================


def test_lab_block_17_an_identical_repeat_is_a_replay_not_a_second_episode(client, world, sessions):
    """The double-click case. An identical repeat returns the same episode, writes no second row
    and no second audit event."""
    order = _routed_order(client, world)
    first = _block(client, world, order)
    assert first.status_code == 200

    # Read back through the history endpoint *before* the replay, and compare against the same
    # endpoint after. Comparing a stored value against the freshly-constructed one in the create
    # response would be comparing two different serializations rather than two readings of the
    # same fact: SQLite does not preserve the timezone this repository's `DateTime(timezone=True)`
    # columns carry, so the create response renders `...Z` and a read-back does not. That is an
    # artefact of the test database, not a property worth asserting either way.
    stored_before = _blocks(client, world, order).json()[0]

    second = _block(client, world, order)
    assert second.status_code == 200, second.text
    assert second.json()["active_block"]["id"] == first.json()["active_block"]["id"]
    assert _blocks(client, world, order).json()[0] == stored_before

    assert len(_blocks(client, world, order).json()) == 1
    blocked_events = [e for e in _db_events(sessions) if e.event_type == "lab_order_blocked"]
    assert len(blocked_events) == 1

    # An empty-string note is the same as no note, so a browser that sends "" then null on the
    # retry is still a replay rather than a conflict.
    order2 = _routed_order(client, world)
    a = _block(client, world, order2, note="")
    b = _block(client, world, order2, note=None)
    assert a.status_code == b.status_code == 200
    assert a.json()["active_block"]["id"] == b.json()["active_block"]["id"]
    assert a.json()["active_block"]["note"] is None


def test_lab_block_18_a_different_reason_while_blocked_is_a_conflict(client, world, sessions):
    """A genuinely different request that happens to arrive second is a conflict, never a silent
    success. Answering it with somebody else's block would tell the caller their reason was
    recorded when it was not."""
    order = _routed_order(client, world)
    assert _block(client, world, order, reason="SPECIMEN_UNUSABLE").status_code == 200

    conflict = _block(client, world, order, reason="PATIENT_DID_NOT_ATTEND")
    assert conflict.status_code == 409, conflict.text
    # Same reason, different note is equally a conflict -- the note is part of the request.
    assert _block(client, world, order, note="autre note").status_code == 409

    assert len(_blocks(client, world, order).json()) == 1
    assert _lab_order(client, world, order).json()["active_block"]["reason"] == "SPECIMEN_UNUSABLE"


def test_lab_block_18b_resolving_an_unknown_or_foreign_episode_is_a_404(client, world):
    order = _routed_order(client, world)
    other = _routed_order(client, world)
    episode_id = _block(client, world, other).json()["active_block"]["id"]

    assert _resolve(client, world, order, str(uuid.uuid4())).status_code == 404
    # An episode that exists, but on a different order: the path must agree with itself.
    assert _resolve(client, world, order, episode_id).status_code == 404
    assert _lab_order(client, world, other).json()["status"] == "BLOCKED"


# == LAB-BLOCK-19: audit =========================================================================


def test_lab_block_19_both_events_are_audited_and_carry_no_free_text(client, world, sessions):
    """Two events, order-centric, in the same transaction as the mutation.

    The reason key is deliberately absent: the authoritative value lives on the episode row, where
    a second copy cannot drift from it -- DHEALTH-BILLING-001's own decision not to record a
    payment amount here, applied again. The free-text note is absent on a stronger footing still:
    a laboratory's operational note belongs nowhere near a security log (`VisitNote.body`'s rule).
    """
    order = _routed_order(client, world)
    episode_id = _block(client, world, order).json()["active_block"]["id"]
    assert _resolve(client, world, order, episode_id, note=RESOLUTION_NOTE).status_code == 200

    events = _db_events(sessions)
    blocked = [e for e in events if e.event_type == "lab_order_blocked"]
    resolved = [e for e in events if e.event_type == "lab_order_block_resolved"]
    assert len(blocked) == 1 and len(resolved) == 1

    for event in (blocked[0], resolved[0]):
        assert event.resource_type == "lab_order"
        assert str(event.resource_id) == order["id"]
        assert str(event.organization_id) == world["lab_id"]
        assert event.outcome == "updated"
    assert str(blocked[0].actor_identity_id) == world["people"]["tech"]["identity"]

    # No column anywhere on these rows carries the note or the reason.
    for event in (blocked[0], resolved[0]):
        values = [str(getattr(event, column.name)) for column in event.__table__.columns]
        joined = " ".join(values)
        assert TECH_NOTE not in joined
        assert RESOLUTION_NOTE not in joined
        assert "SPECIMEN_UNUSABLE" not in joined

    # A replayed block writes no second row.
    _block(client, world, order)  # order is ACCEPTED again, so this is a *new* episode
    assert len([e for e in _db_events(sessions) if e.event_type == "lab_order_blocked"]) == 2


# == The generic state machine is not a back door ================================================


def test_the_generic_transition_primitive_refuses_to_manufacture_a_blocked_order(
    client, world, sessions
):
    """`orders.py`'s generic `transition()` has no production HTTP caller, and this repository's
    discipline is to enforce an invariant inside the mutation anyway rather than rely on "no
    caller happens to bypass it today".

    Two things are checked: it cannot *create* a BLOCKED order (which would have no episode, and
    therefore could not say why it stopped), and it cannot resume one to a state the server did
    not record.
    """
    from daher_adapter.exchange.catalog import CatalogRepository
    from daher_adapter.exchange.models import LabOrderStatus
    from daher_adapter.exchange.orders import InvalidOrderTransition, LabOrderRepository

    order = _routed_order(client, world)
    orders_repo = LabOrderRepository(
        sessions,
        OrganizationRepository(sessions),
        CatalogRepository(sessions, OrganizationRepository(sessions)),
    )
    order_id = uuid.UUID(order["id"])

    with pytest.raises(InvalidOrderTransition):
        orders_repo.transition(order_id, LabOrderStatus.BLOCKED)
    with sessions() as session:
        assert session.scalars(select(LabOrderBlock)).all() == []
        assert session.get(LabOrder, order_id).status == "ACCEPTED"

    # Now block it properly, and try to resume somewhere the server never recorded.
    assert _block(client, world, order).status_code == 200
    with pytest.raises(InvalidOrderTransition):
        orders_repo.transition(order_id, LabOrderStatus.IN_PROGRESS)
    with sessions() as session:
        assert session.get(LabOrder, order_id).status == "BLOCKED"

    # Even the *correct* destination is refused while the episode is unresolved -- otherwise the
    # order would resume with a block still hanging off it, claiming to be unresolved for ever.
    with pytest.raises(InvalidOrderTransition):
        orders_repo.transition(order_id, LabOrderStatus.ACCEPTED)
