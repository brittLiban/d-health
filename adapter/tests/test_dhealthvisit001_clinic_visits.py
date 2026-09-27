"""DHEALTH-CLINIC-VISIT-001: the native clinic visit, end to end over HTTP.

Driven through ``TestClient`` against ``build_exchange_router`` rather than against
``VisitRepository`` directly, for the reason DHEALTH-NATIVE-ACCESS-001's suite states and this
ticket inherits: the claim being earned is *"a clinician can run a consultation in DHealth"*, and a
test that called the service would prove the domain logic while proving nothing about whether a
clinician can reach it. Service-level edges that cannot be produced over HTTP, and the database
constraints that no API call can violate, get their own tests at the bottom.

**Four states, not five.** ``CHECKED_IN`` -> ``IN_PROGRESS`` -> ``COMPLETED``, plus ``CANCELLED``
from ``CHECKED_IN`` only. ``PLANNED``/``NO_SHOW`` are absent because no calendar exists to reach
them -- see ``VisitStatus`` for the full argument and this ticket's report for the recorded
deviation from its own §6 wording.

The real-browser version of the same journey is ``ui/e2e/native/08-clinic-visit.spec.ts``; the
real-PostgreSQL concurrency version is
``tests/integration/test_dhealthvisit001_postgres_qualification.py``. This file is the fast,
exhaustive one, and every row-locking property it *cannot* observe (SQLite makes
``with_for_update`` a no-op) is asserted there instead of being claimed here.

All patient data is synthetic. No real patient data is used anywhere in this file.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import ForeignKeyConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    LabOrder,
    LabOrderIntent,
    Visit,
    VisitNote,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.visits import LEGAL_TRANSITIONS, TERMINAL_STATUSES, VisitStatus


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
def catalog(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> CatalogRepository:
    return CatalogRepository(sessions, organizations)


# -- harness --------------------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _key() -> str:
    """A fresh canonical lowercase UUIDv4, which is what `Idempotency-Key` demands literally."""
    return str(uuid.uuid4())


def _bootstrap(client: TestClient, private_key, subject: str, **kwargs) -> tuple[str, str]:
    token = make_token(private_key, subject=subject, **kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"], token


def _attach(
    client: TestClient, admin_token: str, organization_id: str, identity_id: str, roles: list[str]
) -> str:
    response = client.post(
        f"/exchange/v1/organizations/{organization_id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["membership_id"]


def _db_audit(sessions: sessionmaker[Session]) -> list[ExchangeSecurityAuditEvent]:
    with sessions() as session:
        return list(
            session.scalars(
                select(ExchangeSecurityAuditEvent).order_by(ExchangeSecurityAuditEvent.created_at)
            ).all()
        )


def _audit_types(sessions: sessionmaker[Session]) -> list[str]:
    return [event.event_type for event in _db_audit(sessions)]


def _file_audit(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line
    ]


@pytest.fixture
def world(client: TestClient, keypair, organizations: OrganizationRepository):
    """Two provider organizations and one laboratory, with the personas every refusal needs.

    - Clinique A (PROVIDER): ``admin_a`` (ORG_ADMIN only), ``clinician`` and ``covering``
      (CLINICIAN)
    - Clinique Z (PROVIDER): ``clinician_z`` (CLINICIAN) -- a real clinician, of the wrong
      organization, which is what makes the cross-tenant assertions real rather than
      self-referential
    - Laboratoire B (LAB): ``tech`` (LAB_TECH)
    - ``outsider``: authenticated, member of nothing
    - ``platform_token``: the Keycloak realm role, which must **not** open a clinical door

    ``admin_a`` holding ORG_ADMIN *only* is deliberate: it is the persona that proves visits are
    gated on CLINICIAN specifically and not merely on "belongs to this organization".
    """
    private_key, _ = keypair
    platform_token = make_token(private_key, subject="platform", roles=[PLATFORM_ADMIN_ROLE])

    provider = make_provider(organizations, "Clinique A")
    other_provider = make_provider(organizations, "Clinique Z")
    lab = make_lab(organizations, "Laboratoire B")

    admin_a_id, admin_a_token = _bootstrap(client, private_key, "admin-a", name="Admin A")
    admin_z_id, admin_z_token = _bootstrap(client, private_key, "admin-z", name="Admin Z")
    admin_b_id, admin_b_token = _bootstrap(client, private_key, "admin-b", name="Admin B")
    clinician_id, clinician_token = _bootstrap(client, private_key, "clin", name="Clinicienne C")
    covering_id, covering_token = _bootstrap(client, private_key, "cover", name="Docteur Couvrant")
    clinician_z_id, clinician_z_token = _bootstrap(client, private_key, "clin-z", name="Docteur Z")
    tech_id, tech_token = _bootstrap(client, private_key, "tech", name="Technicien L")
    _outsider_id, outsider_token = _bootstrap(client, private_key, "out", name="Dehors")

    _attach(client, platform_token, str(provider.id), admin_a_id, ["ORG_ADMIN"])
    _attach(client, platform_token, str(other_provider.id), admin_z_id, ["ORG_ADMIN"])
    _attach(client, platform_token, str(lab.id), admin_b_id, ["ORG_ADMIN"])

    clinician_membership = _attach(
        client, admin_a_token, str(provider.id), clinician_id, ["CLINICIAN"]
    )
    _attach(client, admin_a_token, str(provider.id), covering_id, ["CLINICIAN"])
    _attach(client, admin_z_token, str(other_provider.id), clinician_z_id, ["CLINICIAN"])
    _attach(client, admin_b_token, str(lab.id), tech_id, ["LAB_TECH"])

    return {
        "provider_id": str(provider.id),
        "other_provider_id": str(other_provider.id),
        "lab_id": str(lab.id),
        "admin_a_token": admin_a_token,
        "admin_z_token": admin_z_token,
        "clinician_token": clinician_token,
        "clinician_id": clinician_id,
        "clinician_membership": clinician_membership,
        "covering_token": covering_token,
        "covering_id": covering_id,
        "clinician_z_token": clinician_z_token,
        "tech_token": tech_token,
        "outsider_token": outsider_token,
        "platform_token": platform_token,
    }


def _make_patient(
    client: TestClient,
    provider_id: str,
    token: str,
    *,
    display_name: str = "Patiente Synthétique",
    date_of_birth: str | None = "1990-04-17",
) -> dict:
    """A synthetic native Patient. Returns the directory entry, which carries both the ``id`` a
    visit needs and the ``primary_patient_reference_id`` an order needs."""
    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/patients",
        headers={**_auth(token), "Idempotency-Key": _key()},
        json={"display_name": display_name, "date_of_birth": date_of_birth},
    )
    assert response.status_code in (200, 201), response.text
    entry = response.json()
    # The directory response names it `patient_id`; every visit call site here wants `id`.
    return {**entry, "id": entry["patient_id"]}


def _start_visit(
    client: TestClient,
    provider_id: str,
    token: str,
    patient_id: str,
    *,
    reason: str = "Fièvre depuis deux jours",
    key: str | None = None,
) -> tuple[int, dict]:
    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits",
        headers={**_auth(token), "Idempotency-Key": key or _key()},
        json={"patient_id": patient_id, "reason_for_visit": reason},
    )
    return response.status_code, response.json()


def _visit_in_progress(client: TestClient, world, patient_id: str) -> str:
    """The common starting point: a visit this organization's clinician has taken."""
    provider_id = world["provider_id"]
    code, visit = _start_visit(client, provider_id, world["clinician_token"], patient_id)
    assert code == 201, visit
    taken = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/take",
        headers=_auth(world["clinician_token"]),
    )
    assert taken.status_code == 200, taken.text
    return visit["id"]


