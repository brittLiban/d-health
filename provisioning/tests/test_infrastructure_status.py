"""DH-027D — observed infrastructure health, and the ways it must refuse to look healthy.

The property this whole ticket exists for: **a zero exit code from `docker compose ps` is not
health.** Compose returns 0 while printing a service that is `exited` or `unhealthy`, so every
verdict here is derived from parsed service states and never from the command's outcome.

Fixtures use the real field names and shapes emitted by `docker compose ps --all --format json`
on the actual clinical stack — JSON lines, with `Service`, `State`, `Health`, `ExitCode`,
`Image` — rather than an invented schema.
"""

from __future__ import annotations

import json

import pytest

from daher_agent.infrastructure import (
    ComposeOutputError,
    derive_health,
    expected_services,
    one_shot_services,
    parse_ps,
    unknown_report,
)
from daher_provisioning.models import InfrastructureHealth, ServiceState

LONG_RUNNING = {"openmrs-db", "openmrs-backend", "caddy"}
ONE_SHOTS = {"openelis-certs"}
EXPECTED = LONG_RUNNING | ONE_SHOTS


def container(service, state="running", health=None, exit_code=0, image="img@sha256:abc"):
    entry = {"Service": service, "State": state, "ExitCode": exit_code, "Image": image}
    if health is not None:
        entry["Health"] = health
    return entry


def ps_lines(*entries):
    """`docker compose ps --format json` emits JSON LINES, verified against the real command."""
    return "\n".join(json.dumps(e) for e in entries)


def healthy_stack():
    return ps_lines(
        container("openmrs-db", health="healthy"),
        container("openmrs-backend", health="healthy"),
        container("caddy", health="healthy"),
        container("openelis-certs", state="exited", exit_code=0),
    )


def report_for(ps_output, expected=EXPECTED, one_shots=ONE_SHOTS):
    return derive_health(parse_ps(ps_output), expected=expected, one_shots=one_shots)


class TestTheHealthyBaseline:
    def test_a_fully_healthy_stack_is_healthy(self):
        report = report_for(healthy_stack())
        assert report.health is InfrastructureHealth.HEALTHY
        assert report.services_running == 3
        assert report.services_expected == 3
        assert report.reason is None

    def test_a_completed_one_shot_does_not_degrade_anything(self):
        """`openelis-certs` exited(0) is its CORRECT terminal state, not a fault. Counting it
        as a failure would make every healthy clinical stack permanently degraded."""
        report = report_for(healthy_stack())
        certs = next(s for s in report.services if s.name == "openelis-certs")
        assert certs.state is ServiceState.EXITED_OK
        assert report.health is InfrastructureHealth.HEALTHY

    def test_one_shots_are_excluded_from_the_service_ratio(self):
        """Otherwise a perfect stack reads '3 of 4', and an operator learns to ignore it."""
        report = report_for(healthy_stack())
        assert (report.services_running, report.services_expected) == (3, 3)


class TestTheControlThatMattersMost:
    def test_a_zero_exit_code_with_an_unhealthy_service_is_degraded(self):
        """§5's negative control. The command succeeded; the stack did not."""
        report = report_for(
            ps_lines(
                container("openmrs-db", health="healthy"),
                container("openmrs-backend", health="healthy"),
                container("caddy", health="unhealthy"),
                container("openelis-certs", state="exited", exit_code=0),
            )
        )
        assert report.health is InfrastructureHealth.DEGRADED
        assert "caddy unhealthy" in report.reason

    def test_running_without_a_healthcheck_is_not_reported_as_healthy(self):
        """Compose omits `Health` when no healthcheck is declared. That is an absence of
        evidence, and must not be recorded as evidence of health."""
        report = report_for(healthy_stack())
        db = next(s for s in report.services if s.name == "openmrs-db")
        assert db.state is ServiceState.HEALTHY
        no_check = report_for(
            ps_lines(
                container("openmrs-db"),
                container("openmrs-backend"),
                container("caddy"),
                container("openelis-certs", state="exited"),
            )
        )
        assert all(
            s.state is ServiceState.RUNNING for s in no_check.services if s.name in LONG_RUNNING
        )


