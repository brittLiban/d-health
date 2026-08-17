"""DH-027E — decommission must stop the stack, not just relabel the record.

The old endpoint set `DECOMMISSIONED` on request and queued nothing, so the fleet reported a
facility as decommissioned while its stack was still serving patients. That is the same
"queued is not running" mistake the create path had already been fixed for, in the direction
that matters more: an operator who believes a stack is stopped stops watching it.

The property under test is not the status code. It is that the terminal state is reachable
only from an agent's successful STOP report, and that the host claim survives — because the
patient database is still on that host.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daher_provisioning.app import create_app, next_facility_state
from daher_provisioning.audit import AuditLog
from daher_provisioning.auth import PORTAL_SCOPES, KeyStore, Scope, issue_key
from daher_provisioning.hosts import HostRegistry
from daher_provisioning.jobs import JobVerb
from daher_provisioning.models import DeployTarget, FacilityState
from daher_provisioning.ratelimit import TokenBucketLimiter

HOST = "host-a"


@pytest.fixture
def wired(tmp_path):
    key, record = issue_key(
        "portal", actor="portal", scopes=PORTAL_SCOPES | {Scope.FACILITY_DECOMMISSION}
    )
    log = AuditLog(tmp_path / "audit.jsonl")
    hosts = HostRegistry()
    hosts.register(HOST, DeployTarget(host="10.0.0.5"))
    hosts.mark_enrolled(HOST)
    app = create_app(
        key_store=KeyStore([record]),
        audit_log=log,
        limiter=TokenBucketLimiter(rate_per_second=200, burst=200),
        hosts=hosts,
    )
    client = TestClient(app)
    head = {"Authorization": f"Bearer {key}"}
    client.post(
        "/facilities",
        json={
            "display_name": "Clinic One",
            "type": "hospital",
            "host_id": HOST,
            "facility_id": "clinic-one",
        },
        headers=head,
    )
    return client, head, hosts, log, app


def decommission(client, head):
    return client.post("/facilities/clinic-one/decommission", headers=head)


def state_of(client, head, facility_id="clinic-one"):
    """Read the state back through the API rather than from the store, so the test asserts
    what an operator would actually see. There is no GET /facilities/{id}; the list is the
    fleet view the portal renders."""
    listing = client.get("/facilities", headers=head).json()
    return next(f["state"] for f in listing if f["facility_id"] == facility_id)


class TestRequestingDecommission:
    def test_the_state_becomes_decommissioning_not_decommissioned(self, wired):
        """The whole point. Nothing has stopped yet, so nothing may claim it has."""
        client, head, *_ = wired
        response = decommission(client, head)
        assert response.status_code == 200
        assert response.json()["state"] == FacilityState.DECOMMISSIONING.value

    def test_a_stop_job_is_actually_queued_for_the_right_host(self, wired):
        client, head, _, _, app = wired
        decommission(client, head)
        stops = [
            record for record in app.state.queue._jobs.values() if record.job.verb is JobVerb.STOP
        ]
        assert len(stops) == 1, "a decommission that queues nothing stops nothing"
        assert stops[0].job.host_id == HOST
        assert stops[0].job.params.facility_id == "clinic-one"

    def test_the_host_claim_is_not_released(self, wired):
        """⚠️ The patient database is still on that host. Freeing the claim would let another
        facility be provisioned on top of this one's data — the overwrite ADR-022 refuses."""
        client, head, hosts, *_ = wired
        decommission(client, head)
        assert hosts.get(HOST).facility_id == "clinic-one"

    def test_an_unknown_facility_is_404(self, wired):
        client, head, *_ = wired
        assert client.post("/facilities/no-such/decommission", headers=head).status_code == 404

    def test_the_audit_says_the_stack_is_not_confirmed_stopped(self, wired):
        client, head, _, log, _ = wired
        decommission(client, head)
        messages = " ".join(e.message or "" for e in log.entries())
        assert "no data destroyed" in messages
        assert "until its agent reports the stack stopped" in messages