# == the state machine ============================================================================


def test_vis1_start_creates_a_checked_in_visit_with_no_clinician(client, world):
    """The waiting list *is* ``CHECKED_IN``, so a new visit has no clinician and no start time."""
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    code, visit = _start_visit(
        client, world["provider_id"], world["clinician_token"], patient["id"]
    )

    assert code == 201, visit
    assert visit["status"] == VisitStatus.CHECKED_IN.value
    assert visit["clinician_identity_id"] is None
    assert visit["started_at"] is None
    assert visit["completed_at"] is None
    assert visit["cancelled_at"] is None
    assert visit["reason_for_visit"] == "Fièvre depuis deux jours"
    assert visit["patient_id"] == patient["id"]
    # The worklist row must be able to identify a human without a second request.
    assert visit["patient_display_name"] == "Patiente Synthétique"
    assert visit["patient_date_of_birth"] == "1990-04-17"


def test_vis2_start_is_idempotent_on_the_header_key(client, world):
    """A retried or double-clicked check-in returns the same visit with 200, never a second one."""
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    key = _key()
    first_code, first = _start_visit(
        client, world["provider_id"], world["clinician_token"], patient["id"], key=key
    )
    second_code, second = _start_visit(
        client, world["provider_id"], world["clinician_token"], patient["id"], key=key
    )

    assert (first_code, second_code) == (201, 200)
    assert first["id"] == second["id"]

    listed = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers=_auth(world["clinician_token"]),
    )
    assert len([row for row in listed.json() if row["patient_id"] == patient["id"]]) == 1


def test_vis3_reused_key_for_a_different_patient_is_refused(client, world):
    """Returning the *other* patient's visit is the cross-wiring this guard exists to prevent."""
    provider_id = world["provider_id"]
    one = _make_patient(client, provider_id, world["clinician_token"], display_name="Patient Un")
    two = _make_patient(client, provider_id, world["clinician_token"], display_name="Patient Deux")
    key = _key()

    assert _start_visit(client, provider_id, world["clinician_token"], one["id"], key=key)[0] == 201
    code, body = _start_visit(client, provider_id, world["clinician_token"], two["id"], key=key)

    assert code == 409
    assert "different" in body["detail"]


def test_vis4_reused_key_with_a_different_reason_is_refused(client, world):
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    key = _key()
    _start_visit(
        client,
        world["provider_id"],
        world["clinician_token"],
        patient["id"],
        key=key,
        reason="Toux",
    )
    code, _body = _start_visit(
        client,
        world["provider_id"],
        world["clinician_token"],
        patient["id"],
        key=key,
        reason="Douleur abdominale",
    )
    assert code == 409


def test_vis5_take_records_the_acting_clinician_and_the_start_time(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    _code, visit = _start_visit(client, provider_id, world["clinician_token"], patient["id"])

    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/take",
        headers=_auth(world["clinician_token"]),
    )

    assert response.status_code == 200, response.text
    taken = response.json()
    assert taken["status"] == VisitStatus.IN_PROGRESS.value
    assert taken["clinician_identity_id"] == world["clinician_id"]
    assert taken["started_at"] is not None
    assert taken["completed_at"] is None


def test_vis6_retaking_your_own_visit_is_an_idempotent_success(client, world, sessions):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    again = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/take",
        headers=_auth(world["clinician_token"]),
    )

    assert again.status_code == 200, again.text
    assert again.json()["clinician_identity_id"] == world["clinician_id"]
    # No *second* `visit_started` row: the replay committed nothing, and this table's contract is
    # that a row here accompanies a real change.
    assert _audit_types(sessions).count("visit_started") == 1


