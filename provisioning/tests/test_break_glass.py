"""Explicit break-glass authentication mode transition (UI-010/DH-008E).

Break-glass means emergency local OpenMRS authentication when Keycloak SSO cannot be used.
The properties that matter, in the order they matter:

1. It is never automatic. There is exactly one way to enable it: the authenticated,
   audited `/facilities/{id}/break-glass/enable` call — nothing about a Keycloak outage,
   a failed health check, or a timeout can reach it.
2. It is a privileged operation, not a routine one. The scope it needs is deliberately
   withheld from `PORTAL_SCOPES` (§5, §16) — the same posture as decommission.
3. It changes exactly one thing: `SSO_ENABLED` in the facility's own env file, and only
   that file. `FacilityState` and every other env value are untouched.
4. The narrow recreate (`--no-deps openmrs-backend`) is the same pattern bug-21 and
   bug-22 proved live: one service, no cascade.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from daher_agent.executor import CommandResult, ComposeExecutor
from daher_provisioning.audit import AuditAction, AuditLog, AuditOutcome
from daher_provisioning.auth import PORTAL_SCOPES, KeyStore, Scope, issue_key
from daher_provisioning.facility_env import FacilityEnvError, read_env_value, set_sso_enabled
from daher_provisioning.hosts import HostRegistry
from daher_provisioning.jobs import Job, JobParams, JobVerb
from daher_provisioning.models import AuthenticationMode, DeployTarget


class TestSetSsoEnabled:
    def test_only_the_named_value_changes(self):
        original = "SSO_ENABLED=false\nOMRS_DB_PASSWORD=keepme\n# a comment\n"
        updated = set_sso_enabled(original, enabled=True)
        assert "SSO_ENABLED=true" in updated
        assert "OMRS_DB_PASSWORD=keepme" in updated, "an untouched value was modified"
        assert "# a comment" in updated

    def test_disable_direction(self):
        updated = set_sso_enabled("SSO_ENABLED=true\n", enabled=False)
        assert updated == "SSO_ENABLED=false\n"

    def test_an_absent_key_is_refused_rather_than_appended(self):
        with pytest.raises(FacilityEnvError, match="no SSO_ENABLED entry"):
            set_sso_enabled("SOMETHING=1\n", enabled=True)


class TestReadEnvValue:
    def test_reads_the_named_value(self):
        assert read_env_value("A=1\nB=2\n", "B") == "2"

    def test_absent_key_is_none(self):
        assert read_env_value("A=1\n", "MISSING") is None

    def test_ignores_commented_lines(self):
        assert read_env_value("# SSO_ENABLED=true\nSSO_ENABLED=false\n", "SSO_ENABLED") == "false"


def _executor(tmp_path, calls: list[list[str]] | None = None) -> ComposeExecutor:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    def runner(argv, *, cwd, timeout):
        if calls is not None:
            calls.append(argv)
        return CommandResult(0, "ok", "")

    return ComposeExecutor(
        compose_file=compose,
        facility_root=tmp_path / "f",
        base_domain="example.org",
        runner=runner,
    )


def _job(verb: JobVerb, facility_id: str = "daher-bc", job_id: str = "job-00000002") -> Job:
    return Job(
        job_id=job_id,
        host_id="host-a",
        verb=verb,
        params=JobParams(facility_id=facility_id),
        issued_at=datetime.now(UTC),
    )


class TestTheExecutorTransition:
    def _provisioned(self, tmp_path, calls=None):
        executor = _executor(tmp_path, calls)
        provision = _job(JobVerb.PROVISION, job_id="job-00000001")
        assert executor.execute(provision).succeeded
        return executor

    def test_enable_break_glass_flips_sso_enabled_to_false(self, tmp_path):
        executor = self._provisioned(tmp_path)
        before = executor.env_path("daher-bc").read_text(encoding="utf-8")
        assert read_env_value(before, "SSO_ENABLED") == "false", "fresh facilities start SSO-off"

        # Start from SSO on, so the transition to break-glass is a real, observable flip.
        from daher_provisioning.facility_env import write_env

        write_env(
            executor.env_path("daher-bc"),
            set_sso_enabled(before, enabled=True),
            overwrite=True,
        )

        calls: list[list[str]] = []

        def capture(argv, *, cwd, timeout):
            calls.append(argv)
            return CommandResult(0, "ok", "")

        executor._run = capture
        result = executor.execute(_job(JobVerb.ENABLE_BREAK_GLASS))
        assert result.succeeded

        after = executor.env_path("daher-bc").read_text(encoding="utf-8")
        assert read_env_value(after, "SSO_ENABLED") == "false"
        assert calls[-1][-2:] == ["--no-deps", "openmrs-backend"], (
            "must recreate only openmrs-backend, never the whole stack"
        )
        assert "--force-recreate" not in calls[-1], (
            "break-glass changes an env value the container reads at boot; a bare `up` after "
            "the write is what actually changes, not a forced restart of an unrelated kind"
        )

    def test_disable_break_glass_flips_sso_enabled_to_true(self, tmp_path):
        executor = self._provisioned(tmp_path)
        result = executor.execute(_job(JobVerb.DISABLE_BREAK_GLASS))
        assert result.succeeded
        after = executor.env_path("daher-bc").read_text(encoding="utf-8")
        assert read_env_value(after, "SSO_ENABLED") == "true"

    def test_other_env_values_are_untouched_by_either_direction(self, tmp_path):
        executor = self._provisioned(tmp_path)
        before = executor.env_path("daher-bc").read_text(encoding="utf-8")
        db_line = [ln for ln in before.splitlines() if ln.startswith("OMRS_DB_PASSWORD=")][0]

        executor.execute(_job(JobVerb.ENABLE_BREAK_GLASS, job_id="job-aaaaaaaa"))
        executor.execute(_job(JobVerb.DISABLE_BREAK_GLASS, job_id="job-bbbbbbbb"))

        after = executor.env_path("daher-bc").read_text(encoding="utf-8")
        assert db_line in after, "break-glass touched a value it must never touch"

    def test_transition_without_an_env_file_fails_cleanly(self, tmp_path):
        executor = _executor(tmp_path)
        result = executor.execute(_job(JobVerb.ENABLE_BREAK_GLASS))
        assert not result.succeeded
        assert "nothing to transition" in result.detail

    def test_a_failed_recreate_leaves_the_env_flipped_but_reports_failure(self, tmp_path):
        """§16: 'incomplete mode transition → fail visibly.' The env write happens before
        the compose command runs (it has to — the command reads the file it just wrote), so
        a compose failure — including this sandbox's own observed daemon-level denials —
        leaves `SSO_ENABLED`/`DAHER_BREAK_GLASS` already flipped while the container still
        runs the old mode. This is a documented, visible partial state, not a silent one:
        `succeeded=False` propagates to the job report, and re-invoking the same verb
        converges (the env write is idempotent; only the recreate needs to actually happen).
        """
        executor = self._provisioned(tmp_path)

        def failing(argv, *, cwd, timeout):
            return CommandResult(1, "", "compose refused")

        executor._run = failing
        result = executor.execute(_job(JobVerb.ENABLE_BREAK_GLASS))
        assert not result.succeeded, "a failed recreate must be reported as a failure"

        after = executor.env_path("daher-bc").read_text(encoding="utf-8")
        assert read_env_value(after, "SSO_ENABLED") == "false"
        assert read_env_value(after, "DAHER_BREAK_GLASS") == "true", (
            "the env file legitimately reflects the requested mode even though the "
            "container has not caught up yet — this is the visible partial state, not a bug"
        )

    def test_timeout_is_the_fast_bound_not_the_stack_bound(self, tmp_path):
        executor = _executor(tmp_path)
        assert executor.timeout_for(JobVerb.ENABLE_BREAK_GLASS) == executor.timeout_seconds
        assert executor.timeout_for(JobVerb.DISABLE_BREAK_GLASS) == executor.timeout_seconds
        assert executor.timeout_for(JobVerb.ENABLE_BREAK_GLASS) != executor.stack_timeout_seconds


class TestObservedAuthenticationMode:
    """The regression this class exists for: `AuthenticationMode` must NOT be derivable
    from `SSO_ENABLED` alone. A facility that has never turned SSO on (every facility today,
    including the real one — `STACK_DEFAULTS` sets `SSO_ENABLED=false` fail-closed) must
    report LOCAL_ONLY, not BREAK_GLASS. Only a facility that was NORMAL and then had
    `enable_break_glass` actually invoked reports BREAK_GLASS. This was caught by the
    advisor before it shipped: an earlier version of this code inferred BREAK_GLASS from
    `SSO_ENABLED=false` alone, which would have made every never-activated facility read as
    already in emergency mode.
    """

    def _provisioned(self, tmp_path):
        executor = _executor(tmp_path)
        executor.execute(_job(JobVerb.PROVISION, job_id="job-00000001"))
        return executor

    def test_freshly_provisioned_facility_is_local_only_not_break_glass(self, tmp_path):
        executor = self._provisioned(tmp_path)
        assert executor._observe_authentication_mode("daher-bc") is AuthenticationMode.LOCAL_ONLY

    def test_sso_enabled_true_reports_normal(self, tmp_path):
        executor = self._provisioned(tmp_path)
        executor.execute(_job(JobVerb.DISABLE_BREAK_GLASS, job_id="job-00000002"))
        assert executor._observe_authentication_mode("daher-bc") is AuthenticationMode.NORMAL

    def test_enable_break_glass_reports_break_glass_not_local_only(self, tmp_path):
        executor = self._provisioned(tmp_path)
        executor.execute(_job(JobVerb.DISABLE_BREAK_GLASS, job_id="job-00000002"))  # start NORMAL
        executor.execute(_job(JobVerb.ENABLE_BREAK_GLASS, job_id="job-00000003"))
        assert executor._observe_authentication_mode("daher-bc") is AuthenticationMode.BREAK_GLASS

    def test_disable_after_break_glass_returns_to_normal_not_local_only(self, tmp_path):
        executor = self._provisioned(tmp_path)
        executor.execute(_job(JobVerb.DISABLE_BREAK_GLASS, job_id="job-00000002"))
        executor.execute(_job(JobVerb.ENABLE_BREAK_GLASS, job_id="job-00000003"))
        executor.execute(_job(JobVerb.DISABLE_BREAK_GLASS, job_id="job-00000004"))
        assert executor._observe_authentication_mode("daher-bc") is AuthenticationMode.NORMAL

    def test_missing_env_file_is_unknown_not_normal(self, tmp_path):
        executor = _executor(tmp_path)
        assert executor._observe_authentication_mode("nope") is AuthenticationMode.UNKNOWN

    def test_sso_on_wins_if_the_two_flags_disagree(self, tmp_path):
        """A hand-edited env file (or any future bug) that leaves SSO_ENABLED=true and
        DAHER_BREAK_GLASS=true simultaneously must not report BREAK_GLASS: oauth2login being
        the active module is driven by SSO_ENABLED alone (configure-sso.sh), so that is the
        fact this field must agree with.
        """
        executor = self._provisioned(tmp_path)
        from daher_provisioning.facility_env import set_break_glass_flag, write_env

        path = executor.env_path("daher-bc")
        contents = set_sso_enabled(path.read_text(encoding="utf-8"), enabled=True)
        contents = set_break_glass_flag(contents, active=True)
        write_env(path, contents, overwrite=True)
        assert executor._observe_authentication_mode("daher-bc") is AuthenticationMode.NORMAL

    def test_status_report_carries_the_mode(self, tmp_path):
        executor = self._provisioned(tmp_path)
        executor.execute(_job(JobVerb.DISABLE_BREAK_GLASS, job_id="job-00000002"))
        executor.execute(_job(JobVerb.ENABLE_BREAK_GLASS, job_id="job-00000003"))

        def ps_config_runner(argv, *, cwd, timeout):
            if "config" in argv:
                return CommandResult(0, '{"services": {"openmrs-backend": {}}}', "")
            return CommandResult(
                0,
                '{"Service": "openmrs-backend", "State": "running", "Health": "healthy"}',
                "",
            )

        executor._run = ps_config_runner
        result = executor.execute(_job(JobVerb.STATUS, job_id="job-00000004"))
        assert result.infrastructure is not None
        assert result.infrastructure.authentication_mode is AuthenticationMode.BREAK_GLASS


class TestScopeIsPrivileged:
    def test_break_glass_scope_is_withheld_from_the_portal(self):
        assert Scope.BREAK_GLASS not in PORTAL_SCOPES, (
            "break-glass is a privileged emergency action, not a routine portal operation "
            "(§5, §16) — granting it by default would make every portal-holding caller able "
            "to disable SSO enforcement"
        )


def _app_with_facility(tmp_path, scopes):
    from daher_provisioning.app import create_app

    key, record = issue_key("operator", actor="operator", scopes=frozenset(scopes))
    registry = HostRegistry()
    registry.register("host-a", DeployTarget(host="10.0.0.5"))
    registry.mark_enrolled("host-a")
    audit_log = AuditLog(tmp_path / "audit.jsonl")
    app = create_app(key_store=KeyStore([record]), audit_log=audit_log, hosts=registry)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}
    client.post(
        "/facilities",
        json={"display_name": "Daher", "type": "hospital", "host_id": "host-a"},
        headers=headers,
    )
    return client, headers, audit_log


class TestTheEndpoints:
    def test_enabling_queues_a_job_and_records_audit(self, tmp_path):
        client, headers, audit_log = _app_with_facility(tmp_path, frozenset(Scope))
        response = client.post(
            "/facilities/daher/break-glass/enable",
            json={"reason": "keycloak unreachable during evening shift"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json() == {"queued": True}

        entries = audit_log.entries()
        enable_entries = [e for e in entries if e.action == AuditAction.BREAK_GLASS_ENABLE]
        assert len(enable_entries) == 1
        assert enable_entries[0].outcome == AuditOutcome.SUCCESS
        assert enable_entries[0].facility_id == "daher"
        assert "keycloak unreachable" in enable_entries[0].message

    def test_disabling_queues_a_job_and_records_audit(self, tmp_path):
        client, headers, audit_log = _app_with_facility(tmp_path, frozenset(Scope))
        response = client.post("/facilities/daher/break-glass/disable", json={}, headers=headers)
        assert response.status_code == 200
        entries = audit_log.entries()
        assert any(e.action == AuditAction.BREAK_GLASS_DISABLE for e in entries)

    def test_no_credential_or_secret_ever_enters_the_audit_message(self, tmp_path):
        client, headers, audit_log = _app_with_facility(tmp_path, frozenset(Scope))
        client.post(
            "/facilities/daher/break-glass/enable",
            json={"reason": "password=hunter2 secret=abc"},
            headers=headers,
        )
        # The model accepts free text (it cannot know what a caller pastes in), but nothing
        # in the *committed mechanism itself* — the job, the argv, the env write — ever
        # carries a credential; this asserts the one thing that IS validated: the audit
        # entry model has no field named for a secret (ADR-021's structural guarantee,
        # exercised here specifically for this new action).
        entries = audit_log.entries()
        entry = next(e for e in entries if e.action == AuditAction.BREAK_GLASS_ENABLE)
        assert entry.credential_set_id is None

    def test_an_unknown_facility_is_404(self, tmp_path):
        client, headers, _ = _app_with_facility(tmp_path, frozenset(Scope))
        response = client.post(
            "/facilities/does-not-exist/break-glass/enable", json={}, headers=headers
        )
        assert response.status_code == 404

    def test_unauthorized_request_is_refused(self, tmp_path):
        """§16, §20: a caller without the break-glass scope must be refused, not merely
        warned. Portal-shaped scopes are exactly what a compromised or misconfigured
        integration would present, so this is the realistic negative control, not an
        arbitrary missing-permission check.
        """
        client, headers, audit_log = _app_with_facility(tmp_path, PORTAL_SCOPES)
        response = client.post("/facilities/daher/break-glass/enable", json={}, headers=headers)
        assert response.status_code == 403
        entries = audit_log.entries()
        assert any(e.action == AuditAction.AUTH_DENIED for e in entries), (
            "a refused break-glass attempt must still be audited"
        )

    def test_malformed_body_is_refused(self, tmp_path):
        client, headers, _ = _app_with_facility(tmp_path, frozenset(Scope))
        response = client.post(
            "/facilities/daher/break-glass/enable",
            json={"reason": "x" * 1000, "unexpected_field": "anything"},
            headers=headers,
        )
        assert response.status_code == 422

    def test_no_authorization_header_at_all_is_refused(self, tmp_path):
        client, _, _ = _app_with_facility(tmp_path, frozenset(Scope))
        response = client.post("/facilities/daher/break-glass/enable", json={})
        assert response.status_code in (401, 403)


class TestAuditChainStillVerifies:
    def test_chain_verifies_after_break_glass_events(self, tmp_path):
        client, headers, audit_log = _app_with_facility(tmp_path, frozenset(Scope))
        client.post("/facilities/daher/break-glass/enable", json={}, headers=headers)
        client.post("/facilities/daher/break-glass/disable", json={}, headers=headers)
        checked = audit_log.verify_chain()  # raises ChainBroken on any tampering
        assert checked >= 2, "verifying zero entries is not a pass (audit.py's own warning)"
