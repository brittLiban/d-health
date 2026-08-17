"""DH-027B — a failed facility creation must leave nothing behind.

The sequence is: resolve the host, claim it, insert the facility record, enqueue the job.
Claiming happens before the insert, so a failing insert used to leave the host marked
occupied by a facility that was never created. That host could then never be deployed to
again and an operator had to unpick it by hand.

These are failure-injection tests. Each one breaks a specific step and asserts on the state
left behind, not on the status code — a 409 with an orphaned claim behind it is still the bug.
"""

from __future__ import annotations

import pytest

from daher_provisioning.hosts import HostError, HostRegistry
from daher_provisioning.models import DeployTarget
from daher_provisioning.repository import FacilityExists, FacilityRepository
from daher_provisioning.store import Database, SqliteFacilityStore, SqliteHostStore

HOST = "host-a"
TARGET = DeployTarget(host="10.0.0.5")


def registered(registry):
    registry.register(HOST, TARGET)
    registry.mark_enrolled(HOST)
    return registry


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, tmp_path):
    """Both backends, because production uses SQLite and the tests used to use only memory.

    A compensation path that worked in memory and not in the durable store would be a fix
    that exists only in the test suite.
    """
    if request.param == "memory":
        return registered(HostRegistry()), FacilityRepository()
    db = Database(tmp_path / "state.sqlite")
    hosts = SqliteHostStore(db)
    hosts.register(HOST, TARGET)
    hosts.mark_enrolled(HOST)
    return hosts, SqliteFacilityStore(db)


class TestReleaseIsCompensationNotAnOperation:
    def test_a_claim_can_be_released_by_the_facility_that_holds_it(self, backend):
        hosts, _ = backend
        hosts.claim(HOST, "clinic-one")
        assert hosts.release(HOST, "clinic-one") is True
        assert hosts.get(HOST).facility_id is None

    def test_releasing_with_the_wrong_facility_id_changes_nothing(self, backend):
        """The control that stops compensation becoming an eviction tool. That host holds a
        patient database; a stale or hostile call must not free it."""
        hosts, _ = backend
        hosts.claim(HOST, "clinic-one")
        assert hosts.release(HOST, "someone-elses-facility") is False
        assert hosts.get(HOST).facility_id == "clinic-one", "the live claim must survive"

    def test_releasing_an_unclaimed_host_is_a_no_op(self, backend):
        hosts, _ = backend
        assert hosts.release(HOST, "clinic-one") is False

    def test_releasing_an_unknown_host_does_not_raise(self, backend):
        hosts, _ = backend
        assert hosts.release("no-such-host", "clinic-one") is False

    def test_a_released_host_can_be_claimed_again(self, backend):
        """The point of the whole exercise: the host is usable after a failed create."""
        hosts, _ = backend
        hosts.claim(HOST, "failed-facility")
        hosts.release(HOST, "failed-facility")
        assert hosts.claim(HOST, "second-attempt").facility_id == "second-attempt"


class TestTheOrphanThisTicketExistsToPrevent:
    def test_claim_then_failed_insert_leaves_the_host_free(self, backend):
        """Reproduces DH-027B's sequence directly against the stores."""
        hosts, facilities = backend
        from datetime import UTC, datetime

        from daher_provisioning.models import Facility, FacilityType

        facility = Facility(
            facility_id="clinic-one",
            display_name="Clinic One",
            type=FacilityType.HOSPITAL,
            deploy_target=TARGET,
            created_at=datetime.now(UTC),
        )
        facilities.add(facility)

        # A second create for the same id: the claim succeeds, the insert must not.
        hosts.release(HOST, "clinic-one")  # free the host as a fresh request would find it
        hosts.claim(HOST, "clinic-one")
        with pytest.raises(FacilityExists):
            facilities.add(facility)

        # Without compensation the host stays claimed here. With it, the host is reusable.
        assert hosts.release(HOST, "clinic-one") is True
        assert hosts.get(HOST).facility_id is None

    def test_an_occupied_host_still_refuses_a_second_facility(self, backend):
        """Compensation must not weaken the refusal it sits next to (ADR-022)."""
        hosts, _ = backend
        hosts.claim(HOST, "clinic-one")
        with pytest.raises(HostError) as raised:
            hosts.claim(HOST, "clinic-two")
        assert "already runs facility" in str(raised.value)


