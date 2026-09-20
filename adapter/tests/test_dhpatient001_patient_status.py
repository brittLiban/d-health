"""DH-PATIENT-001: the patient-visible result/follow-up status projection
(``PatientOrderResponse.result_status``) and the provider-side patient-access metadata read.

Same harness pattern as ``test_exchange_access_mode.py``/``test_exchange_results_api.py``: a bare
``FastAPI()`` app with ``build_exchange_router`` mounted directly and a ``StaticKeySource``-backed
``TokenValidator`` (local RSA keypair, zero network) for the provider/lab OIDC side, plus the
entirely separate opaque bearer path for the patient route.

The order journey below is deliberately the **provider-assigned** one (DH-PATIENT-001 §7: the
launch MVP has the clinic assign the laboratory, so the resulting grant is ``ORDER_READ``), not
DH-EX-005's patient-marketplace selection -- the marketplace backend is preserved and still tested
by its own files, but it is not the flow this ticket's patient surface exists for.

Every value that reaches the lab result rows here is a deliberately distinctive synthetic sentinel
(``87.6543``, ``SENTINELUNIT``, ``SENTINEL-TEXT-RESULT-DO-NOT-LEAK``, ...), so the no-leak
regression below can assert against the *serialized JSON string* rather than only against a field
list -- a nested field added carelessly in some future ticket fails that assertion even if nobody
remembers to extend a key-by-key comparison. No real patient, practitioner, or result data appears
anywhere in this file (AGENTS.md, DH-FIELD-001).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import (
    DoctorReview,
    LabOrder,
    LabResultReleaseVersion,
    PatientAccessGrant,
)
from daher_adapter.exchange.organizations import OrganizationRepository

TTL_HOURS = 168

# Distinctive synthetic sentinels -- see this module's docstring for why they are shaped this way.
SENTINEL_NUMERIC = "87.6543"
SENTINEL_UNIT = "SENTINELUNIT"
SENTINEL_RANGE_LOW = "11.1111"
SENTINEL_RANGE_HIGH = "99.9999"
SENTINEL_TEXT = "SENTINEL-TEXT-RESULT-DO-NOT-LEAK"
SENTINEL_AMENDED_NUMERIC = "22.2222"
SENTINEL_SOURCE_CODE = "SENTINELSOURCECODE"
SENTINEL_DISPLAY_LABEL = "Sentinel Amended Measure"


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


# -- harness helpers -----------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _new_key() -> str:
    return str(uuid.uuid4())


def _bootstrap_identity(client: TestClient, private_key, subject: str, **token_kwargs) -> str:
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
    return provider, make_token(private_key, subject=f"clinician-{name}")


def _configure_route(client, private_key, provider_id, lab_id) -> None:
    platform_admin_token = make_token(
        private_key, subject=f"route-admin-{uuid.uuid4()}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.put(
        f"/exchange/v1/organizations/{provider_id}/lab-routes/{lab_id}",
        headers=_auth(platform_admin_token),
        json={"active": True},
    )
    assert response.status_code in (200, 201), response.text


def _patient_access_url(provider_id, order_id) -> str:
    return f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/patient-access"


def _issue_access(client, token, provider_id, order_id):
    return client.post(_patient_access_url(provider_id, order_id), headers=_auth(token))


def _patient_order(client, access_token):
    return client.get("/exchange/v1/patient/order", headers=_auth(access_token))


def _expire_grant(sessions: sessionmaker[Session], order_id: uuid.UUID) -> None:
    """Age the order's live grant into the past. ``issued_at`` moves too, not only ``expires_at``:
    ``ck_exchange_patient_access_grants_expiry_after_issue`` (``expires_at > issued_at``) is a real
    database constraint, so a grant cannot be made to look expired by moving its expiry alone --
    which is itself worth knowing, since it means no production path can produce an
    already-expired-at-issuance row either."""
    with sessions.begin() as session:
        grant = session.scalar(
            select(PatientAccessGrant).where(
                PatientAccessGrant.lab_order_id == order_id,
                PatientAccessGrant.revoked_at.is_(None),
            )
        )
        assert grant is not None
        now = datetime.now(UTC)
        grant.issued_at = now - timedelta(hours=2)
        grant.expires_at = now - timedelta(hours=1)


def _order_item_ids(sessions: sessionmaker[Session], order_id: uuid.UUID) -> list[uuid.UUID]:
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        return [item.id for item in order.items]


def _create_assigned_order(
    client,
    private_key,
    organizations,
    catalog,
    directory,
    patients,
    *,
    name: str,
    test_count: int = 1,
):
    """Provider-assigned order, taken as far as ``SELECTED``. Returns
    ``(provider, clinician_token, lab, order_id)``."""
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_record = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Synthetic Patient {name}",
    )
    entries = []
    for index in range(test_count):
        entry, _ = catalog.create_entry(
            code=f"DHEALTH-PAT-{name}-{index}",
            display_name=f"Analyse de démonstration {name}-{index}",
        )
        entries.append(entry)

    lab = make_lab(organizations, name=f"PatLab-{name}")
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Avenue",
        latitude=None,
        longitude=None,
        public_phone="+25321000000",
        listed=False,
        accepting_orders=True,
    )
    for entry in entries:
        catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    _configure_route(client, private_key, provider.id, lab.id)

    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_record.id),
            "catalog_entry_ids": [str(entry.id) for entry in entries],
        },
    )
    assert create_response.status_code == 201, create_response.text
    order_id = uuid.UUID(create_response.json()["id"])

    assign_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/assign-lab",
        headers=_auth(clinician_token),
        json={"lab_organization_id": str(lab.id)},
    )
    assert assign_response.status_code == 201, assign_response.text
    return provider, clinician_token, lab, order_id


def _lab_tech_token(client, private_key, lab_id, name: str) -> str:
    identity_id = _bootstrap_identity(client, private_key, f"labtech-{name}")
    _grant_roles(client, private_key, lab_id, identity_id, f"labtech-{name}", ["LAB_TECH"])
    return make_token(private_key, subject=f"labtech-{name}")


def _take_to_in_progress(client, private_key, lab_id, order_id, name: str) -> str:
    tech_token = _lab_tech_token(client, private_key, lab_id, name)
    base = f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}"
    assert client.post(f"{base}/accept", headers=_auth(tech_token)).status_code == 200
    assert client.post(f"{base}/record-collection", headers=_auth(tech_token)).status_code == 201
    assert client.post(f"{base}/start-processing", headers=_auth(tech_token)).status_code == 200
    return tech_token


def _submit_numeric(client, tech_token, lab_id, order_id, item_id) -> str:
    response = client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "numeric_value": SENTINEL_NUMERIC,
            "numeric_unit": SENTINEL_UNIT,
            "reference_range_low": SENTINEL_RANGE_LOW,
            "reference_range_high": SENTINEL_RANGE_HIGH,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["observation_id"]


def _submit_text(client, tech_token, lab_id, order_id, item_id) -> str:
    response = client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(tech_token),
        json={"result_type": "TEXT", "text_value": SENTINEL_TEXT},
    )
    assert response.status_code == 201, response.text
    return response.json()["observation_id"]


def _release_v1(client, private_key, lab_id, order_id, name: str) -> dict:
    releaser_identity_id = _bootstrap_identity(client, private_key, f"releaser-{name}")
    _grant_roles(
        client,
        private_key,
        lab_id,
        releaser_identity_id,
        f"releaser-{name}",
        ["LAB_RESULT_RELEASER"],
    )
    releaser_token = make_token(private_key, subject=f"releaser-{name}")
    inspection = client.get(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/results",
        headers=_auth(releaser_token),
    )
    assert inspection.status_code == 200, inspection.text
    response = client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": inspection.json()["result_fingerprint"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _release_amendment_v2(
    client, private_key, lab_id, order_id, item_id, observation_id, tech_token, name: str
) -> dict:
    """Start an amendment, correct the released observation, release it -- producing version 2 and,
    with it, a brand-new ``PENDING`` ``DoctorReview`` bound to that version."""
    amendments_url = f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/amendments"
    started = client.post(amendments_url, headers=_auth(tech_token))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]

    corrected = client.post(
        f"{amendments_url}/{amendment_id}/items/{item_id}/observations/{observation_id}/correct",
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": SENTINEL_SOURCE_CODE,
            "display_label": SENTINEL_DISPLAY_LABEL,
            "numeric_value": SENTINEL_AMENDED_NUMERIC,
            "numeric_unit": SENTINEL_UNIT,
        },
    )
    assert corrected.status_code == 201, corrected.text

    releaser_identity_id = _bootstrap_identity(client, private_key, f"amend-releaser-{name}")
    _grant_roles(
        client,
        private_key,
        lab_id,
        releaser_identity_id,
        f"amend-releaser-{name}",
        ["LAB_RESULT_RELEASER"],
    )
    releaser_token = make_token(private_key, subject=f"amend-releaser-{name}")
    inspection = client.get(f"{amendments_url}/{amendment_id}", headers=_auth(releaser_token))
    assert inspection.status_code == 200, inspection.text
    released = client.post(
        f"{amendments_url}/{amendment_id}/release",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": inspection.json()["result_fingerprint"]},
    )
    assert released.status_code == 201, released.text
    return released.json()


def _review_latest_version(client, clinician_token, provider_id, order_id, sessions, *, follow_up):
    """Record a terminal disposition against the order's *latest* release version explicitly --
    the version-targeted route, never the legacy order-only one (which refuses once more than one
    version exists)."""
    with sessions() as session:
        version_id = session.scalar(
            select(LabResultReleaseVersion.id)
            .where(LabResultReleaseVersion.lab_order_id == order_id)
            .order_by(LabResultReleaseVersion.version_number.desc())
            .limit(1)
        )
    assert version_id is not None
    disposition = "follow-up-required" if follow_up else "no-follow-up-required"
    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/orders/{order_id}"
        f"/release-versions/{version_id}/review/{disposition}",
        headers=_auth(clinician_token),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _released_and_reviewed_order(
    client, private_key, organizations, catalog, directory, patients, sessions, name: str
):
    """The full journey to "version 1 released, review still PENDING". Returns
    ``(provider, clinician_token, lab, order_id, item_id, observation_id, tech_token)``."""
    provider, clinician_token, lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name=name
    )
    tech_token = _take_to_in_progress(client, private_key, lab.id, order_id, name)
    item_id = _order_item_ids(sessions, order_id)[0]
    observation_id = _submit_numeric(client, tech_token, lab.id, order_id, item_id)
    _release_v1(client, private_key, lab.id, order_id, name)
    return provider, clinician_token, lab, order_id, item_id, observation_id, tech_token


# ==================================================================================================
# PS-group: the patient result-status projection (§26-§29, §59)
# ==================================================================================================


def test_ps1_unreleased_order_is_not_ready(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """PS1: no released version and not a grandfathered COMPLETED order -> ``NOT_READY``."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PS1"
    )
    access = _issue_access(client, clinician_token, provider.id, order_id)
    assert access.status_code == 201, access.text
    assert access.json()["access_mode"] == "ORDER_READ"

    body = _patient_order(client, access.json()["access_token"]).json()
    assert body["status"] == "SELECTED"
    assert body["result_status"] == "NOT_READY"