def test_vis7_taking_another_clinicians_visit_is_refused_without_rewriting_the_record(
    client, world
):
    """A state conflict, not an authority refusal -- and the clinician of record is unchanged."""
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    stolen = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/take",
        headers=_auth(world["covering_token"]),
    )

    assert stolen.status_code == 409, stolen.text
    after = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}",
        headers=_auth(world["clinician_token"]),
    )
    assert after.json()["visit"]["clinician_identity_id"] == world["clinician_id"]


def test_vis8_complete_is_terminal_and_stamps_completed_at(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    done = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(world["clinician_token"]),
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == VisitStatus.COMPLETED.value
    assert done.json()["completed_at"] is not None

    twice = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(world["clinician_token"]),
    )
    assert twice.status_code == 409


def test_vis9_a_checked_in_visit_cannot_be_completed(client, world):
    """``COMPLETED`` is reachable only through ``IN_PROGRESS``: nobody completes a consultation that
    never started."""
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    _code, visit = _start_visit(client, provider_id, world["clinician_token"], patient["id"])

    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/complete",
        headers=_auth(world["clinician_token"]),
    )
    assert response.status_code == 409
    assert "CHECKED_IN" in response.json()["detail"]


def test_vis10_a_covering_clinician_may_complete_someone_elses_visit(client, world):
    """Authority is organization-scoped, not per-row. A clinic with covering clinicians must work;
    only *rewriting who saw the patient* is refused (VIS-7)."""
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    done = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(world["covering_token"]),
    )

    assert done.status_code == 200, done.text
    # The clinician of record is still the one who actually took the visit.
    assert done.json()["clinician_identity_id"] == world["clinician_id"]


def test_vis11_cancel_works_from_checked_in(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    _code, visit = _start_visit(client, provider_id, world["clinician_token"], patient["id"])

    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/cancel",
        headers=_auth(world["clinician_token"]),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == VisitStatus.CANCELLED.value
    assert body["cancelled_at"] is not None
    # Never started, so it must not claim to have been.
    assert body["started_at"] is None
    assert body["clinician_identity_id"] is None


def test_vis12_an_in_progress_visit_can_never_be_cancelled(client, world):
    """§12, verbatim: "Never from ``IN_PROGRESS`` -- that is a completed-with-note." The patient was
    seen, and no status may say otherwise. This is the single most load-bearing refusal in the
    lifecycle, because the tempting alternative silently erases a real contact."""
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/cancel",
        headers=_auth(world["clinician_token"]),
    )

    assert response.status_code == 409, response.text
    assert "IN_PROGRESS" in response.json()["detail"]
    still = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}",
        headers=_auth(world["clinician_token"]),
    )
    assert still.json()["visit"]["status"] == VisitStatus.IN_PROGRESS.value


def test_vis13_a_completed_visit_cannot_be_cancelled_or_reopened(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(world["clinician_token"]),
    )

    for action in ("cancel", "take"):
        response = client.post(
            f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/{action}",
            headers=_auth(world["clinician_token"]),
        )
        assert response.status_code == 409, (action, response.text)


def test_vis14_the_transition_table_is_exactly_the_three_legal_moves(client, world):
    """Pinned as data, so a future edit that adds a transition has to change this assertion and say
    why. In particular ``IN_PROGRESS -> CANCELLED`` must stay absent."""
    assert LEGAL_TRANSITIONS == frozenset(
        {
            ("CHECKED_IN", "IN_PROGRESS"),
            ("CHECKED_IN", "CANCELLED"),
            ("IN_PROGRESS", "COMPLETED"),
        }
    )
    assert ("IN_PROGRESS", "CANCELLED") not in LEGAL_TRANSITIONS
    assert TERMINAL_STATUSES == frozenset({"COMPLETED", "CANCELLED"})


def test_vis15_planned_and_no_show_are_not_reachable_states(client, world):
    """The recorded four-state deviation, pinned. Neither value exists in the enum, and the database
    CHECK constraint refuses both -- so no future caller can introduce a state with no way in."""
    assert [state.value for state in VisitStatus] == [
        "CHECKED_IN",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
    ]
    assert "PLANNED" not in {state.value for state in VisitStatus}
    assert "NO_SHOW" not in {state.value for state in VisitStatus}


# == clinical documentation =======================================================================


def test_vis16_notes_are_append_only_revisions(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    first = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/notes",
        headers=_auth(world["clinician_token"]),
        json={"body": "Examen initial consigné."},
    )
    second = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/notes",
        headers=_auth(world["clinician_token"]),
        json={"body": "Correction : examen initial consigné, tension relevée par l'infirmière."},
    )

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["revision_number"] == 1
    assert second.json()["revision_number"] == 2

    detail = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}",
        headers=_auth(world["clinician_token"]),
    ).json()
    # Both revisions survive: the earlier text is not replaced, and the history is readable.
    assert [note["revision_number"] for note in detail["notes"]] == [1, 2]
    assert detail["notes"][0]["body"] == "Examen initial consigné."


def test_vis17_a_note_records_its_own_author_not_the_visits_clinician(client, world):
    """A covering clinician's words are attributed to the covering clinician."""
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    note = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/notes",
        headers=_auth(world["covering_token"]),
        json={"body": "Vu en couverture pendant la pause."},
    )

    assert note.status_code == 201, note.text
    assert note.json()["author_identity_id"] == world["covering_id"]
    assert world["covering_id"] != world["clinician_id"]