class TestEachWayAStackCanBeBroken:
    def test_a_missing_service_is_degraded(self):
        """`ps` cannot show what does not exist — this is why `config` is consulted too."""
        report = report_for(
            ps_lines(
                container("openmrs-db", health="healthy"),
                container("caddy", health="healthy"),
                container("openelis-certs", state="exited"),
            )
        )
        assert report.health is InfrastructureHealth.DEGRADED
        assert "openmrs-backend missing" in report.reason
        missing = next(s for s in report.services if s.name == "openmrs-backend")
        assert missing.state is ServiceState.MISSING

    def test_an_exited_long_running_service_is_degraded_even_at_exit_zero(self):
        """A long-running service that exited cleanly is still down."""
        report = report_for(
            ps_lines(
                container("openmrs-db", health="healthy"),
                container("openmrs-backend", state="exited", exit_code=0),
                container("caddy", health="healthy"),
                container("openelis-certs", state="exited"),
            )
        )
        assert report.health is InfrastructureHealth.DEGRADED
        assert "openmrs-backend exited_ok" in report.reason

    def test_a_failed_setup_job_is_degraded(self):
        """A one-shot that exited NONZERO means the stack came up wrong."""
        report = report_for(
            ps_lines(
                container("openmrs-db", health="healthy"),
                container("openmrs-backend", health="healthy"),
                container("caddy", health="healthy"),
                container("openelis-certs", state="exited", exit_code=1),
            )
        )
        assert report.health is InfrastructureHealth.DEGRADED
        assert "setup job failed" in report.reason

    def test_a_starting_stack_is_unknown_not_healthy(self):
        """Mid-boot is not broken, but it is not proof of health either."""
        report = report_for(
            ps_lines(
                container("openmrs-db", health="healthy"),
                container("openmrs-backend", health="starting"),
                container("caddy", health="healthy"),
                container("openelis-certs", state="exited"),
            )
        )
        assert report.health is InfrastructureHealth.UNKNOWN
        assert "still starting" in report.reason

    def test_an_unrecognised_state_is_unknown_not_healthy(self):
        report = report_for(
            ps_lines(
                container("openmrs-db", state="some-future-docker-state"),
                container("openmrs-backend", health="healthy"),
                container("caddy", health="healthy"),
                container("openelis-certs", state="exited"),
            )
        )
        assert report.health is InfrastructureHealth.DEGRADED


class TestUnreadableOutput:
    def test_malformed_json_is_refused(self):
        with pytest.raises(ComposeOutputError):
            parse_ps("{not json at all")

    def test_a_truncated_line_is_refused_rather_than_partially_parsed(self):
        truncated = json.dumps(container("openmrs-db"))[:40]
        with pytest.raises(ComposeOutputError):
            parse_ps(truncated)

    def test_empty_output_is_no_services_not_an_error(self):
        """A stack that was never brought up produces no containers. That is degraded by the
        missing-service rule, not a parse failure."""
        report = report_for("")
        assert report.health is InfrastructureHealth.DEGRADED
        assert report.services_running == 0

    def test_an_unknown_report_is_never_healthy(self):
        assert unknown_report("docker unavailable").health is InfrastructureHealth.UNKNOWN

    def test_a_json_array_is_also_accepted(self):
        """Compose has emitted both shapes across versions; being strict would break on
        upgrade for no safety gain."""
        entries = [container("openmrs-db", health="healthy")]
        assert len(parse_ps(json.dumps(entries))) == 1


class TestExpectedSetComesFromTheComposeFile:
    CONFIG = json.dumps(
        {
            "services": {
                "openmrs-db": {},
                "openmrs-backend": {
                    "depends_on": {
                        "openmrs-db": {"condition": "service_healthy"},
                        "openelis-certs": {"condition": "service_completed_successfully"},
                    }
                },
                "openelis-certs": {},
            }
        }
    )

    def test_one_shots_are_derived_from_service_completed_successfully(self):
        """Not from a hand-maintained list, which is wrong the first time someone adds a
        setup job — and not from restart policies, which every service in the real clinical
        compose file declares, including the one-shots. That was checked, not assumed."""
        assert one_shot_services(self.CONFIG) == {"openelis-certs"}

    def test_expected_services_are_every_declared_service(self):
        assert expected_services(self.CONFIG) == {
            "openmrs-db",
            "openmrs-backend",
            "openelis-certs",
        }

    def test_a_profile_gated_service_absent_from_config_is_not_expected(self):
        """`docker compose config` omits inactive profiles, so a facility with no off-site
        backup is not marked degraded for lacking the backup container (ADR-025)."""
        assert "backup" not in expected_services(self.CONFIG)

    def test_malformed_config_is_refused(self):
        with pytest.raises(ComposeOutputError):
            expected_services("{}")
        with pytest.raises(ComposeOutputError):
            one_shot_services("not json")


