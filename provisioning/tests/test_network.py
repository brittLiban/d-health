"""Application-level source allowlist (ADR-023).

The firewall is the primary control; this is the second one, for when the firewall gets
edited. So the tests that matter are the refusals — and specifically that a forwarded header
cannot talk its way past the check.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daher_provisioning.app import create_app
from daher_provisioning.audit import AuditLog, AuditOutcome
from daher_provisioning.auth import PORTAL_SCOPES, KeyStore, issue_key
from daher_provisioning.network import SourceAllowlist, SourceNotAllowed


class TestAllowlist:
    def test_an_empty_allowlist_does_not_enforce(self):
        allowlist = SourceAllowlist([])
        assert not allowlist.enforced
        allowlist.check("203.0.113.9")  # relies on the firewall alone

    def test_an_allowed_address_passes(self):
        SourceAllowlist(["10.0.0.5"]).check("10.0.0.5")

    def test_a_cidr_range_passes(self):
        SourceAllowlist(["10.0.0.0/24"]).check("10.0.0.77")

    def test_an_outside_address_is_refused(self):
        with pytest.raises(SourceNotAllowed, match="not in the provisioning allowlist"):
            SourceAllowlist(["10.0.0.0/24"]).check("203.0.113.9")

    def test_the_refusal_discourages_disabling_the_check(self):
        with pytest.raises(SourceNotAllowed, match="rather than disabling the check"):
            SourceAllowlist(["10.0.0.5"]).check("203.0.113.9")

    def test_a_missing_address_is_refused_while_enforcing(self):
        """Anything that obscures the peer must not become a bypass."""
        with pytest.raises(SourceNotAllowed, match="no determinable source"):
            SourceAllowlist(["10.0.0.5"]).check(None)

    def test_an_unparseable_address_is_refused(self):
        with pytest.raises(SourceNotAllowed, match="unparseable"):
            SourceAllowlist(["10.0.0.5"]).check("not-an-ip")

    def test_a_host_address_with_a_prefix_is_accepted_as_config(self):
        """Typing 10.0.0.5/24 is an easy slip; failing on it pushes people to disable it."""
        SourceAllowlist(["10.0.0.5/24"]).check("10.0.0.99")

    def test_ipv6_is_supported(self):
        SourceAllowlist(["2001:db8::/32"]).check("2001:db8::1")

    def test_a_non_address_peer_is_refused(self):
        """Starlette's TestClient defaults its peer to the literal string 'testclient'.

        Anything that is not an address must be refused rather than allowed, or every
        transport that obscures the peer becomes a bypass.
        """
        with pytest.raises(SourceNotAllowed):
            SourceAllowlist(["127.0.0.0/8"]).check("testclient")


class TestEnforcedThroughHttp:
    @staticmethod
    def wired(tmp_path, cidrs):
        key, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        log = AuditLog(tmp_path / "audit.jsonl")
        app = create_app(
            key_store=KeyStore([record]),
            audit_log=log,
            allowed_source_cidrs=cidrs,
        )
        # ⚠️ TestClient's default peer is the literal string "testclient", which the
        # allowlist correctly refuses as unparseable. Set a real address so these tests
        # exercise allow/deny rather than the unparseable path.
        return TestClient(app, client=("127.0.0.1", 40000)), key, log

    def test_a_disallowed_source_is_refused_before_authentication(self, tmp_path):
        """An unauthorised source must not even reach credential comparison."""
        client, key, _ = self.wired(tmp_path, ["10.99.99.0/24"])
        response = client.get("/facilities", headers={"Authorization": f"Bearer {key}"})
        assert response.status_code == 403
        assert response.json()["detail"] == "source not allowed"

    def test_a_disallowed_source_is_audited(self, tmp_path):
        client, key, log = self.wired(tmp_path, ["10.99.99.0/24"])
        client.get("/facilities", headers={"Authorization": f"Bearer {key}"})
        entry = log.entries()[-1]
        assert entry.outcome == AuditOutcome.DENIED
        assert entry.actor == "blocked-source"

    def test_an_allowed_source_still_works(self, tmp_path):
        client, key, _ = self.wired(tmp_path, ["127.0.0.0/8"])
        assert (
            client.get("/facilities", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        )

    def test_a_forwarded_header_cannot_bypass_the_allowlist(self, tmp_path):
        """X-Forwarded-For is attacker-controlled; believing it hands over the bypass."""
        client, key, _ = self.wired(tmp_path, ["10.99.99.0/24"])
        response = client.get(
            "/facilities",
            headers={
                "Authorization": f"Bearer {key}",
                "X-Forwarded-For": "10.99.99.5",
                "X-Real-IP": "10.99.99.5",
                "Forwarded": "for=10.99.99.5",
            },
        )
        assert response.status_code == 403, (
            "a forwarded header talked its way past the source allowlist"
        )

    def test_agents_are_also_subject_to_the_allowlist(self, tmp_path):
        client, _, _ = self.wired(tmp_path, ["10.99.99.0/24"])
        response = client.get("/agent/jobs/claim", headers={"Authorization": "Bearer dha_h_x"})
        assert response.status_code == 403

    def test_health_is_unaffected(self, tmp_path):
        """Liveness must stay answerable; it reveals nothing."""
        client, _, _ = self.wired(tmp_path, ["10.99.99.0/24"])
        assert client.get("/health").status_code == 200

    def test_no_allowlist_leaves_behaviour_unchanged(self, tmp_path):
        client, key, _ = self.wired(tmp_path, None)
        assert (
            client.get("/facilities", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        )