class TestThroughTheRealHttpApp:
    """The level the orphan actually manifests at, and where the audit log must agree."""

    @staticmethod
    def _wired(tmp_path, repository=None, hosts=None):
        from fastapi.testclient import TestClient

        from daher_provisioning.app import create_app
        from daher_provisioning.audit import AuditLog
        from daher_provisioning.auth import PORTAL_SCOPES, KeyStore, issue_key
        from daher_provisioning.ratelimit import TokenBucketLimiter

        key, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        log = AuditLog(tmp_path / "audit.jsonl")
        hosts = hosts if hosts is not None else registered(HostRegistry())
        app = create_app(
            key_store=KeyStore([record]),
            audit_log=log,
            limiter=TokenBucketLimiter(rate_per_second=100, burst=100),
            hosts=hosts,
            repository=repository,
        )
        return TestClient(app), key, hosts, log

    def _body(self, **over):
        body = {"display_name": "Clinic One", "type": "hospital", "host_id": HOST}
        body.update(over)
        return body

    def test_a_duplicate_facility_id_does_not_orphan_the_host_claim(self, tmp_path):
        client, key, hosts, _ = self._wired(tmp_path)
        head = {"Authorization": f"Bearer {key}"}

        first = client.post("/facilities", json=self._body(facility_id="clinic-one"), headers=head)
        assert first.status_code == 201
        claimed_by = hosts.get(HOST).facility_id
        assert claimed_by == "clinic-one"

        # Free the host so the second request gets past the occupied-host refusal and
        # reaches the insert — which is the step that must compensate.
        hosts.release(HOST, "clinic-one")
        second = client.post("/facilities", json=self._body(facility_id="clinic-one"), headers=head)

        assert second.status_code == 409
        assert hosts.get(HOST).facility_id is None, (
            "the host must NOT be left claimed by a facility that was never created"
        )

    def test_a_storage_failure_also_compensates_and_is_not_reported_as_success(self, tmp_path):
        """Not just FacilityExists: any failure that leaves the claim behind is the same bug."""

        class BrokenRepository(FacilityRepository):
            def add(self, facility):
                raise RuntimeError("disk on fire")

        client, key, hosts, log = self._wired(tmp_path, repository=BrokenRepository())
        response = client.post(
            "/facilities",
            json=self._body(facility_id="clinic-one"),
            headers={"Authorization": f"Bearer {key}"},
        )

        assert response.status_code == 500
        assert hosts.get(HOST).facility_id is None, "claim must be rolled back on any failure"

        entries = log.entries()
        outcomes = {e.outcome.value for e in entries if e.facility_id == "clinic-one"}
        assert "failure" in outcomes
        assert "success" not in outcomes, (
            "the audit log must never record a success for a create that did not happen"
        )
        assert any("rolled back" in (e.message or "") for e in entries)

    def test_the_audit_message_says_when_a_rollback_could_not_happen(self, tmp_path):
        """If compensation itself fails, that must be visible rather than silently assumed.

        An operator reading "rolled back" when nothing was rolled back would look in the
        wrong place. So the message distinguishes the two outcomes explicitly.
        """

        class BrokenRepository(FacilityRepository):
            def add(self, facility):
                raise RuntimeError("disk on fire")

        class UnreleasableHosts(HostRegistry):
            def release(self, host_id, facility_id):
                return False

        client, key, _, log = self._wired(
            tmp_path,
            repository=BrokenRepository(),
            hosts=registered(UnreleasableHosts()),
        )
        response = client.post(
            "/facilities",
            json=self._body(facility_id="clinic-one"),
            headers={"Authorization": f"Bearer {key}"},
        )

        assert response.status_code == 500
        messages = " ".join(e.message or "" for e in log.entries())
        assert "NOT rolled back" in messages, (
            "a failed compensation must say so; claiming a rollback that did not happen "
            "sends the operator looking in the wrong place"
        )