class TestOnlyAnAgentReportReachesTheTerminalState:
    @staticmethod
    def _agent_headers(app):
        """Enrol this host the way the real bootstrap does: the agent generates its own
        credential and the API only ever stores the hash (ADR-022)."""
        from daher_provisioning.enrolment import generate_agent_credential

        token, _record = app.state.enrolments.mint(HOST)
        secret, credential_hash = generate_agent_credential(HOST)
        app.state.enrolments.redeem(token, credential_hash=credential_hash)
        return {"Authorization": f"Bearer {secret}"}

    @classmethod
    def _claim_until(cls, client, agent, verb):
        """Claim jobs until the wanted verb appears. The create path queues a PROVISION
        first, so a STOP is never the first job in the queue."""
        for _ in range(10):
            body = client.get("/agent/jobs/claim", headers=agent).json()
            job = body.get("job")
            if job is None:
                break
            if job["verb"] == verb.value:
                return job
        raise AssertionError(f"no {verb} job was ever handed out")

    @classmethod
    def _claim_and_report(cls, client, app, status_text):
        agent = cls._agent_headers(app)
        job = cls._claim_until(client, agent, JobVerb.STOP)
        return client.post(
            f"/agent/jobs/{job['job_id']}/report",
            json={"status": status_text, "detail": "compose stop"},
            headers=agent,
        )

    def test_a_successful_stop_report_completes_the_decommission(self, wired):
        client, head, _, _, app = wired
        decommission(client, head)
        assert self._claim_and_report(client, app, "succeeded").status_code == 200
        state = state_of(client, head)
        assert state == FacilityState.DECOMMISSIONED.value

    def test_a_failed_stop_report_leaves_it_decommissioning(self, wired):
        """The honest state: the request was made, the stack was not confirmed stopped."""
        client, head, _, _, app = wired
        decommission(client, head)
        self._claim_and_report(client, app, "failed")
        state = state_of(client, head)
        assert state == FacilityState.DECOMMISSIONING.value, (
            "a stack that did not confirm stopping must not be reported as decommissioned"
        )

    def test_a_refused_stop_report_leaves_it_decommissioning(self, wired):
        client, head, _, _, app = wired
        decommission(client, head)
        self._claim_and_report(client, app, "refused")
        state = state_of(client, head)
        assert state == FacilityState.DECOMMISSIONING.value

    def test_a_successful_provision_report_does_not_retire_a_facility(self, wired):
        """Only a STOP moves the state. A PROVISION report must not retire anything, or a
        successful deployment would decommission the facility it just brought up."""
        client, head, _, _, app = wired
        agent = self._agent_headers(app)
        job = self._claim_until(client, agent, JobVerb.PROVISION)
        client.post(
            f"/agent/jobs/{job['job_id']}/report",
            json={"status": "succeeded", "detail": "up"},
            headers=agent,
        )
        state = state_of(client, head)
        assert state != FacilityState.DECOMMISSIONED.value