def test_vis18_a_note_cannot_be_written_before_the_visit_starts(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    _code, visit = _start_visit(client, provider_id, world["clinician_token"], patient["id"])

    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/notes",
        headers=_auth(world["clinician_token"]),
        json={"body": "Trop tôt."},
    )
    assert response.status_code == 409


def test_vis19_a_completed_visits_note_cannot_be_amended_here(client, world):
    """The named gap, pinned so it is a decision rather than an accident: post-completion amendment
    belongs in an explicit versioned workflow (ADR-048's shape), not a second weaker one invented
    inside this ticket."""
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/notes",
        headers=_auth(world["clinician_token"]),
        json={"body": "Consultation terminée."},
    )
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(world["clinician_token"]),
    )

    late = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/notes",
        headers=_auth(world["clinician_token"]),
        json={"body": "Ajout après coup."},
    )
    assert late.status_code == 409


def test_vis20_an_empty_note_is_refused(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    for body in ("", "   "):
        response = client.post(
            f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/notes",
            headers=_auth(world["clinician_token"]),
            json={"body": body},
        )
        assert response.status_code == 422, (body, response.text)


def test_vis21_a_visit_may_be_completed_with_no_note_at_all(client, world):
    """No invented clinical documentation policy: an empty visit is an honest record of a contact
    nobody wrote about, and requiring a note would be a clinical rule this repository may not make
    up (``AGENTS.md``)."""
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    done = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(world["clinician_token"]),
    )
    assert done.status_code == 200, done.text


def test_vis22_the_stated_reason_can_be_corrected_while_the_visit_is_open(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    _code, visit = _start_visit(client, provider_id, world["clinician_token"], patient["id"])

    fixed = client.put(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/reason",
        headers=_auth(world["clinician_token"]),
        json={"reason_for_visit": "Fièvre depuis trois jours"},
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["reason_for_visit"] == "Fièvre depuis trois jours"


def test_vis23_the_stated_reason_is_frozen_once_the_visit_is_terminal(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(world["clinician_token"]),
    )

    late = client.put(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/reason",
        headers=_auth(world["clinician_token"]),
        json={"reason_for_visit": "Autre chose"},
    )
    assert late.status_code == 409


# == reads ========================================================================================


def test_vis24_the_worklist_filters_by_status_and_rejects_an_unknown_one(client, world):
    provider_id = world["provider_id"]
    waiting = _make_patient(
        client, provider_id, world["clinician_token"], display_name="En attente"
    )
    seen = _make_patient(client, provider_id, world["clinician_token"], display_name="En cours")
    _start_visit(client, provider_id, world["clinician_token"], waiting["id"])
    in_progress_id = _visit_in_progress(client, world, seen["id"])

    checked_in = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits?status=CHECKED_IN",
        headers=_auth(world["clinician_token"]),
    )
    assert checked_in.status_code == 200
    assert [row["patient_id"] for row in checked_in.json()] == [waiting["id"]]

    both = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits?status=CHECKED_IN&status=IN_PROGRESS",
        headers=_auth(world["clinician_token"]),
    )
    assert len(both.json()) == 2
    assert in_progress_id in {row["id"] for row in both.json()}

    # An unknown state is a clean 422, never an empty list that reads as "nobody is waiting".
    bogus = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits?status=PLANNED",
        headers=_auth(world["clinician_token"]),
    )
    assert bogus.status_code == 422


def test_vis25_a_patients_visit_history_is_scoped_to_this_organization(client, world):
    provider_id = world["provider_id"]
    patient = _make_patient(client, provider_id, world["clinician_token"])
    first = _visit_in_progress(client, world, patient["id"])
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{first}/complete",
        headers=_auth(world["clinician_token"]),
    )
    _code, second = _start_visit(client, provider_id, world["clinician_token"], patient["id"])

    history = client.get(
        f"/exchange/v1/organizations/{provider_id}/patients/{patient['id']}/visits",
        headers=_auth(world["clinician_token"]),
    )
    assert history.status_code == 200, history.text
    assert {row["id"] for row in history.json()} == {first, second["id"]}

    # The same patient id, asked of the other provider, is simply not their patient.
    foreign = client.get(
        f"/exchange/v1/organizations/{world['other_provider_id']}/patients/{patient['id']}/visits",
        headers=_auth(world["clinician_z_token"]),
    )
    assert foreign.status_code == 404


def test_vis26_an_unknown_visit_id_is_404_not_500(client, world):
    response = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits/{uuid.uuid4()}",
        headers=_auth(world["clinician_token"]),
    )
    assert response.status_code == 404


# == authorization: VIS-AUTH-1..8 =================================================================


def test_visauth1_no_token_is_401(client, world):
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    response = client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers={"Idempotency-Key": _key()},
        json={"patient_id": patient["id"], "reason_for_visit": "Sans jeton"},
    )
    assert response.status_code == 401


def test_visauth2_an_authenticated_non_member_is_404(client, world):
    """404, never 403: a stranger must not learn that this organization exists."""
    response = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers=_auth(world["outsider_token"]),
    )
    assert response.status_code == 404


def test_visauth3_an_org_admin_without_clinician_is_403(client, world):
    """403, not 404: they genuinely belong to this organization, they simply may not see patients.
    The distinction is the whole reason ``require_clinician`` exists separately from
    ``require_membership``."""
    response = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers=_auth(world["admin_a_token"]),
    )
    assert response.status_code == 403


def test_visauth4_a_clinician_of_another_provider_is_404(client, world):
    response = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers=_auth(world["clinician_z_token"]),
    )
    assert response.status_code == 404


def test_visauth5_a_lab_technician_is_404_at_a_provider(client, world):
    response = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers=_auth(world["tech_token"]),
    )
    assert response.status_code == 404


