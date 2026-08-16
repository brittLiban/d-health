"""API surface behaviour (ADR-021).

The tests that carry weight are the ones asserting the boundary holds through HTTP: no
credential means no access, a missing scope means no action, denials are audited, and a
201 does not claim a facility is running when nothing was deployed.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from daher_provisioning.app import create_app
from daher_provisioning.audit import AuditLog, AuditOutcome
from daher_provisioning.auth import PORTAL_SCOPES, KeyStore, Scope, issue_key
from daher_provisioning.ratelimit import TokenBucketLimiter


@pytest.fixture
def wired(tmp_path):
    portal_key, portal_record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
    admin_key, admin_record = issue_key(
        "admin", actor="admin", scopes=frozenset(Scope) | PORTAL_SCOPES
    )
    store = KeyStore([portal_record, admin_record])
    log = AuditLog(tmp_path / "audit.jsonl")
    app = create_app(
        key_store=store,
        audit_log=log,
        limiter=TokenBucketLimiter(rate_per_second=100, burst=100),
    )
    return TestClient(app), portal_key, admin_key, log


def a_body(**overrides):
    body = {
        "display_name": "Daher Birth Centre",
        "type": "hospital",
        "deploy_target": {"host": "10.0.0.5"},
    }
    body.update(overrides)
    return body


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


class TestAuthenticationBoundary:
    def test_no_credential_is_refused(self, wired):
        client, *_ = wired
        assert client.get("/facilities").status_code == 401

    def test_wrong_credential_is_refused(self, wired):
        client, *_ = wired
        response = client.get("/facilities", headers=auth("dhp_portal_wrong"))
        assert response.status_code == 401

    def test_denial_is_audited(self, wired):
        client, _, _, log = wired
        client.get("/facilities", headers=auth("dhp_portal_wrong"))
        entries = log.entries()
        assert entries, "a rejected request must still be recorded"
        assert entries[-1].outcome == AuditOutcome.DENIED

    def test_denial_audit_never_contains_the_presented_secret(self, wired):
        client, _, _, log = wired
        secret = "do-not-log-this-value"
        client.get("/facilities", headers=auth(f"dhp_portal_{secret}"))
        assert secret not in log.path.read_text(encoding="utf-8")

    def test_health_needs_no_credential_and_reveals_nothing(self, wired):
        client, *_ = wired
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "up"}

    def test_no_schema_browser_is_exposed(self, wired):
        """A schema browser on a network-reachable service is free reconnaissance."""
        client, *_ = wired
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404


class TestScopeBoundary:
    def test_portal_cannot_decommission(self, wired):
        client, portal_key, _, _ = wired
        client.post("/facilities", json=a_body(), headers=auth(portal_key))
        response = client.post(
            "/facilities/daher-birth-centre/decommission", headers=auth(portal_key)
        )
        assert response.status_code == 403

    def test_scope_denial_is_audited(self, wired):
        client, portal_key, _, log = wired
        client.post("/facilities/anything/decommission", headers=auth(portal_key))
        assert log.entries()[-1].outcome == AuditOutcome.DENIED

    def test_a_credential_with_the_scope_may_decommission(self, wired):
        client, portal_key, admin_key, _ = wired
        client.post("/facilities", json=a_body(), headers=auth(portal_key))
        response = client.post(
            "/facilities/daher-birth-centre/decommission", headers=auth(admin_key)
        )
        assert response.status_code == 200
        assert response.json()["state"] == "decommissioned"


class TestCreateDoesNotOverclaim:
    def test_201_reports_what_did_not_happen(self, wired):
        client, portal_key, _, _ = wired
        response = client.post("/facilities", json=a_body(), headers=auth(portal_key))
        assert response.status_code == 201
        payload = response.json()
        assert payload["dns_configured"] is False
        assert payload["incomplete_steps"], "a 201 must not imply the stack is running"
        assert any("deploy" in step for step in payload["incomplete_steps"])

    def test_credentials_are_referenced_never_returned(self, wired):
        client, portal_key, _, _ = wired
        response = client.post("/facilities", json=a_body(), headers=auth(portal_key))
        body = json.dumps(response.json())
        assert "credential_set_id" in body
        for leaked in ("PASSWORD", "password", "passphrase", "OMRS_ADMIN"):
            assert leaked not in body

    def test_duplicate_facility_id_is_refused(self, wired):
        client, portal_key, _, _ = wired
        client.post("/facilities", json=a_body(), headers=auth(portal_key))
        response = client.post("/facilities", json=a_body(), headers=auth(portal_key))
        assert response.status_code == 409
        assert "patient database" in response.json()["detail"]

    def test_invalid_facility_id_is_refused(self, wired):
        client, portal_key, _, _ = wired
        response = client.post(
            "/facilities", json=a_body(facility_id="NOT VALID"), headers=auth(portal_key)
        )
        assert response.status_code == 422

    def test_labs_are_provisionable_independently(self, wired):
        """Hospitals and labs are separate types; neither implies the other."""
        client, portal_key, _, _ = wired
        response = client.post(
            "/facilities",
            json=a_body(display_name="Daher Lab", type="lab"),
            headers=auth(portal_key),
        )
        assert response.status_code == 201
        assert response.json()["facility"]["type"] == "lab"

    def test_creation_is_audited_with_the_credential_reference(self, wired):
        client, portal_key, _, log = wired
        client.post("/facilities", json=a_body(), headers=auth(portal_key))
        entry = log.entries()[-1]
        assert entry.outcome == AuditOutcome.SUCCESS
        assert entry.credential_set_id is not None


class TestReading:
    def test_list_is_empty_before_anything_is_created(self, wired):
        client, portal_key, _, _ = wired
        assert client.get("/facilities", headers=auth(portal_key)).json() == []

    def test_created_facility_appears(self, wired):
        client, portal_key, _, _ = wired
        client.post("/facilities", json=a_body(), headers=auth(portal_key))
        listed = client.get("/facilities", headers=auth(portal_key)).json()
        assert [f["facility_id"] for f in listed] == ["daher-birth-centre"]

    def test_status_carries_no_clinical_metrics(self, wired):
        client, portal_key, _, _ = wired
        client.post("/facilities", json=a_body(), headers=auth(portal_key))
        payload = client.get(
            "/facilities/daher-birth-centre/status", headers=auth(portal_key)
        ).json()
        for forbidden in ("patient", "encounter", "result", "specimen"):
            assert not any(forbidden in key.lower() for key in payload)

    def test_unknown_facility_is_404(self, wired):
        client, portal_key, _, _ = wired
        assert client.get("/facilities/nope/status", headers=auth(portal_key)).status_code == 404


class TestRateLimiting:
    def test_exhausted_budget_returns_429_with_retry_after(self, tmp_path):
        portal_key, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        app = create_app(
            key_store=KeyStore([record]),
            audit_log=AuditLog(tmp_path / "audit.jsonl"),
            limiter=TokenBucketLimiter(rate_per_second=0.001, burst=1),
        )
        client = TestClient(app)
        assert client.get("/facilities", headers=auth(portal_key)).status_code == 200
        response = client.get("/facilities", headers=auth(portal_key))
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_rate_limiting_is_audited(self, tmp_path):
        portal_key, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        log = AuditLog(tmp_path / "audit.jsonl")
        client = TestClient(
            create_app(
                key_store=KeyStore([record]),
                audit_log=log,
                limiter=TokenBucketLimiter(rate_per_second=0.001, burst=1),
            )
        )
        client.get("/facilities", headers=auth(portal_key))
        client.get("/facilities", headers=auth(portal_key))
        assert any(e.action.value == "auth.rate_limited" for e in log.entries())


class TestAuditIntegrity:
    def test_the_chain_verifies_after_a_realistic_session(self, wired):
        client, portal_key, admin_key, log = wired
        client.get("/facilities", headers=auth(portal_key))
        client.post("/facilities", json=a_body(), headers=auth(portal_key))
        client.get("/facilities/daher-birth-centre/status", headers=auth(portal_key))
        client.get("/facilities", headers=auth("dhp_portal_wrong"))
        client.post("/facilities/daher-birth-centre/decommission", headers=auth(admin_key))

        checked = log.verify_chain()
        assert checked >= 5, "every action, including the denial, must be recorded"