class TestTheLifecycleTable:
    """DH-027C. Every transition is driven by an agent report about work it actually ran —
    never by a job being queued. Tested directly against the table, because the table is the
    specification and a table nobody reads is where a wrong transition hides.
    """

    def test_a_successful_provision_report_starts_running(self):
        assert (
            next_facility_state(JobVerb.PROVISION, "succeeded", FacilityState.PROVISIONING)
            is FacilityState.RUNNING
        )

    @pytest.mark.parametrize("reported", ["failed", "refused"])
    def test_a_failed_provision_report_is_degraded_not_running(self, reported):
        assert (
            next_facility_state(JobVerb.PROVISION, reported, FacilityState.PROVISIONING)
            is FacilityState.DEGRADED
        )

    def test_a_successful_start_recovers_a_degraded_facility(self):
        assert (
            next_facility_state(JobVerb.START, "succeeded", FacilityState.DEGRADED)
            is FacilityState.RUNNING
        )

    def test_a_successful_stop_only_retires_from_decommissioning(self):
        assert (
            next_facility_state(JobVerb.STOP, "succeeded", FacilityState.DECOMMISSIONING)
            is FacilityState.DECOMMISSIONED
        )
        assert next_facility_state(JobVerb.STOP, "succeeded", FacilityState.RUNNING) is None, (
            "a STOP report for a facility nobody asked to retire must not retire it"
        )

    @pytest.mark.parametrize(
        "verb", [JobVerb.PROVISION, JobVerb.START, JobVerb.STOP, JobVerb.STATUS, JobVerb.BACKUP_NOW]
    )
    def test_decommissioned_is_terminal(self, verb):
        """A late or replayed report must never resurrect a retired facility."""
        assert next_facility_state(verb, "succeeded", FacilityState.DECOMMISSIONED) is None

    @pytest.mark.parametrize("verb", [JobVerb.STATUS, JobVerb.BACKUP_NOW, JobVerb.ROTATE_TLS, None])
    def test_verbs_that_do_not_change_lifecycle_change_nothing(self, verb):
        """A status check or a backup is not a lifecycle event. Reporting one must not move
        the facility, or reading status would rewrite the thing it reports."""
        for state in (FacilityState.PROVISIONING, FacilityState.RUNNING, FacilityState.DEGRADED):
            assert next_facility_state(verb, "succeeded", state) is None

    def test_a_provision_report_does_not_revive_a_decommissioning_facility(self):
        assert (
            next_facility_state(JobVerb.PROVISION, "succeeded", FacilityState.DECOMMISSIONING)
            is None
        )


class TestTheLifecycleThroughTheApi:
    def test_a_confirmed_provision_makes_the_facility_running(self, wired):
        """Before DH-027C nothing ever left PROVISIONING, so the fleet could never show a
        working facility."""
        client, head, _, _, app = wired
        assert state_of(client, head) == FacilityState.PROVISIONING.value

        agent = TestOnlyAnAgentReportReachesTheTerminalState._agent_headers(app)
        job = TestOnlyAnAgentReportReachesTheTerminalState._claim_until(
            client, agent, JobVerb.PROVISION
        )
        client.post(
            f"/agent/jobs/{job['job_id']}/report",
            json={"status": "succeeded", "detail": "up"},
            headers=agent,
        )
        assert state_of(client, head) == FacilityState.RUNNING.value

    def test_a_failed_provision_makes_it_degraded(self, wired):
        client, head, _, _, app = wired
        agent = TestOnlyAnAgentReportReachesTheTerminalState._agent_headers(app)
        job = TestOnlyAnAgentReportReachesTheTerminalState._claim_until(
            client, agent, JobVerb.PROVISION
        )
        client.post(
            f"/agent/jobs/{job['job_id']}/report",
            json={"status": "failed", "detail": "compose exited 1"},
            headers=agent,
        )
        assert state_of(client, head) == FacilityState.DEGRADED.value

    def test_the_state_change_appears_in_the_audit_log(self, wired):
        client, head, _, log, app = wired
        agent = TestOnlyAnAgentReportReachesTheTerminalState._agent_headers(app)
        job = TestOnlyAnAgentReportReachesTheTerminalState._claim_until(
            client, agent, JobVerb.PROVISION
        )
        client.post(
            f"/agent/jobs/{job['job_id']}/report",
            json={"status": "succeeded", "detail": "up"},
            headers=agent,
        )
        messages = " ".join(e.message or "" for e in log.entries())
        assert "provisioning → running" in messages

    def test_queueing_alone_never_makes_a_facility_running(self, wired):
        """The mistake the roadmap explicitly warns against."""
        client, head, *_ = wired
        assert state_of(client, head) == FacilityState.PROVISIONING.value, (
            "a queued PROVISION job is not a running stack"
        )