def test_visauth6_the_platform_admin_realm_role_does_not_open_a_clinical_door(client, world):
    """A platform realm role configures the network; it is not a clinical credential. This mirrors
    what `require_clinician` already does for the patient directory, and ADR-063's principle that
    platform authority never silently widens."""
    response = client.get(
        f"/exchange/v1/organizations/{world['provider_id']}/visits",
        headers=_auth(world["platform_token"]),
    )
    assert response.status_code in (403, 404)


def test_visauth7_a_deactivated_membership_loses_visit_access_on_the_next_request(client, world):
    """The same token, before and after. No re-login: that is the property under test, and it is the
    one DHEALTH-NATIVE-ACCESS-001 made reachable through the product."""
    provider_id = world["provider_id"]
    before = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits",
        headers=_auth(world["clinician_token"]),
    )
    assert before.status_code == 200

    revoked = client.post(
        f"/exchange/v1/organizations/{provider_id}/memberships/"
        f"{world['clinician_membership']}/deactivate",
        headers=_auth(world["admin_a_token"]),
    )
    assert revoked.status_code == 200, revoked.text

    after = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits",
        headers=_auth(world["clinician_token"]),
    )
    assert after.status_code in (403, 404)


def test_visauth8_a_visit_id_from_another_organization_is_404_for_every_operation(client, world):
    """Anti-enumeration across the whole surface, not only the read: a clinician holding a real
    visit UUID from Clinique A must not be able to confirm it, take it, note it, or finish it from
    Clinique Z."""
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])
    other = world["other_provider_id"]
    token = world["clinician_z_token"]

    assert (
        client.get(
            f"/exchange/v1/organizations/{other}/visits/{visit_id}", headers=_auth(token)
        ).status_code
        == 404
    )
    for action in ("take", "complete", "cancel"):
        response = client.post(
            f"/exchange/v1/organizations/{other}/visits/{visit_id}/{action}",
            headers=_auth(token),
        )
        assert response.status_code == 404, (action, response.text)
    noted = client.post(
        f"/exchange/v1/organizations/{other}/visits/{visit_id}/notes",
        headers=_auth(token),
        json={"body": "Ne devrait pas passer."},
    )
    assert noted.status_code == 404
    reasoned = client.put(
        f"/exchange/v1/organizations/{other}/visits/{visit_id}/reason",
        headers=_auth(token),
        json={"reason_for_visit": "Ne devrait pas passer."},
    )
    assert reasoned.status_code == 404


def test_visauth9_a_patient_from_another_organization_cannot_be_given_a_visit(client, world):
    """The application refuses it, and the composite foreign key would refuse it again -- see
    ``test_vis40`` for the database layer of the same guarantee."""
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    code, _body = _start_visit(
        client, world["other_provider_id"], world["clinician_z_token"], patient["id"]
    )
    assert code == 404


# == order linkage ================================================================================


def _create_intent(
    client: TestClient,
    provider_id: str,
    token: str,
    patient_reference_id: str,
    entry_id: str,
    *,
    visit_id: str | None = None,
    key: str | None = None,
):
    body: dict = {
        "patient_reference_id": patient_reference_id,
        "catalog_entry_ids": [entry_id],
    }
    if visit_id is not None:
        body["visit_id"] = visit_id
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key or _key()},
        json=body,
    )


def test_vis30_an_order_raised_during_a_visit_carries_the_visit_to_the_committed_order(
    client, world, catalog
):
    """The load-bearing linkage assertion: captured on the intent, copied forward by commit."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-CBC", display_name="CBC (synthetic)")
    patient = _make_patient(client, provider_id, token)
    visit_id = _visit_in_progress(client, world, patient["id"])

    intent = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit_id,
    )
    assert intent.status_code == 201, intent.text
    assert intent.json()["visit_id"] == visit_id

    committed = client.post(
        f"/exchange/v1/organizations/{provider_id}/order-intents/{intent.json()['id']}/commit",
        headers=_auth(token),
    )
    assert committed.status_code in (200, 201), committed.text
    assert committed.json()["visit_id"] == visit_id

    detail = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}",
        headers=_auth(token),
    ).json()
    assert [order["id"] for order in detail["orders"]] == [committed.json()["id"]]


def test_vis31_an_order_with_no_visit_still_works_and_stays_unlinked(client, world, catalog):
    """Backward compatibility is permanent, not transitional: a standalone order is a real case."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-GLU", display_name="Glucose (synthetic)")
    patient = _make_patient(client, provider_id, token)

    intent = _create_intent(
        client, provider_id, token, patient["primary_patient_reference_id"], str(entry.id)
    )
    assert intent.status_code == 201, intent.text
    assert intent.json()["visit_id"] is None

    committed = client.post(
        f"/exchange/v1/organizations/{provider_id}/order-intents/{intent.json()['id']}/commit",
        headers=_auth(token),
    )
    assert committed.status_code in (200, 201), committed.text
    assert committed.json()["visit_id"] is None


