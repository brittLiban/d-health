"""Registered deploy hosts (ADR-022).

Two escalation paths this closes, both of which looked like ordinary requests:

- a caller naming any address it liked, and
- a create request quietly reusing a host that already runs a live facility.
"""

from __future__ import annotations

import dataclasses

import pytest

from daher_provisioning.hosts import HostError, HostRegistry, RegisteredHost
from daher_provisioning.models import DeployTarget


def a_registry() -> HostRegistry:
    registry = HostRegistry()
    registry.register("host-a", DeployTarget(host="10.0.0.5"))
    return registry


class TestRegistration:
    def test_registered_host_is_retrievable(self):
        registry = a_registry()
        assert registry.get("host-a").target.host == "10.0.0.5"

    def test_unknown_host_is_refused_with_the_reason(self):
        with pytest.raises(HostError, match="not registered"):
            a_registry().get("somewhere-else")

    def test_the_refusal_explains_why_targets_are_not_taken_from_requests(self):
        with pytest.raises(HostError, match="never taken from the request"):
            a_registry().get("attacker-chosen")

    @pytest.mark.parametrize("bad", ["AB", "x", "-lead", "trail-", "under_score", "x" * 65])
    def test_invalid_host_ids_are_refused(self, bad):
        with pytest.raises(HostError):
            HostRegistry().register(bad, DeployTarget(host="10.0.0.5"))

    def test_double_registration_is_refused(self):
        registry = a_registry()
        with pytest.raises(HostError, match="already registered"):
            registry.register("host-a", DeployTarget(host="10.0.0.6"))


class TestEnrolment:
    def test_a_host_that_has_not_enrolled_cannot_be_claimed(self):
        """The agent must have bootstrapped before work is assigned (ADR-022)."""
        registry = a_registry()
        with pytest.raises(HostError, match="has not enrolled"):
            registry.claim("host-a", "facility-a")

    def test_claiming_works_after_enrolment(self):
        registry = a_registry()
        registry.mark_enrolled("host-a")
        claimed = registry.claim("host-a", "facility-a")
        assert claimed.facility_id == "facility-a"


class TestOccupiedHostsAreNeverOverwritten:
    def test_second_facility_on_the_same_host_is_refused(self):
        registry = a_registry()
        registry.mark_enrolled("host-a")
        registry.claim("host-a", "facility-a")
        with pytest.raises(HostError, match="already runs facility"):
            registry.claim("host-a", "facility-b")

    def test_the_refusal_names_the_actual_risk(self):
        registry = a_registry()
        registry.mark_enrolled("host-a")
        registry.claim("host-a", "facility-a")
        with pytest.raises(HostError, match="existing patient database"):
            registry.claim("host-a", "facility-b")

    def test_the_original_binding_survives_the_refused_attempt(self):
        registry = a_registry()
        registry.mark_enrolled("host-a")
        registry.claim("host-a", "facility-a")
        with pytest.raises(HostError):
            registry.claim("host-a", "facility-b")
        assert registry.get("host-a").facility_id == "facility-a"


class TestListing:
    def test_lists_in_stable_order(self):
        registry = HostRegistry()
        for host_id in ("host-c", "host-a", "host-b"):
            registry.register(host_id, DeployTarget(host="10.0.0.5"))
        assert [h.host_id for h in registry.list()] == ["host-a", "host-b", "host-c"]

    def test_records_are_immutable(self):
        host = RegisteredHost(host_id="host-a", target=DeployTarget(host="10.0.0.5"))
        with pytest.raises(dataclasses.FrozenInstanceError):
            host.host_id = "other"