def test_ps2_released_with_pending_review_is_awaiting_clinician_review(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PS2: version 1 released, its review obligation still ``PENDING``."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id, _item, _obs, _tech = _released_and_reviewed_order(
        client, private_key, organizations, catalog, directory, patients, sessions, "PS2"
    )
    access = _issue_access(client, clinician_token, provider.id, order_id)
    body = _patient_order(client, access.json()["access_token"]).json()
    assert body["status"] == "COMPLETED"
    assert body["result_status"] == "AWAITING_CLINICIAN_REVIEW"


def test_ps3_latest_review_no_follow_up_required_projects_verbatim(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PS3: the clinician's recorded ``NO_FOLLOW_UP_REQUIRED`` reaches the patient unchanged."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id, _item, _obs, _tech = _released_and_reviewed_order(
        client, private_key, organizations, catalog, directory, patients, sessions, "PS3"
    )
    _review_latest_version(
        client, clinician_token, provider.id, order_id, sessions, follow_up=False
    )
    access = _issue_access(client, clinician_token, provider.id, order_id)
    body = _patient_order(client, access.json()["access_token"]).json()
    assert body["result_status"] == "NO_FOLLOW_UP_REQUIRED"


def test_ps4_latest_review_follow_up_required_projects_verbatim(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PS4: same, for ``FOLLOW_UP_REQUIRED``."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id, _item, _obs, _tech = _released_and_reviewed_order(
        client, private_key, organizations, catalog, directory, patients, sessions, "PS4"
    )
    _review_latest_version(client, clinician_token, provider.id, order_id, sessions, follow_up=True)
    access = _issue_access(client, clinician_token, provider.id, order_id)
    body = _patient_order(client, access.json()["access_token"]).json()
    assert body["result_status"] == "FOLLOW_UP_REQUIRED"