def test_vis32_an_order_cannot_be_raised_during_a_visit_that_has_not_started(
    client, world, catalog
):
    """An order attached to a ``CHECKED_IN`` visit would claim a consultation that never began --
    the visit has no clinician yet by construction."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-NA", display_name="Sodium (synthetic)")
    patient = _make_patient(client, provider_id, token)
    _code, visit = _start_visit(client, provider_id, token, patient["id"])

    response = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit["id"],
    )
    assert response.status_code == 409, response.text
    assert "in progress" in response.json()["detail"]


def test_vis33_an_order_for_a_different_patient_than_the_visit_is_refused(client, world, catalog):
    """Two independent statements about who is being treated. If they disagree, one is wrong, and
    choosing a winner silently is the identity guesswork ADR-051 forbids."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-K", display_name="Potassium (synthetic)")
    visited = _make_patient(client, provider_id, token, display_name="Patient Visité")
    other = _make_patient(client, provider_id, token, display_name="Patient Autre")
    visit_id = _visit_in_progress(client, world, visited["id"])

    response = _create_intent(
        client,
        provider_id,
        token,
        other["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit_id,
    )
    assert response.status_code == 409, response.text
    assert "different patient" in response.json()["detail"]


def test_vis34_a_visit_from_another_organization_cannot_be_named_by_an_order(
    client, world, catalog
):
    provider_id = world["provider_id"]
    entry, _ = catalog.create_entry(code="DH-VIS-CA", display_name="Calcium (synthetic)")
    patient = _make_patient(client, provider_id, world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])

    z_patient = _make_patient(client, world["other_provider_id"], world["clinician_z_token"])
    response = _create_intent(
        client,
        world["other_provider_id"],
        world["clinician_z_token"],
        z_patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit_id,
    )
    assert response.status_code == 404, response.text


def test_vis35_replaying_one_key_under_a_different_visit_is_a_loud_conflict(client, world, catalog):
    """Without ``visit_id`` in the replay comparison this would silently return the *first* visit's
    intent while the caller believed it had attached the order to the second."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-MG", display_name="Magnesium (synthetic)")
    patient = _make_patient(client, provider_id, token)
    first_visit = _visit_in_progress(client, world, patient["id"])

    # The intent is raised while the first visit is genuinely in progress -- which is the only
    # moment it can be (VIS-32) -- and only then does the first consultation end and a second begin.
    key = _key()
    first = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=first_visit,
        key=key,
    )
    assert first.status_code == 201, first.text

    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{first_visit}/complete",
        headers=_auth(token),
    )
    second_visit = _visit_in_progress(client, world, patient["id"])

    replay = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=second_visit,
        key=key,
    )
    assert replay.status_code == 409, replay.text
    assert "visit" in replay.json()["detail"]


def test_vis36_replaying_one_key_under_the_same_visit_returns_the_same_intent(
    client, world, catalog
):
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-PH", display_name="Phosphate (synthetic)")
    patient = _make_patient(client, provider_id, token)
    visit_id = _visit_in_progress(client, world, patient["id"])
    key = _key()

    first = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit_id,
        key=key,
    )
    second = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit_id,
        key=key,
    )

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["visit_id"] == visit_id


def test_vis37_committing_after_the_visit_completed_still_succeeds(client, world, catalog):
    """Commit deliberately does not re-validate the visit. A lost-response retry sent after the
    clinician finished must not fail where the first attempt would have succeeded -- that is the
    entire guarantee the intent mechanism exists to provide."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-TSH", display_name="TSH (synthetic)")
    patient = _make_patient(client, provider_id, token)
    visit_id = _visit_in_progress(client, world, patient["id"])
    intent = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit_id,
    )
    assert intent.status_code == 201, intent.text

    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(token),
    )

    committed = client.post(
        f"/exchange/v1/organizations/{provider_id}/order-intents/{intent.json()['id']}/commit",
        headers=_auth(token),
    )
    assert committed.status_code in (200, 201), committed.text
    assert committed.json()["visit_id"] == visit_id


def test_vis38_completing_a_visit_never_depends_on_its_orders(client, world, catalog):
    """Ticket requirement, asserted rather than assumed: an outstanding diagnostic order does not
    hold the visit open, and the order's own status is untouched by the visit finishing."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-CRP", display_name="CRP (synthetic)")
    patient = _make_patient(client, provider_id, token)
    visit_id = _visit_in_progress(client, world, patient["id"])
    intent = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit_id,
    )
    committed = client.post(
        f"/exchange/v1/organizations/{provider_id}/order-intents/{intent.json()['id']}/commit",
        headers=_auth(token),
    )
    order_status_before = committed.json()["status"]
    assert order_status_before == "AWAITING_LAB_SELECTION"

    done = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/complete",
        headers=_auth(token),
    )
    assert done.status_code == 200, done.text

    detail = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}",
        headers=_auth(token),
    ).json()
    assert detail["visit"]["status"] == "COMPLETED"
    assert detail["orders"][0]["status"] == order_status_before


def test_vis39_the_visit_detail_never_inlines_result_values(client, world, catalog):
    """ADR-044's boundary, structurally: this response has no field that could carry an
    entered-but-unreleased observation, so a new screen cannot leak one."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    entry, _ = catalog.create_entry(code="DH-VIS-HB", display_name="Hemoglobin (synthetic)")
    patient = _make_patient(client, provider_id, token)
    visit_id = _visit_in_progress(client, world, patient["id"])
    intent = _create_intent(
        client,
        provider_id,
        token,
        patient["primary_patient_reference_id"],
        str(entry.id),
        visit_id=visit_id,
    )
    client.post(
        f"/exchange/v1/organizations/{provider_id}/order-intents/{intent.json()['id']}/commit",
        headers=_auth(token),
    )

    detail = client.get(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}",
        headers=_auth(token),
    ).json()
    order_keys = set(detail["orders"][0])
    for leaked in ("observations", "results", "result", "values", "release", "observation_values"):
        assert leaked not in order_keys, leaked
    assert set(detail) == {"visit", "notes", "orders"}


# == audit ========================================================================================