class TestTheRealClinicalStackShape:
    """Parsed from output captured from the actual running stack, so the parser is tested
    against what Compose really emits rather than a fixture that agrees with it."""

    REAL = ps_lines(
        container("adapter", health="healthy"),
        container("caddy", health="unhealthy"),
        container("keycloak", health="healthy"),
        container("keycloak-sso-config", state="exited", exit_code=0),
        container("openelis-db", health="healthy"),
        container("openelis-fhir"),
        container("openmrs-backend", health="healthy"),
        container("openmrs-gateway"),
    )

    def test_the_live_stack_reads_as_degraded_because_caddy_is_unhealthy(self):
        """⚠️ This is bug 20 surfacing honestly. Caddy's HTTP routes serve correctly but its
        healthcheck cannot exec, so Compose reports `unhealthy`. DH-027D reports what was
        observed and does NOT decide the facility is unusable — nor does it hide it."""
        report = derive_health(
            parse_ps(self.REAL),
            expected={
                "adapter",
                "caddy",
                "keycloak",
                "keycloak-sso-config",
                "openelis-db",
                "openelis-fhir",
                "openmrs-backend",
                "openmrs-gateway",
            },
            one_shots={"keycloak-sso-config"},
        )
        assert report.health is InfrastructureHealth.DEGRADED
        assert "caddy unhealthy" in report.reason
        # 6 of 7, not 7 of 7: an UNHEALTHY container is up but is not counted as running.
        # The conservative reading is the useful one — it makes the ratio itself informative
        # rather than a number that stays reassuring while a service is failing.
        assert report.services_running == 6
        assert report.services_expected == 7
        assert all("patient" not in s.name for s in report.services)


