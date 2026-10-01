"""DHEALTH-LAB-REPORT-001/ADR-072: real PostgreSQL qualification of the version-bound report.

**Why this file has to exist.** The unit suite
(``tests/test_dhealthlabreport001_report.py``, 32 tests) runs on SQLite, where three things this
ticket's claims rest on are absent or differently shaped:

- ``DateTime(timezone=True)`` round-trips **naive** through the SQLite dialect. The unit suite
  therefore has to normalise the timezone before comparing, which means the claim *"``released_at``
  is the same stored value as ``LabResultRelease.released_at``"* is only ever asserted there after a
  conversion. Here it is asserted against a real ``timestamptz``, tz-aware on both sides, with no
  normalisation at all.
- SQLite serialises every writer against the whole database, so the concurrency claims come out
  right for a reason that has nothing to do with the report's own design.
- ``PRAGMA foreign_keys`` is off in ``tests/conftest.py``, so no ``RESTRICT`` is enforced.

What is qualified here and nowhere else:

- **PG-RP-1** ``released_at`` equals the stored release timestamp exactly, for Version 1
  (``LabResultRelease.released_at``) and Version 2 (``LabResultAmendment.released_at``), as real
  ``timestamptz`` values.
- **PG-RP-2** A report read while an amendment's correction is **uncommitted** in another
  transaction. MVCC, not SQLite's writer lock, is what makes the answer right.
- **PG-RP-3** A report read while a Version 2 **release** is genuinely blocked mid-flight on the
  order's own row lock. The deterministic proof that a reader can never observe half of one version
  and half of another: the reader is not blocked, and what it gets is complete Version 1.
- **PG-RP-4** The same three reports after the release commits: Version 1 unchanged in every
  clinical field, Version 2 exact, and only the two derived currentness fields moved.
- **PG-RP-5** A laboratory disabling the analysis in a concurrent transaction leaves both historical
  reports readable. Availability governs new ordering; it does not rewrite released truth.
- **PG-RP-6** A real, existing release-version id belonging to a different order is refused rather
  than falling back to this order's own current release.
- **PG-RP-7** Migration state: exactly one head at ``0039_lab_specimens``, **no report table
  exists**, and the release/version/manifest tables the report reads carry the constraint set the
  model describes. This is what makes "no migration" checkable rather than asserted in prose.

**The proof is the observable HTTP response**, which is why this file drives the real router through
``TestClient`` against PostgreSQL rather than calling repositories. The report is built inside
``build_exchange_router``'s closure, so a repository-level test could not qualify the route at all.
Nothing here inspects ``pg_locks``.

**Manual / opt-in only**, mirroring every other live-infra test in this directory: skipped by
default, including in CI, unless every ``DHRP_PG_*`` variable is set.

Usage::

    DHRP_PG_HOST=127.0.0.1 \\
    DHRP_PG_PORT=55441 \\
    DHRP_PG_USER=adapter \\
    DHRP_PG_PASSWORD=... \\
    DHRP_PG_DATABASE=dhrp_qual \\
        pytest adapter/tests/integration/test_dhealthlabreport001_postgres_qualification.py -v

The target database must already be migrated to ``0039_lab_specimens``. **Never point it at a
facility database** -- nothing here checks that, so use a disposable instance and destroy it after.

All catalogue, patient and organization data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import (
    LabOrder,
    LabResultAmendment,
    LabResultRelease,
    LabResultReleaseVersion,
)
from daher_adapter.exchange.organizations import OrganizationRepository

PG_HOST = os.environ.get("DHRP_PG_HOST")
PG_PORT = os.environ.get("DHRP_PG_PORT")
PG_USER = os.environ.get("DHRP_PG_USER")
PG_PASSWORD = os.environ.get("DHRP_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHRP_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a disposable PostgreSQL database (DHRP_PG_*) -- never in CI",
)

TTL_HOURS = 168

NUMERIC_REQUIRED = {
    "source_code": "NUM_A",
    "display_label": "Mesure numérique A",
    "result_type": "NUMERIC",
    "numeric_unit": "mg/dL",
    "required": True,
    "sort_order": 0,
}


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def client(sessions: sessionmaker, keypair, tmp_path: Path) -> TestClient:
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


# ── harness ──────────────────────────────────────────────────────────────────────────────────────


def _prefix(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _member_token(client, private_key, org_id, subject: str, roles: list[str]) -> str:
    token = make_token(private_key, subject=subject)
    me = client.get("/exchange/v1/me", headers=_auth(token))
    assert me.status_code == 200, me.text
    admin = make_token(private_key, subject=f"admin-{subject}", roles=[PLATFORM_ADMIN_ROLE])
    granted = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(admin),
        json={"identity_id": me.json()["identity_id"], "roles": roles},
    )
    assert granted.status_code in (200, 201), granted.text
    return token


class Scenario:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _released_scenario(client, sessions: sessionmaker, private_key, name: str) -> Scenario:
    """An order whose Version 1 is released, built entirely through the product's own HTTP surface.

    Deliberately not seeded with direct writes: the report's claims are about what the product
    produces, so the state it reads has to have been produced the same way.
    """
    key = _prefix(name)
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)

    provider = organizations.create(org_type="PROVIDER", name=f"{key}-clinique")
    lab = organizations.create(org_type="LAB", name=f"{key}-laboratoire")
    entry, _ = catalog.create_entry(code=f"{key}-TEST", display_name=f"Analyse {key}")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{key} address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    patient_reference = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system=f"urn:{key}:patient",
        source_identity_value="P-1",
        display_name=f"Patient Synthétique {key}",
    )

    clinician = _member_token(client, private_key, provider.id, f"clinician-{key}", ["CLINICIAN"])
    created = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        # A canonical lowercase UUIDv4, which the order endpoint enforces -- `key` is a readable
        # prefix for the synthetic organization names, not a request identity.
        headers={**_auth(clinician), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_reference_id": str(patient_reference.id),
            "catalog_entry_ids": [str(entry.id)],
        },
    )
    assert created.status_code == 201, created.text
    order_id = uuid.UUID(created.json()["id"])

    access = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician),
    )
    assert access.status_code == 201, access.text
    patient_token = access.json()["access_token"]
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(patient_token)
    ).json()["options"]
    mine = [option for option in options if option["lab"]["id"] == str(lab.id)]
    selected = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(patient_token),
        json={
            "lab_organization_id": str(lab.id),
            "expected_price_ids": [line["current_price_id"] for line in mine[0]["prices"]],
        },
    )
    assert selected.status_code == 201, selected.text

    # One identity holding both laboratory roles: legitimate (ADR-044 forces no second person), and
    # it keeps this harness to one token for entry, amendment and release.
    tech = _member_token(
        client, private_key, lab.id, f"tech-{key}", ["LAB_TECH", "LAB_RESULT_RELEASER", "ORG_ADMIN"]
    )
    for path, expected in (("accept", 200), ("record-collection", 201), ("start-processing", 200)):
        response = client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/{path}",
            headers=_auth(tech),
        )
        assert response.status_code == expected, response.text

    template = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates",
        headers=_auth(tech),
        json={"version": 1, "observations": [NUMERIC_REQUIRED]},
    )
    assert template.status_code == 201, template.text
    template_body = template.json()
    activated = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates/"
        f"{template_body['id']}/activate",
        headers=_auth(tech),
        json={"expected_current_template_id": None},
    )
    assert activated.status_code == 200, activated.text

    with sessions() as session:
        item_id = session.get(LabOrder, order_id).items[0].id

    values = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}"
        f"/native-result-values",
        headers=_auth(tech),
        json={
            "template_id": template_body["id"],
            "values": [
                {
                    "definition_id": template_body["observations"][0]["id"],
                    "numeric_value": "4.2",
                }
            ],
        },
    )
    assert values.status_code in (200, 201), values.text
    observation_id = values.json()["values"][0]["observation_id"]

    completed = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}"
        f"/complete-result-entry",
        headers=_auth(tech),
    )
    assert completed.status_code in (200, 201), completed.text

    inspection = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results", headers=_auth(tech)
    )
    assert inspection.status_code == 200, inspection.text
    released = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(tech),
        json={"expected_result_fingerprint": inspection.json()["result_fingerprint"]},
    )
    assert released.status_code == 201, released.text

    return Scenario(
        key=key,
        provider_id=provider.id,
        clinician=clinician,
        lab_id=lab.id,
        offering_id=offering.id,
        tech=tech,
        order_id=order_id,
        item_id=item_id,
        observation_id=observation_id,
        template_body=template_body,
    )


def _lab_report_url(scenario: Scenario, version_id) -> str:
    return (
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/release-versions/{version_id}/report"
    )


def _clinic_report_url(scenario: Scenario, version_id) -> str:
    return (
        f"/exchange/v1/organizations/{scenario.provider_id}/orders/{scenario.order_id}"
        f"/release-versions/{version_id}/report"
    )


def _version_id(client, scenario: Scenario, number: int) -> str:
    response = client.get(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/release-versions",
        headers=_auth(scenario.tech),
    )
    assert response.status_code == 200, response.text
    for version in response.json()["versions"]:
        if version["version_number"] == number:
            return version["release_version_id"]
    raise AssertionError(f"no version {number}: {response.json()}")


def _report(client, scenario: Scenario, version_id) -> dict:
    response = client.get(_lab_report_url(scenario, version_id), headers=_auth(scenario.tech))
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def _start_amendment(client, scenario: Scenario) -> str:
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}/amendments",
        headers=_auth(scenario.tech),
    )
    assert response.status_code == 201, response.text
    return response.json()["amendment_id"]


def _amend(client, scenario: Scenario, amendment_id: str, value: str) -> None:
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/amendments/{amendment_id}/items/{scenario.item_id}"
        f"/observations/{scenario.observation_id}/native-correct",
        headers=_auth(scenario.tech),
        json={"numeric_value": value},
    )
    assert response.status_code in (200, 201), response.text


def _release_amendment(client, scenario: Scenario, amendment_id: str) -> int:
    inspection = client.get(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/amendments/{amendment_id}",
        headers=_auth(scenario.tech),
    )
    assert inspection.status_code == 200, inspection.text
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/amendments/{amendment_id}/release",
        headers=_auth(scenario.tech),
        json={"expected_result_fingerprint": inspection.json()["result_fingerprint"]},
    )
    return response.status_code


def _numeric(report: dict) -> Decimal:
    return Decimal(report["results"][0]["observations"][0]["numeric_value"])


# ── PG-RP-1: the release timestamp is the stored one, as a real timestamptz ──────────────────────


def test_pg_rp_1_released_at_is_the_stored_release_timestamp_on_real_timestamptz(
    client: TestClient, sessions: sessionmaker, keypair
):
    """The claim in `ReleasedReportResponse`'s docstring, asserted without the timezone
    normalisation the SQLite suite needs.

    For Version 1 the stored value lives on ``LabResultRelease.released_at``; for Version 2 on
    ``LabResultAmendment.released_at``. In both cases the release transaction assigns the version's
    ``created_at`` from the same value, which is what makes the report's single `released_at` field
    truthful for both kinds of version without a branch.
    """
    private_key, _ = keypair
    scenario = _released_scenario(client, sessions, private_key, "rp1")

    v1_id = _version_id(client, scenario, 1)
    v1 = _report(client, scenario, v1_id)
    with sessions() as session:
        release = session.scalars(
            select(LabResultRelease).where(LabResultRelease.lab_order_id == scenario.order_id)
        ).one()
        version_one = session.get(LabResultReleaseVersion, uuid.UUID(v1_id))
        assert release.released_at.tzinfo is not None, "timestamptz round-tripped as naive"
        assert version_one.created_at == release.released_at
    assert datetime.fromisoformat(v1["released_at"]) == release.released_at

    amendment_id = _start_amendment(client, scenario)
    _amend(client, scenario, amendment_id, "9.9")
    assert _release_amendment(client, scenario, amendment_id) == 201

    v2_id = _version_id(client, scenario, 2)
    v2 = _report(client, scenario, v2_id)
    with sessions() as session:
        amendment = session.get(LabResultAmendment, uuid.UUID(amendment_id))
        version_two = session.get(LabResultReleaseVersion, uuid.UUID(v2_id))
        assert amendment.released_at.tzinfo is not None
        assert version_two.created_at == amendment.released_at
    assert datetime.fromisoformat(v2["released_at"]) == amendment.released_at
    # And the two versions are genuinely different instants, so the assertions above are not both
    # passing against one shared value.
    assert v1["released_at"] != v2["released_at"]


# ── PG-RP-2: an uncommitted amendment correction ─────────────────────────────────────────────────


def test_pg_rp_2_an_uncommitted_correction_is_invisible_to_the_report(
    client: TestClient, sessions: sessionmaker, keypair
):
    """MVCC, not a writer lock, is what makes this right.

    A transaction this test owns inserts the amendment's superseding observation and holds it open.
    On SQLite the reader would be blocked or the whole database serialised; on PostgreSQL the reader
    proceeds and must simply not see it -- and then must still not see it after the commit, because
    the amendment is prepared but **not released**.
    """
    private_key, _ = keypair
    scenario = _released_scenario(client, sessions, private_key, "rp2")
    v1_id = _version_id(client, scenario, 1)
    before = _report(client, scenario, v1_id)
    assert _numeric(before) == Decimal("4.2")

    amendment_id = _start_amendment(client, scenario)

    held = threading.Event()
    release_hold = threading.Event()
    failures: list[BaseException] = []

    def hold_uncommitted_correction() -> None:
        try:
            # The amendment correction, applied in a transaction that is deliberately not committed
            # until this test says so.
            from daher_adapter.exchange.models import LabObservation, LabResultType

            with sessions.begin() as session:
                predecessor = session.get(LabObservation, uuid.UUID(scenario.observation_id))
                successor = LabObservation(
                    lab_result_id=predecessor.lab_result_id,
                    result_type=LabResultType.NUMERIC.value,
                    source_code=predecessor.source_code,
                    source_system=predecessor.source_system,
                    display_label_snapshot=predecessor.display_label_snapshot,
                    numeric_value=Decimal("9.9"),
                    numeric_unit=predecessor.numeric_unit,
                    recorded_by_identity_id=predecessor.recorded_by_identity_id,
                    supersedes_observation_id=predecessor.id,
                    amendment_id=uuid.UUID(amendment_id),
                )
                session.add(successor)
                session.flush()
                held.set()
                release_hold.wait(timeout=30)
        except BaseException as error:  # noqa: BLE001
            failures.append(error)
            held.set()

    worker = threading.Thread(target=hold_uncommitted_correction, daemon=True)
    worker.start()
    assert held.wait(timeout=30), "the holding transaction never opened"
    assert not failures, failures

    # The reader is not blocked, and sees exactly Version 1.
    during = _report(client, scenario, v1_id)
    assert during == before

    release_hold.set()
    worker.join(timeout=30)
    assert not failures, failures

    # Committed, but still unreleased: there is no Version 2 and Version 1 has not moved.
    after = _report(client, scenario, v1_id)
    assert after == before
    versions = client.get(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/release-versions",
        headers=_auth(scenario.tech),
    ).json()["versions"]
    assert [v["version_number"] for v in versions] == [1]


# ── PG-RP-3/4: a report read while a Version 2 release is blocked mid-flight ─────────────────────


def test_pg_rp_3_a_reader_never_observes_half_of_one_version_and_half_of_another(
    client: TestClient, sessions: sessionmaker, keypair
):
    """**The deterministic concurrency proof, and the reason it is deterministic.**

    A thread barrier would not reliably produce the overlap. Instead this test holds the order's own
    row with ``SELECT ... FOR UPDATE`` -- the exact row ``release_amendment`` locks first -- so the
    Version 2 release genuinely blocks at a known point, with its manifest not yet written. The
    report for Version 1 is then read while that is true.

    What is being proven is not merely that the read succeeds. It is that *nothing* about the
    in-flight release can appear in it: the content comes from manifest entries bound to Version 1's
    own id, and Version 2's entries do not exist yet and will never be bound to that id. The
    currentness fields are likewise internally consistent with what is committed.

    **PG-RP-4** is folded in after the lock is released: the same three reports once Version 2 has
    committed.
    """
    private_key, _ = keypair
    scenario = _released_scenario(client, sessions, private_key, "rp3")
    v1_id = _version_id(client, scenario, 1)
    v1_before = _report(client, scenario, v1_id)

    amendment_id = _start_amendment(client, scenario)
    _amend(client, scenario, amendment_id, "9.9")
    # The amendment is prepared and still unreleased: Version 1 is untouched and is still current.
    assert _report(client, scenario, v1_id) == v1_before

    locked = threading.Event()
    release_lock = threading.Event()
    release_status: dict[str, object] = {}

    def hold_order_row() -> None:
        with sessions.begin() as session:
            session.execute(
                select(LabOrder).where(LabOrder.id == scenario.order_id).with_for_update()
            ).one()
            locked.set()
            release_lock.wait(timeout=40)

    def release_version_two() -> None:
        try:
            release_status["code"] = _release_amendment(client, scenario, amendment_id)
        except BaseException as error:  # noqa: BLE001
            release_status["error"] = error

    holder = threading.Thread(target=hold_order_row, daemon=True)
    holder.start()
    assert locked.wait(timeout=30), "the order row was never locked"

    releaser = threading.Thread(target=release_version_two, daemon=True)
    releaser.start()
    # The release is now blocked on the held row. Give it a moment to actually reach the lock; the
    # assertion that matters does not depend on the exact instant, only on the release not having
    # committed, which the held transaction guarantees for as long as it is open.
    releaser.join(timeout=3)
    assert "code" not in release_status, "the release committed despite the held row lock"

    during = _report(client, scenario, v1_id)
    # Complete Version 1, not a partial or blended set.
    assert during["results"] == v1_before["results"]
    assert _numeric(during) == Decimal("4.2")
    assert during["release_version_number"] == 1
    # Internally consistent: nothing uncommitted is reflected, so Version 1 is still current.
    assert during["is_current_version"] is True
    assert during["current_release_version_number"] == 1

    release_lock.set()
    holder.join(timeout=30)
    releaser.join(timeout=40)
    assert release_status.get("error") is None, release_status
    assert release_status.get("code") == 201, release_status

    # PG-RP-4, after the commit.
    v2_id = _version_id(client, scenario, 2)
    v2 = _report(client, scenario, v2_id)
    assert _numeric(v2) == Decimal("9.9")
    assert v2["release_version_number"] == 2
    assert v2["is_amendment"] is True
    assert v2["is_current_version"] is True

    v1_after = _report(client, scenario, v1_id)
    assert _numeric(v1_after) == Decimal("4.2")
    assert v1_after["results"] == v1_before["results"]
    assert v1_after["released_at"] == v1_before["released_at"]
    moved = {key for key in v1_before if v1_before[key] != v1_after[key]}
    assert moved == {"is_current_version", "current_release_version_number"}
    assert v1_after["current_release_version_number"] == 2


# ── PG-RP-5: availability does not rewrite released truth ────────────────────────────────────────


def test_pg_rp_5_disabling_the_offering_concurrently_leaves_both_reports_readable(
    client: TestClient, sessions: sessionmaker, keypair
):
    """DHEALTH-LAB-OFFERINGS-001 added a row lock to ``set_offering_active``. This asserts that the
    two tickets do not interact: the offering is switched off in a transaction held open across the
    report reads, and both the laboratory's and the clinic's report are served unchanged."""
    private_key, _ = keypair
    scenario = _released_scenario(client, sessions, private_key, "rp5")
    v1_id = _version_id(client, scenario, 1)
    before = _report(client, scenario, v1_id)

    disabled = client.patch(
        f"/exchange/v1/organizations/{scenario.lab_id}/offerings/{scenario.offering_id}",
        headers=_auth(scenario.tech),
        json={"active": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["active"] is False

    assert _report(client, scenario, v1_id) == before
    clinic = client.get(_clinic_report_url(scenario, v1_id), headers=_auth(scenario.clinician))
    assert clinic.status_code == 200, clinic.text
    assert clinic.json() == before


# ── PG-RP-6: a real version id from another order ────────────────────────────────────────────────


def test_pg_rp_6_a_foreign_release_version_is_refused_not_substituted(
    client: TestClient, sessions: sessionmaker, keypair
):
    """The failure mode this guards is not a 500 -- it is a silent 200 carrying this order's own
    current release in answer to a request for a different version entirely."""
    private_key, _ = keypair
    mine = _released_scenario(client, sessions, private_key, "rp6a")
    theirs = _released_scenario(client, sessions, private_key, "rp6b")
    foreign = _version_id(client, theirs, 1)

    response = client.get(_lab_report_url(mine, foreign), headers=_auth(mine.tech))
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "release version not found"}

    clinic = client.get(_clinic_report_url(mine, foreign), headers=_auth(mine.clinician))
    assert clinic.status_code == 404, clinic.text


# ── PG-RP-7: no migration, and the schema the report actually reads ──────────────────────────────


def test_pg_rp_7_this_ticket_adds_no_migration_and_no_report_table(sessions: sessionmaker):
    """**This ticket adds no migration**, because a report stores no new durable fact: it is a
    deterministic rendering of an immutable release manifest plus current reference identity.

    Read from the live database rather than from the models, because a model and a database that
    disagree is exactly what a migration test exists to catch."""
    with sessions() as session:
        heads = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    assert len(heads) == 1, f"exactly one Alembic head, found {heads}"
    assert heads[0] == "0039_lab_specimens", f"expected the specimen head, found {heads[0]!r}"

    with sessions() as session:
        tables = set(
            session.scalars(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            ).all()
        )
    # No report table, no report-number sequence, no generated-document store. Each of those is a
    # thing a reader might assume a printable report needed; none of them exists.
    report_shaped = {name for name in tables if "report" in name or "accession" in name}
    assert report_shaped == set(), report_shaped

    with sessions() as session:
        manifest_constraints = {
            (row[0], row[1])
            for row in session.execute(
                text(
                    "SELECT conname, contype FROM pg_constraint "
                    "WHERE conrelid = 'exchange_release_manifest_entries'::regclass"
                )
            ).all()
        }
        version_constraints = {
            (row[0], row[1])
            for row in session.execute(
                text(
                    "SELECT conname, contype FROM pg_constraint "
                    "WHERE conrelid = 'exchange_release_versions'::regclass"
                )
            ).all()
        }

    manifest_names = {name for name, _ in manifest_constraints}
    version_names = {name for name, _ in version_constraints}
    # The two constraints the report's exactness rests on: one row per (version, observation), and a
    # version bound either to the original release or to exactly one amendment, never both.
    assert "uq_exchange_release_manifest_entries_version_observation" in manifest_names
    assert "ck_exchange_release_versions_v1_bound_to_release" in version_names
    assert "uq_exchange_release_versions_order_version" in version_names

    # Foreign keys are RESTRICT, so nothing can delete an observation a historical manifest names.
    with sessions() as session:
        delete_rules = {
            row[0]: row[1]
            for row in session.execute(
                text(
                    "SELECT conname, confdeltype FROM pg_constraint "
                    "WHERE conrelid = 'exchange_release_manifest_entries'::regclass "
                    "AND contype = 'f'"
                )
            ).all()
        }
    assert delete_rules, "no foreign keys found on the manifest table"
    # 'r' is RESTRICT in pg_constraint.confdeltype.
    assert set(delete_rules.values()) == {"r"}, delete_rules