def test_vis50_every_mutation_writes_exactly_one_visit_scoped_audit_row(client, world, sessions):
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    patient = _make_patient(client, provider_id, token)

    before = [event for event in _db_audit(sessions) if event.event_type.startswith("visit_")]
    assert before == []

    _code, visit = _start_visit(client, provider_id, token, patient["id"])
    client.put(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/reason",
        headers=_auth(token),
        json={"reason_for_visit": "Fièvre, corrigé"},
    )
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/take", headers=_auth(token)
    )
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/notes",
        headers=_auth(token),
        json={"body": "Note clinique."},
    )
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit['id']}/complete",
        headers=_auth(token),
    )

    rows = [event for event in _db_audit(sessions) if event.event_type.startswith("visit_")]
    assert [event.event_type for event in rows] == [
        "visit_created",
        "visit_reason_amended",
        "visit_started",
        "visit_note_appended",
        "visit_completed",
    ]
    for event in rows:
        assert event.resource_type == "visit"
        assert str(event.resource_id) == visit["id"]
        assert str(event.organization_id) == provider_id
        assert str(event.actor_identity_id) == world["clinician_id"]


def test_vis51_a_note_body_never_reaches_any_audit_row(client, world, sessions, audit_path):
    """``AGENTS.md``: clinical free text is not audit material. Asserted against every column of
    every row, and against the best-effort refusal log too, rather than trusting the call site."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    patient = _make_patient(client, provider_id, token)
    visit_id = _visit_in_progress(client, world, patient["id"])
    secret = "SIGNAL-DE-TEST-CONTENU-CLINIQUE"
    client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/notes",
        headers=_auth(token),
        json={"body": f"Observation: {secret}"},
    )

    for event in _db_audit(sessions):
        for column in event.__table__.columns:
            assert secret not in str(getattr(event, column.name))
    assert secret not in json.dumps(_file_audit(audit_path))


def test_vis52_a_refused_transition_writes_no_security_audit_row(client, world, sessions):
    """A 409 is not a state mutation, so it must not appear in the transactional security table --
    the boundary ``ExchangeSecurityAuditEvent``'s own docstring draws."""
    provider_id = world["provider_id"]
    token = world["clinician_token"]
    patient = _make_patient(client, provider_id, token)
    visit_id = _visit_in_progress(client, world, patient["id"])
    before = len(_db_audit(sessions))

    refused = client.post(
        f"/exchange/v1/organizations/{provider_id}/visits/{visit_id}/cancel", headers=_auth(token)
    )
    assert refused.status_code == 409

    assert len(_db_audit(sessions)) == before


# == the database layer ===========================================================================
#
# Guarantees no API call can violate, asserted directly against the schema. These exist because the
# application checks above could all be deleted and the database would still refuse -- which is the
# point of having them.


def test_vis40_the_cross_provider_patient_guard_is_a_composite_foreign_key(client, world):
    """DH-IDENTITY-001 §8's rule, applied to visits: the *database* must reject a visit naming a
    patient of another provider organization, not merely the service layer.

    Asserted **structurally** here rather than by attempting the bad insert, because this suite's
    SQLite engine does not enable ``PRAGMA foreign_keys`` (see ``tests/conftest.py``) and an insert
    that "succeeds" would prove nothing either way. The refusal itself is qualified against real
    PostgreSQL in ``tests/integration/test_dhealthvisit001_postgres_qualification.py`` -- the same
    division of labour this file's docstring states. What is checkable here is the shape: the
    constraint must be composite and must target ``Patient``'s composite unique key, because a plain
    foreign key to ``exchange_patients.id`` would permit exactly the cross-tenant row it is meant to
    stop."""
    composite = [
        constraint
        for constraint in Visit.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint) and len(constraint.elements) == 2
    ]
    assert len(composite) == 1, "the composite patient/provider foreign key is missing"
    constraint = composite[0]
    assert constraint.name == "fk_exchange_visits_patient_same_provider"
    assert {element.parent.name for element in constraint.elements} == {
        "patient_id",
        "provider_organization_id",
    }
    assert {element.column.table.name for element in constraint.elements} == {"exchange_patients"}
    assert {element.column.name for element in constraint.elements} == {
        "id",
        "provider_organization_id",
    }
    # No single-column foreign key on `patient_id` beside it: that would be the weaker check, and
    # its presence would suggest the composite one is optional.
    single = [
        constraint
        for constraint in Visit.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and len(constraint.elements) == 1
        and constraint.elements[0].parent.name == "patient_id"
    ]
    assert single == []