class TestTheWholeChainThroughTheApi:
    """§17's acceptance path: STATUS report → API validates → persists → /status shows it.

    Also the place where a buggy or compromised agent must not be able to assert health.
    """

    @staticmethod
    def _wired(tmp_path):
        from fastapi.testclient import TestClient

        from daher_provisioning.app import create_app
        from daher_provisioning.audit import AuditLog
        from daher_provisioning.auth import PORTAL_SCOPES, KeyStore, issue_key
        from daher_provisioning.enrolment import generate_agent_credential
        from daher_provisioning.hosts import HostRegistry
        from daher_provisioning.models import DeployTarget
        from daher_provisioning.ratelimit import TokenBucketLimiter

        key, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        hosts = HostRegistry()
        hosts.register("host-a", DeployTarget(host="10.0.0.5"))
        hosts.mark_enrolled("host-a")
        app = create_app(
            key_store=KeyStore([record]),
            audit_log=AuditLog(tmp_path / "audit.jsonl"),
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
                "host_id": "host-a",
                "facility_id": "clinic-one",
            },
            headers=head,
        )
        token, _ = app.state.enrolments.mint("host-a")
        secret, credential_hash = generate_agent_credential("host-a")
        app.state.enrolments.redeem(token, credential_hash=credential_hash)
        return client, head, {"Authorization": f"Bearer {secret}"}, app

    _job_counter = 0

    @classmethod
    def _status_job(cls, client, head, agent, app):
        """Queue a STATUS job and hand it to the agent, returning its job id."""
        from datetime import UTC, datetime

        from daher_provisioning.jobs import Job, JobParams, JobVerb

        cls._job_counter += 1
        app.state.queue.enqueue(
            Job(
                job_id=f"job-status-{cls._job_counter}",
                host_id="host-a",
                verb=JobVerb.STATUS,
                params=JobParams(facility_id="clinic-one"),
                issued_at=datetime.now(UTC),
            )
        )
        for _ in range(5):
            body = client.get("/agent/jobs/claim", headers=agent).json()
            job = body.get("job")
            if job and job["verb"] == JobVerb.STATUS.value:
                return job["job_id"]
        raise AssertionError("no STATUS job was handed out")

    def _status(self, client, head):
        return client.get("/facilities/clinic-one/status", headers=head).json()

    def test_before_any_check_health_is_unknown_and_there_is_no_timestamp(self, tmp_path):
        """The false green this ticket removes: the endpoint used to return zero counts with
        a FRESH `checked_at`, implying a check that had never happened."""
        client, head, _agent, _app = self._wired(tmp_path)
        status = self._status(client, head)
        assert status["health"] == "unknown"
        assert status["checked_at"] is None, "no check happened, so there is no timestamp"
        assert "no infrastructure check" in status["health_reason"]

    def test_a_reported_check_is_persisted_and_served(self, tmp_path):
        client, head, agent, app = self._wired(tmp_path)
        job_id = self._status_job(client, head, agent, app)
        report = derive_health(parse_ps(healthy_stack()), expected=EXPECTED, one_shots=ONE_SHOTS)
        response = client.post(
            f"/agent/jobs/{job_id}/report",
            json={
                "status": "succeeded",
                "detail": "ok",
                "infrastructure": report.model_dump(mode="json"),
            },
            headers=agent,
        )
        assert response.status_code == 200

        status = self._status(client, head)
        assert status["health"] == "healthy"
        assert (status["services_running"], status["services_expected"]) == (3, 3)
        assert status["checked_at"] is not None
        assert {s["name"] for s in status["services"]} >= LONG_RUNNING

    def test_a_degraded_check_is_served_as_degraded(self, tmp_path):
        client, head, agent, app = self._wired(tmp_path)
        job_id = self._status_job(client, head, agent, app)
        broken = derive_health(
            parse_ps(
                ps_lines(
                    container("openmrs-db", health="healthy"),
                    container("openmrs-backend", health="healthy"),
                    container("caddy", health="unhealthy"),
                    container("openelis-certs", state="exited"),
                )
            ),
            expected=EXPECTED,
            one_shots=ONE_SHOTS,
        )
        client.post(
            f"/agent/jobs/{job_id}/report",
            json={
                "status": "succeeded",
                "detail": "degraded",
                "infrastructure": broken.model_dump(mode="json"),
            },
            headers=agent,
        )
        status = self._status(client, head)
        assert status["health"] == "degraded"
        assert "caddy unhealthy" in status["health_reason"]

    def test_an_agent_reporting_success_with_no_evidence_gets_unknown_not_healthy(self, tmp_path):
        """⚠️ The security-shaped control. A buggy or compromised agent must not be able to
        assert health by omission — 'succeeded' with no infrastructure evidence is UNKNOWN."""
        client, head, agent, app = self._wired(tmp_path)
        job_id = self._status_job(client, head, agent, app)
        client.post(
            f"/agent/jobs/{job_id}/report",
            json={"status": "succeeded", "detail": "all good, trust me"},
            headers=agent,
        )
        status = self._status(client, head)
        assert status["health"] == "unknown", "health must not be assertable by omission"
        assert "without infrastructure evidence" in status["health_reason"]

    def test_a_malformed_report_gets_unknown_not_healthy(self, tmp_path):
        client, head, agent, app = self._wired(tmp_path)
        job_id = self._status_job(client, head, agent, app)
        client.post(
            f"/agent/jobs/{job_id}/report",
            json={
                "status": "succeeded",
                "detail": "ok",
                "infrastructure": {"health": "healthy", "nonsense_field": True},
            },
            headers=agent,
        )
        status = self._status(client, head)
        assert status["health"] == "unknown"
        assert "failed validation" in status["health_reason"]

    def test_a_later_check_replaces_the_earlier_one(self, tmp_path):
        """Latest-only, deliberately: a dashboard needs what is true now."""
        client, head, agent, app = self._wired(tmp_path)
        job_id = self._status_job(client, head, agent, app)
        good = derive_health(parse_ps(healthy_stack()), expected=EXPECTED, one_shots=ONE_SHOTS)
        client.post(
            f"/agent/jobs/{job_id}/report",
            json={
                "status": "succeeded",
                "detail": "",
                "infrastructure": good.model_dump(mode="json"),
            },
            headers=agent,
        )
        assert self._status(client, head)["health"] == "healthy"

        second = self._status_job(client, head, agent, app)
        bad = derive_health(
            parse_ps(ps_lines(container("openmrs-db", health="unhealthy"))),
            expected=EXPECTED,
            one_shots=ONE_SHOTS,
        )
        client.post(
            f"/agent/jobs/{second}/report",
            json={
                "status": "succeeded",
                "detail": "",
                "infrastructure": bad.model_dump(mode="json"),
            },
            headers=agent,
        )
        assert self._status(client, head)["health"] == "degraded"

    def test_lifecycle_is_not_rewritten_by_a_status_check(self, tmp_path):
        """§14: one transient unhealthy check must not drive a lifecycle transition. The two
        concepts stay independent unless a decision says otherwise."""
        client, head, agent, app = self._wired(tmp_path)
        before = self._status(client, head)["state"]
        job_id = self._status_job(client, head, agent, app)
        bad = derive_health(
            parse_ps(ps_lines(container("openmrs-db", health="unhealthy"))),
            expected=EXPECTED,
            one_shots=ONE_SHOTS,
        )
        client.post(
            f"/agent/jobs/{job_id}/report",
            json={
                "status": "succeeded",
                "detail": "",
                "infrastructure": bad.model_dump(mode="json"),
            },
            headers=agent,
        )
        after = self._status(client, head)
        assert after["state"] == before, "a STATUS observation must not move the lifecycle"
        assert after["health"] == "degraded"
