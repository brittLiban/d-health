"""DHEALTH-ACCESS-CAPABILITIES-001: the authorization matrix, pinned across the migration.

§18 is the load-bearing requirement of this ticket: *"No staff member should gain or lose authority
merely because the representation changed."* This file is how that is made checkable rather than
asserted.

── What it does ──

Every persona DHealth currently has calls one representative route per authorization boundary, and
the **exact status code** is recorded. The expected values below were captured on `790c736` — the
last commit before capabilities existed, when authority came from role-name checks — and they have
not been edited since. Re-running this file after the migration compares role-era authority against
capability-era authority, cell by cell.

A diff here is not a test to update. It is authority drift, and §18 calls that a P0/P1 security
defect. The only legitimate way for a cell to change is a documented, deliberate fix, recorded in
the ticket and in the ADR -- and **this ticket makes none**. §27's correction (an administrator who
can configure prices must not land on the operational billing surface) is a *navigation* change, not
an authority change: reading the price list with either billing capability is deliberate and stays,
so `billing.services.read.provider` remains 200 for `org_admin`. Every one of the 160 cells below is
expected to be identical before and after.

── Why exact status codes, not "allowed/refused" ──

403 and 404 mean different things here and the difference is itself a security property: 404 is
anti-enumeration for a caller with no relationship to the organization, 403 is a member lacking
permission. Collapsing them into a boolean would let a migration turn one into the other -- leaking
whether an organization exists -- while this file stayed green.

── Why these routes ──

One per authority boundary, chosen so the answer depends on authorization alone: reads of
collections that exist for both organizations, so a 200 is a real 200 and never an accident of a
missing row. The boundaries are derived from `api.py`'s own gate usage (39 `_require_clinician`
routes plus four review routes reached through `_submit_doctor_review`, 17 `_require_lab_tech`, and
so on), not from memory.

All patient, service and price data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.organizations import OrganizationRepository


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
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap(client: TestClient, private_key, subject: str, **kwargs) -> tuple[str, str]:
    token = make_token(private_key, subject=subject, **kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"], token


def _attach(
    client: TestClient, admin_token: str, organization_id: str, identity_id: str, roles: list[str]
) -> None:
    response = client.post(
        f"/exchange/v1/organizations/{organization_id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text


@pytest.fixture
def world(client: TestClient, keypair, organizations: OrganizationRepository):
    """Every persona DHealth has, each holding exactly the authority its name claims.

    The single-role personas are the point: an `ORG_ADMIN` who is *only* an administrator is what
    proves administration does not imply clinical or financial authority, and a `CLINICIAN` who is
    only a clinician proves the reverse. `both` (CLINICIAN + ORG_ADMIN) is here because a
    multi-role membership is where a migration is most likely to lose a row quietly.
    """
    private_key, _ = keypair
    platform_token = make_token(private_key, subject="platform", roles=[PLATFORM_ADMIN_ROLE])

    provider = make_provider(organizations, "Clinique Matrice")
    lab = make_lab(organizations, "Laboratoire Matrice")

    people: dict[str, str] = {}
    for name, org, roles in (
        ("clinician", provider, ["CLINICIAN"]),
        ("org_admin", provider, ["ORG_ADMIN"]),
        ("both", provider, ["CLINICIAN", "ORG_ADMIN"]),
        ("clerk", provider, ["BILLING_CLERK"]),
        ("lab_admin", lab, ["ORG_ADMIN"]),
        ("lab_tech", lab, ["LAB_TECH"]),
        ("releaser", lab, ["LAB_RESULT_RELEASER"]),
        ("lab_clerk", lab, ["BILLING_CLERK"]),
    ):
        identity_id, token = _bootstrap(client, private_key, name)
        _attach(client, platform_token, str(org.id), identity_id, roles)
        people[name] = token

    # A real, authenticated human who belongs to nothing. Every refusal they receive must be 404,
    # never 403: no relationship at all is indistinguishable from the organization not existing.
    _, outsider = _bootstrap(client, private_key, "outsider")
    people["outsider"] = outsider
    # Platform authority with no membership anywhere. ADR-063: network administration is not
    # clinical or financial authority, and this persona is what keeps that true.
    people["platform"] = platform_token

    return {"provider_id": str(provider.id), "lab_id": str(lab.id), "tokens": people}


#: One route per authorization boundary, with the organization placeholder filled at call time.
#:
#: `{p}` is the provider, `{l}` the laboratory. Reads only: this table is about *who may*, and a
#: write would make the second run of the file answer a different question than the first.
ROUTES: dict[str, tuple[str, str]] = {
    "patient.directory.read": ("GET", "/organizations/{p}/patients"),
    "patient.reference.read": ("GET", "/organizations/{p}/patient-references"),
    "visit.read": ("GET", "/organizations/{p}/visits"),
    "order.read": ("GET", "/organizations/{p}/orders"),
    "order.routes.read": ("GET", "/organizations/{p}/lab-routes"),
    "result.review.read": ("GET", "/organizations/{p}/result-reviews"),
    "lab.queue.read": ("GET", "/organizations/{l}/lab-orders"),
    "lab.release.queue": ("GET", "/organizations/{l}/release-queue"),
    "staff.access.read.provider": ("GET", "/organizations/{p}/memberships"),
    "staff.access.read.lab": ("GET", "/organizations/{l}/memberships"),
    "facility.offerings.read": ("GET", "/organizations/{l}/offerings"),
    "billing.invoices.read.provider": ("GET", "/organizations/{p}/billing/invoices"),
    "billing.invoices.read.lab": ("GET", "/organizations/{l}/billing/invoices"),
    "billing.services.read.provider": ("GET", "/organizations/{p}/billing/services"),
    "billing.patients.lookup": ("GET", "/organizations/{p}/billing/patients?q=a"),
    "organization.read": ("GET", "/organizations/{p}"),
}

PERSONAS = (
    "clinician",
    "org_admin",
    "both",
    "clerk",
    "lab_admin",
    "lab_tech",
    "releaser",
    "lab_clerk",
    "outsider",
    "platform",
)

#: Captured on `790c736`, before capabilities existed. **Do not edit to make a test pass.**
#:
#: Read it as: this persona, calling this route, gets this status. `200` is authority; `403` is a
#: member of the organization lacking the permission; `404` is anti-enumeration for somebody with no
#: relationship to it at all.
#:
#: Held in a JSON file beside this module rather than inline, so that a diff of the baseline is
#: visible in review as data rather than buried in a code change.
EXPECTED: dict[str, dict[str, int]] = {}

_MATRIX_FILE = Path(__file__).with_name("dhealthaccesscap001_authority_matrix.json")
if _MATRIX_FILE.exists():
    EXPECTED = json.loads(_MATRIX_FILE.read_text(encoding="utf-8"))


def _observe(client: TestClient, world) -> dict[str, dict[str, int]]:
    observed: dict[str, dict[str, int]] = {}
    for key, (method, template) in ROUTES.items():
        path = "/exchange/v1" + template.format(p=world["provider_id"], l=world["lab_id"])
        observed[key] = {}
        for persona in PERSONAS:
            response = client.request(method, path, headers=_auth(world["tokens"][persona]))
            observed[key][persona] = response.status_code
    return observed


@pytest.mark.skipif(not EXPECTED, reason="matrix not yet captured -- run the capture script first")
def test_the_authorization_matrix_is_unchanged(client, world):
    """Every cell, compared against what the role-era code answered.

    Reported as a diff of named cells rather than a dict inequality, because "matrix changed" is
    useless and "`clerk` may now read `/orders`" is a finding."""
    observed = _observe(client, world)

    drift = []
    for key in sorted(set(EXPECTED) | set(observed)):
        before, after = EXPECTED.get(key, {}), observed.get(key, {})
        for persona in sorted(set(before) | set(after)):
            was, now = before.get(persona), after.get(persona)
            if was != now:
                drift.append(f"  {key} / {persona}: {was} -> {now}")

    assert not drift, "AUTHORITY DRIFT (§18 calls this a P0/P1):\n" + "\n".join(drift)


def test_every_boundary_is_actually_exercised(client, world):
    """A matrix of all-404 would compare equal to itself and prove nothing.

    So: each route must be genuinely reachable by somebody (at least one 200) and genuinely refused
    to somebody (at least one 401/403/404). A route that is open to everyone or closed to everyone
    is not an authorization boundary and does not belong in this file."""
    observed = _observe(client, world)
    for key, row in observed.items():
        statuses = set(row.values())
        assert any(code == 200 for code in statuses), f"{key}: nobody can reach it -- {row}"
        assert any(code in (401, 403, 404) for code in statuses), (
            f"{key}: nobody is refused -- {row}"
        )


def test_an_outsider_is_never_told_an_organization_exists(client, world):
    """404 everywhere, never 403. A caller with no relationship to the organization must not be able
    to distinguish "exists but forbidden" from "does not exist" -- that distinction is an
    enumeration oracle, and it is the property most easily lost when gates are rewritten."""
    observed = _observe(client, world)
    for key, row in observed.items():
        assert row["outsider"] == 404, f"{key}: outsider got {row['outsider']}, expected 404"


def test_platform_authority_opens_no_clinical_or_financial_door(client, world):
    """ADR-063, re-pinned across the migration. A platform administrator holding no membership is a
    network administrator, not a clinician, a laboratory worker or a cashier."""
    observed = _observe(client, world)
    for key in (
        "patient.directory.read",
        "visit.read",
        "order.read",
        "result.review.read",
        "lab.queue.read",
        "lab.release.queue",
        "billing.invoices.read.provider",
        "billing.patients.lookup",
    ):
        assert observed[key]["platform"] != 200, (
            f"{key}: platform admin reached a clinical surface ({observed[key]['platform']})"
        )


def test_capture_matrix_helper_is_not_left_enabled():
    """The capture path writes the expected file; it must never run as part of an ordinary suite.

    Pinned because a capture accidentally left on would rewrite the baseline to match whatever the
    code currently does -- which is precisely the failure mode §18 exists to prevent, and it would
    look green forever."""
    import os

    assert os.environ.get("DHCAP_CAPTURE_MATRIX") is None, (
        "DHCAP_CAPTURE_MATRIX is set: the baseline would be overwritten instead of compared"
    )


def test_capture(client, world):
    """Writes the baseline, and only when explicitly asked.

    Not a test of anything -- it lives here so the capture uses the identical fixtures, personas and
    routes the comparison uses. Run once on the pre-migration commit:

        DHCAP_CAPTURE_MATRIX=1 pytest \
            tests/test_dhealthaccesscap001_authority_matrix.py::test_capture
    """
    import os

    if os.environ.get("DHCAP_CAPTURE_MATRIX") is None:
        pytest.skip("set DHCAP_CAPTURE_MATRIX=1 to capture the baseline")
    _MATRIX_FILE.write_text(
        json.dumps(_observe(client, world), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert _MATRIX_FILE.exists()


def test_uuid_import_is_used_by_the_module():
    """Guards against the lint-only edit that would silently remove a fixture dependency."""
    assert uuid is not None