@pytest.mark.parametrize(
    ("status_value", "timestamps", "why"),
    [
        ("COMPLETED", {}, "COMPLETED without completed_at"),
        ("CANCELLED", {}, "CANCELLED without cancelled_at"),
        ("IN_PROGRESS", {}, "IN_PROGRESS without started_at"),
        ("CHECKED_IN", {"completed_at": "now"}, "CHECKED_IN carrying completed_at"),
        ("CHECKED_IN", {"started_at": "now"}, "CHECKED_IN carrying started_at"),
    ],
)
def test_vis41_the_timestamp_check_constraints_make_state_history_non_forgeable(
    client, world, sessions, status_value, timestamps, why
):
    from daher_adapter.models import utc_now

    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    fields = {name: utc_now() for name in timestamps}
    # A clinician is required by its own constraint for the two started states, so supply one where
    # the state demands it -- otherwise this test would pass for the wrong reason.
    if status_value in ("IN_PROGRESS", "COMPLETED"):
        fields["clinician_identity_id"] = uuid.UUID(world["clinician_id"])
        if status_value == "COMPLETED":
            fields["started_at"] = utc_now()

    with sessions() as session:
        session.add(
            Visit(
                idempotency_key=_key(),
                provider_organization_id=uuid.UUID(world["provider_id"]),
                patient_id=uuid.UUID(patient["id"]),
                status=status_value,
                reason_for_visit="État incohérent",
                **fields,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_vis42_a_taken_visit_cannot_exist_without_a_clinician(client, world, sessions):
    from daher_adapter.models import utc_now

    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    with sessions() as session:
        session.add(
            Visit(
                idempotency_key=_key(),
                provider_organization_id=uuid.UUID(world["provider_id"]),
                patient_id=uuid.UUID(patient["id"]),
                status="IN_PROGRESS",
                reason_for_visit="Sans clinicien",
                started_at=utc_now(),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_vis43_two_note_revisions_cannot_share_a_number(client, world, sessions):
    """The append-only concurrency guarantee at the database layer: the loser of a race is refused
    rather than silently overwriting the winner's text."""
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    visit_id = _visit_in_progress(client, world, patient["id"])
    client.post(
        f"/exchange/v1/organizations/{world['provider_id']}/visits/{visit_id}/notes",
        headers=_auth(world["clinician_token"]),
        json={"body": "Révision une."},
    )

    with sessions() as session:
        session.add(
            VisitNote(
                visit_id=uuid.UUID(visit_id),
                revision_number=1,
                author_identity_id=uuid.UUID(world["clinician_id"]),
                body="Une deuxième révision numéro un.",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_vis44_an_unknown_status_value_is_refused_by_the_check_constraint(client, world, sessions):
    """Including ``PLANNED`` and ``NO_SHOW``: the two omitted states are refused by the schema, so
    they cannot be reintroduced by an application-layer edit alone."""
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    for bad in ("PLANNED", "NO_SHOW", "ARRIVED", "planned"):
        with sessions() as session:
            session.add(
                Visit(
                    idempotency_key=_key(),
                    provider_organization_id=uuid.UUID(world["provider_id"]),
                    patient_id=uuid.UUID(patient["id"]),
                    status=bad,
                    reason_for_visit="État inexistant",
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()


def test_vis45_visit_id_is_nullable_on_both_the_intent_and_the_order(client, world):
    """Pinned structurally. Both must stay nullable forever -- every pre-visit order and every
    standalone order depends on it (``AGENTS.md``'s backward-compatibility rule)."""
    assert LabOrder.__table__.c.visit_id.nullable is True
    assert LabOrderIntent.__table__.c.visit_id.nullable is True


def test_vis46_every_reference_to_a_visit_is_restrict(client, world):
    """``RESTRICT`` is the enforcement behind "a completed visit is never deleted": once an order,
    an intent or a note references a visit, no delete path for it exists at all. There is also no
    delete endpoint, but a constraint outlives any particular router.

    Structural for the same reason as ``test_vis40``: SQLite foreign keys are off in this suite, so
    the *behaviour* is qualified against real PostgreSQL and the *declaration* is qualified here."""
    referencing = {
        (table.name, column.name): fk
        for table in (LabOrder.__table__, LabOrderIntent.__table__, VisitNote.__table__)
        for column in table.columns
        for fk in column.foreign_keys
        if fk.column.table.name == "exchange_visits"
    }
    assert set(referencing) == {
        ("exchange_lab_orders", "visit_id"),
        ("exchange_lab_order_intents", "visit_id"),
        ("exchange_visit_notes", "visit_id"),
    }
    for (table_name, column_name), fk in referencing.items():
        assert fk.ondelete == "RESTRICT", (table_name, column_name, fk.ondelete)


def test_vis47_a_date_of_birth_is_optional_on_a_worklist_row(client, world):
    """ADR-051: nothing invents a placeholder birthday, so the worklist must render without one."""
    patient = _make_patient(
        client, world["provider_id"], world["clinician_token"], date_of_birth=None
    )
    code, visit = _start_visit(
        client, world["provider_id"], world["clinician_token"], patient["id"]
    )
    assert code == 201
    assert visit["patient_date_of_birth"] is None
    assert isinstance(visit["patient_display_name"], str)


def test_vis48_a_reason_is_required(client, world):
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    for reason in ("", "   "):
        response = client.post(
            f"/exchange/v1/organizations/{world['provider_id']}/visits",
            headers={**_auth(world["clinician_token"]), "Idempotency-Key": _key()},
            json={"patient_id": patient["id"], "reason_for_visit": reason},
        )
        assert response.status_code == 422, (reason, response.text)


def test_vis49_a_visit_requires_a_canonical_idempotency_key(client, world):
    patient = _make_patient(client, world["provider_id"], world["clinician_token"])
    for key in (None, "not-a-uuid", str(uuid.uuid4()).upper(), f"{{{uuid.uuid4()}}}"):
        headers = _auth(world["clinician_token"])
        if key is not None:
            headers["Idempotency-Key"] = key
        response = client.post(
            f"/exchange/v1/organizations/{world['provider_id']}/visits",
            headers=headers,
            json={"patient_id": patient["id"], "reason_for_visit": "Clé douteuse"},
        )
        assert response.status_code == 422, (key, response.text)


def test_vis53_the_visit_date_of_birth_round_trips_exactly(client, world):
    """No approximate or year-only semantics anywhere (ADR-051/DH-IDENTITY-001)."""
    patient = _make_patient(
        client, world["provider_id"], world["clinician_token"], date_of_birth="1954-12-31"
    )
    _code, visit = _start_visit(
        client, world["provider_id"], world["clinician_token"], patient["id"]
    )
    assert visit["patient_date_of_birth"] == "1954-12-31"
    assert date.fromisoformat(visit["patient_date_of_birth"]) == date(1954, 12, 31)