def test_ps5_amendment_release_reverts_patient_to_awaiting_review(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PS5/§28/§66 -- the load-bearing amendment regression.

    Version 1 is reviewed terminally (``NO_FOLLOW_UP_REQUIRED``), then a version-2 amendment is
    released. The patient must stop seeing version 1's disposition: that judgement was made about
    content version 2 has superseded, and nobody has yet reviewed what replaced it."""
    private_key, _ = keypair
    provider, clinician_token, lab, order_id, item_id, observation_id, tech_token = (
        _released_and_reviewed_order(
            client, private_key, organizations, catalog, directory, patients, sessions, "PS5"
        )
    )
    _review_latest_version(
        client, clinician_token, provider.id, order_id, sessions, follow_up=False
    )
    access_token = _issue_access(client, clinician_token, provider.id, order_id).json()[
        "access_token"
    ]
    assert _patient_order(client, access_token).json()["result_status"] == "NO_FOLLOW_UP_REQUIRED"

    released_v2 = _release_amendment_v2(
        client, private_key, lab.id, order_id, item_id, observation_id, tech_token, "PS5"
    )
    assert released_v2["version_number"] == 2

    # The *same* already-issued patient link now truthfully reports the new obligation -- no
    # re-issuance, no patient action, and emphatically not version 1's stale disposition.
    assert _patient_order(client, access_token).json()["result_status"] == (
        "AWAITING_CLINICIAN_REVIEW"
    )


def test_ps6_after_v2_review_patient_sees_v2_disposition(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PS6: once version 2 is itself reviewed, the patient sees *that* disposition -- and it is
    genuinely version 2's, not version 1's carried forward (they differ here on purpose)."""
    private_key, _ = keypair
    provider, clinician_token, lab, order_id, item_id, observation_id, tech_token = (
        _released_and_reviewed_order(
            client, private_key, organizations, catalog, directory, patients, sessions, "PS6"
        )
    )
    _review_latest_version(
        client, clinician_token, provider.id, order_id, sessions, follow_up=False
    )
    _release_amendment_v2(
        client, private_key, lab.id, order_id, item_id, observation_id, tech_token, "PS6"
    )
    _review_latest_version(client, clinician_token, provider.id, order_id, sessions, follow_up=True)

    access = _issue_access(client, clinician_token, provider.id, order_id)
    assert _patient_order(client, access.json()["access_token"]).json()["result_status"] == (
        "FOLLOW_UP_REQUIRED"
    )

    # Version 1's own review row is untouched and still terminal -- the projection reads the latest
    # version, it does not rewrite history.
    with sessions() as session:
        statuses = {
            version.version_number: session.scalar(
                select(DoctorReview.status).where(DoctorReview.release_version_id == version.id)
            )
            for version in session.scalars(
                select(LabResultReleaseVersion).where(
                    LabResultReleaseVersion.lab_order_id == order_id
                )
            ).all()
        }
    assert statuses == {1: "NO_FOLLOW_UP_REQUIRED", 2: "FOLLOW_UP_REQUIRED"}


def test_ps5_negative_control_amendment_merely_prepared_keeps_v1_disposition(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Adjacent case PS5 alone would not catch: an amendment *in preparation* has released no
    version, so version 1's real disposition is still the truthful answer. A projection that
    reacted to amendment episodes rather than released versions would wrongly flip here."""
    private_key, _ = keypair
    provider, clinician_token, lab, order_id, _item, _obs, tech_token = (
        _released_and_reviewed_order(
            client, private_key, organizations, catalog, directory, patients, sessions, "PS5N"
        )
    )
    _review_latest_version(
        client, clinician_token, provider.id, order_id, sessions, follow_up=False
    )
    started = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/amendments",
        headers=_auth(tech_token),
    )
    assert started.status_code == 201, started.text

    access = _issue_access(client, clinician_token, provider.id, order_id)
    assert _patient_order(client, access.json()["access_token"]).json()["result_status"] == (
        "NO_FOLLOW_UP_REQUIRED"
    )


def test_ps_legacy_completed_order_without_release_uses_its_unbound_review(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """§29: a grandfathered ``COMPLETED``-without-``LabResultRelease`` order (ADR-044's rule) has
    no release version, and none is fabricated. Its one honest unbound ``DoctorReview`` row is
    used verbatim -- ``PENDING`` here, which is the truthful "nobody has recorded a disposition".

    The COMPLETED status is forced directly in the database precisely because no supported API path
    can produce this shape any more (release is the only route to COMPLETED, and it always creates
    version 1) -- which is exactly what makes it the grandfathered shape worth testing."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PSLEG"
    )
    with sessions.begin() as session:
        session.get(LabOrder, order_id).status = "COMPLETED"

    access = _issue_access(client, clinician_token, provider.id, order_id)
    body = _patient_order(client, access.json()["access_token"]).json()
    assert body["status"] == "COMPLETED"
    assert body["result_status"] == "AWAITING_CLINICIAN_REVIEW"

    with sessions() as session:
        review = session.scalar(select(DoctorReview).where(DoctorReview.lab_order_id == order_id))
    assert review is not None
    assert review.release_version_id is None, (
        "the grandfathered order must keep an unbound review row -- no release version may be "
        "fabricated for it"
    )

    # And the same legacy row's own terminal disposition, once recorded through the legacy
    # order-only review route, reaches the patient verbatim rather than being ignored.
    legacy_review = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert legacy_review.status_code == 200, legacy_review.text
    assert _patient_order(client, access.json()["access_token"]).json()["result_status"] == (
        "NO_FOLLOW_UP_REQUIRED"
    )


def test_ps7_ps8_patient_response_serializes_no_result_value_or_reviewer_identity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PS7/PS8/§30/§68: an order carrying a numeric result (value, unit, both reference-range
    bounds) *and* a text result, released and terminally reviewed, must serialize a patient
    response in which none of that content -- and no reviewer/releaser identity -- appears
    anywhere in the raw JSON."""
    private_key, _ = keypair
    provider, clinician_token, lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PS7", test_count=2
    )
    tech_token = _take_to_in_progress(client, private_key, lab.id, order_id, "PS7")
    numeric_item, text_item = _order_item_ids(sessions, order_id)
    _submit_numeric(client, tech_token, lab.id, order_id, numeric_item)
    _submit_text(client, tech_token, lab.id, order_id, text_item)
    release_body = _release_v1(client, private_key, lab.id, order_id, "PS7")
    review_body = _review_latest_version(
        client, clinician_token, provider.id, order_id, sessions, follow_up=True
    )

    access = _issue_access(client, clinician_token, provider.id, order_id)
    response = _patient_order(client, access.json()["access_token"])
    assert response.status_code == 200, response.text
    raw = response.text

    # The provider side genuinely holds this content, so these are real values being withheld,
    # not values that never existed.
    provider_results = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert provider_results.status_code == 200, provider_results.text
    assert SENTINEL_TEXT in provider_results.text
    assert SENTINEL_UNIT in provider_results.text

    for forbidden in (
        SENTINEL_NUMERIC,
        SENTINEL_UNIT,
        SENTINEL_RANGE_LOW,
        SENTINEL_RANGE_HIGH,
        SENTINEL_TEXT,
    ):
        assert forbidden not in raw, f"patient response leaked result content: {forbidden}"

    # PS8/§31: no reviewer or releaser provenance of any kind.
    for forbidden_identity in (
        str(review_body["reviewed_by_practitioner_reference_id"]),
        str(release_body["released_by_identity_id"]),
    ):
        assert forbidden_identity not in raw

    # Structural belt-and-braces: none of the field names §30 names may exist either.
    for forbidden_field in (
        "numeric_value",
        "text_value",
        "reference_range",
        "source_code",
        "observation",
        "manifest",
        "diagnosis",
        "reviewed_by",
    ):
        assert forbidden_field not in raw, f"patient response exposes a {forbidden_field!r} field"

    body = response.json()
    assert set(body.keys()) == {
        "status",
        "requested_tests",
        "ordering_provider_name",
        "selection",
        "result_status",
    }
    assert body["result_status"] == "FOLLOW_UP_REQUIRED"


def test_ps9_invalid_expired_and_revoked_tokens_are_indistinguishable(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PS9/§25: every patient-credential failure mode collapses to one identical 401 -- an
    unauthenticated caller can never tell "never existed" from "expired" from "revoked"."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PS9"
    )
    revoked_token = _issue_access(client, clinician_token, provider.id, order_id).json()[
        "access_token"
    ]
    assert _patient_order(client, revoked_token).status_code == 200
    assert (
        client.delete(
            _patient_access_url(provider.id, order_id), headers=_auth(clinician_token)
        ).status_code
        == 204
    )

    expired_token = _issue_access(client, clinician_token, provider.id, order_id).json()[
        "access_token"
    ]
    _expire_grant(sessions, order_id)

    responses = [
        _patient_order(client, revoked_token),
        _patient_order(client, expired_token),
        _patient_order(client, "totally-unknown-token-value"),
        client.get("/exchange/v1/patient/order"),
    ]
    assert [r.status_code for r in responses] == [401, 401, 401, 401]
    assert len({r.text for r in responses}) == 1, (
        f"at least one failure mode returns a distinguishable body: {[r.text for r in responses]}"
    )


# ==================================================================================================
# PM-group: provider-side patient-access metadata read (§8-§10, §46, §60)
# ==================================================================================================


def _get_metadata(client, token, provider_id, order_id):
    return client.get(_patient_access_url(provider_id, order_id), headers=_auth(token))


def test_pm1_no_active_grant_reports_inactive(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """PM1: an order that has never had a grant issued."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PM1"
    )
    response = _get_metadata(client, clinician_token, provider.id, order_id)
    assert response.status_code == 200, response.text
    assert response.json() == {"active": False, "expires_at": None, "access_mode": None}
    assert response.headers["cache-control"] == "no-store"


def test_pm2_live_grant_reports_active_with_expiry_and_mode(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """PM2: a live grant's expiry and mode are exactly the issuance response's own."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PM2"
    )
    issued = _issue_access(client, clinician_token, provider.id, order_id).json()

    body = _get_metadata(client, clinician_token, provider.id, order_id).json()
    assert body["active"] is True
    assert body["access_mode"] == "ORDER_READ" == issued["access_mode"]
    assert datetime.fromisoformat(body["expires_at"]) == datetime.fromisoformat(
        issued["expires_at"]
    )


def test_pm3_expired_grant_reports_inactive_without_mutating_it(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PM3/§9/§46: an expired grant is inactive, its expiry instant is not disclosed, and merely
    reading it must not set ``revoked_at`` -- the read is a GET, not a retirement."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PM3"
    )
    _issue_access(client, clinician_token, provider.id, order_id)
    _expire_grant(sessions, order_id)

    assert _get_metadata(client, clinician_token, provider.id, order_id).json() == {
        "active": False,
        "expires_at": None,
        "access_mode": None,
    }
    with sessions() as session:
        grant = session.scalar(
            select(PatientAccessGrant).where(PatientAccessGrant.lab_order_id == order_id)
        )
    assert grant.revoked_at is None, "reading metadata must not retire the grant it reports on"


def test_pm3b_revoked_grant_reports_inactive(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """Adjacent to PM3: revoked, not expired, is equally inactive."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PM3B"
    )
    _issue_access(client, clinician_token, provider.id, order_id)
    assert (
        client.delete(
            _patient_access_url(provider.id, order_id), headers=_auth(clinician_token)
        ).status_code
        == 204
    )
    assert _get_metadata(client, clinician_token, provider.id, order_id).json()["active"] is False


def test_pm4_wrong_provider_cannot_inspect_and_is_non_enumerating(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """PM4: another provider's clinician gets the identical 404 a nonexistent order id gets."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PM4"
    )
    _issue_access(client, clinician_token, provider.id, order_id)
    other_provider, other_token = _setup_clinician(client, private_key, organizations, "PM4-Other")

    real_order = _get_metadata(client, other_token, other_provider.id, order_id)
    invented_order = _get_metadata(client, other_token, other_provider.id, uuid.uuid4())
    assert real_order.status_code == 404, real_order.text
    assert invented_order.status_code == 404
    assert real_order.json() == invented_order.json()


def test_pm5_lab_role_cannot_inspect_provider_patient_access_state(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """PM5: the lab performing the order is not a ``CLINICIAN`` for the ordering provider, so it
    cannot read that provider's patient-access state at all.

    The refusal is a **404, not a 403**, and that is deliberate pre-existing behavior this route
    inherits verbatim from ``_require_clinician``/``AuthorizationService.require_clinician``: a
    caller with *zero* relationship to an organization must not be able to distinguish "that
    provider exists and I am not its clinician" from "no such provider" (anti-enumeration). The
    assertion below pins that indistinguishability rather than just the status code."""
    private_key, _ = keypair
    provider, clinician_token, lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PM5"
    )
    _issue_access(client, clinician_token, provider.id, order_id)
    tech_token = _lab_tech_token(client, private_key, lab.id, "PM5")

    refused = _get_metadata(client, tech_token, provider.id, order_id)
    invented_org = _get_metadata(client, tech_token, uuid.uuid4(), order_id)
    assert refused.status_code == 404, refused.text
    assert invented_org.status_code == 404
    assert refused.json() == invented_org.json()

    # ...and the lab cannot reach the order under its *own* organization id either -- the route is
    # provider-scoped, and the lab is not the ordering provider.
    assert _get_metadata(client, tech_token, lab.id, order_id).status_code in (403, 404)


def test_pm5b_platform_admin_alone_cannot_inspect(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """Adjacent to PM5: patient access is clinical order access -- ``_require_clinician``
    deliberately does not admit a platform admin who holds no CLINICIAN role here."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PM5B"
    )
    _issue_access(client, clinician_token, provider.id, order_id)
    admin_token = make_token(
        private_key, subject="pm5b-platform-admin", roles=[PLATFORM_ADMIN_ROLE]
    )
    assert _get_metadata(client, admin_token, provider.id, order_id).status_code == 403


def test_pm6_metadata_never_returns_raw_secret_or_hash(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """PM6/§10: neither the raw bearer token nor its persisted digest nor the grant's UUID appears
    in the metadata response -- the raw secret remains unrecoverable after issuance, forever."""
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _create_assigned_order(
        client, private_key, organizations, catalog, directory, patients, name="PM6"
    )
    raw_token = _issue_access(client, clinician_token, provider.id, order_id).json()["access_token"]
    with sessions() as session:
        grant = session.scalar(
            select(PatientAccessGrant).where(PatientAccessGrant.lab_order_id == order_id)
        )
        token_hash = grant.token_hash
        grant_id = str(grant.id)

    response = _get_metadata(client, clinician_token, provider.id, order_id)
    assert set(response.json().keys()) == {"active", "expires_at", "access_mode"}
    for forbidden in (raw_token, token_hash, grant_id):
        assert forbidden not in response.text
