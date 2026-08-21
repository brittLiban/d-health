"""STATUS now observes scheduled backup evidence alongside infrastructure health (DH-027H).

`_status` composes `_observe_infrastructure` (DH-027D, unit-tested in
`test_infrastructure_status.py`/`test_executor.py`) with the new `_observe_backup`. These
tests are about the composition and the backup half specifically — not re-testing infra
health, which is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from daher_agent.executor import BACKUP_STATUS_PATH, CommandResult, ComposeExecutor
from daher_provisioning.facility_env import BackupSettings
from daher_provisioning.jobs import Job, JobParams, JobVerb
from daher_provisioning.models import BackupState, InfrastructureHealth


def a_status_job(facility_id: str = "daher-bc") -> Job:
    return Job(
        job_id="job-00000001",
        host_id="host-a",
        verb=JobVerb.STATUS,
        params=JobParams(facility_id=facility_id),
        issued_at=datetime.now(UTC),
    )


def configured() -> BackupSettings:
    return BackupSettings(passphrase="x" * 32, s3_bucket="daher-backups")


def an_executor(tmp_path, *, backup=None, runner):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    return ComposeExecutor(
        compose_file=compose,
        facility_root=tmp_path / "facilities",
        base_domain="example.org",
        runner=runner,
        backup=backup,
    )


def compose_ok(*extra):
    """A runner that answers `config`/`ps` for one healthy long-running service, so
    `_observe_infrastructure` reports HEALTHY and each test is free to vary only the
    backup-observation leg."""
    calls = []

    def runner(argv, *, cwd, timeout):
        calls.append(argv)
        joined = " ".join(argv)
        if joined.endswith("config --format json"):
            return CommandResult(0, '{"services": {"web": {}}}', "")
        if joined.endswith("ps --all --format json"):
            return CommandResult(
                0, '{"Service": "web", "State": "running", "Health": "healthy"}', ""
            )
        for matcher, result in extra:
            if matcher(argv):
                return result
        return CommandResult(1, "", "unexpected call")

    return runner, calls


class TestNotConfiguredNeverTouchesTheContainer:
    def test_no_backup_settings_reports_not_configured(self, tmp_path):
        runner, calls = compose_ok()
        executor = an_executor(tmp_path, backup=None, runner=runner)
        result = executor.execute(a_status_job())
        assert result.backup.state is BackupState.NOT_CONFIGURED
        assert not any("exec" in c for c in calls), (
            "an unconfigured host must never attempt to exec into a container that was "
            "never started because the backup profile was never active"
        )

    def test_partial_settings_also_report_not_configured(self, tmp_path):
        runner, _ = compose_ok()
        executor = an_executor(
            tmp_path, backup=BackupSettings(passphrase="x" * 32, s3_bucket=""), runner=runner
        )
        result = executor.execute(a_status_job())
        assert result.backup.state is BackupState.NOT_CONFIGURED


class TestAConfiguredHostReadsTheRealMarker:
    def test_the_exact_documented_read_only_command_is_used(self, tmp_path):
        """Matches `backup/README.md`'s own documented manual check exactly — this is not a
        new capability, it is the agent automating an already-documented read."""
        runner, calls = compose_ok(
            (lambda argv: "exec" in argv, CommandResult(0, "SUCCESS 20260821T020000Z\n", ""))
        )
        executor = an_executor(tmp_path, backup=configured(), runner=runner)
        executor.execute(a_status_job())
        exec_calls = [c for c in calls if "exec" in c]
        assert exec_calls, "a configured host must actually attempt the read"
        argv = exec_calls[0]
        assert argv[-2:] == ["cat", BACKUP_STATUS_PATH]
        assert "-T" in argv, "no TTY allocation in a non-interactive agent context"
        assert "backup" in argv

    def test_a_success_marker_is_reported_with_its_own_timestamp(self, tmp_path):
        runner, _ = compose_ok(
            (lambda argv: "exec" in argv, CommandResult(0, "SUCCESS 20260821T020000Z\n", ""))
        )
        executor = an_executor(tmp_path, backup=configured(), runner=runner)
        result = executor.execute(a_status_job())
        assert result.backup.state is BackupState.SUCCESS
        assert result.backup.checked_at == datetime(2026, 8, 21, 2, 0, 0, tzinfo=UTC)

    def test_a_failed_marker_is_reported_as_failed(self, tmp_path):
        """Control-shaped: the exec command itself exits 0 (it successfully read the file),
        but the file's own content says FAILED — the report must say FAILED, not SUCCESS."""
        runner, _ = compose_ok(
            (lambda argv: "exec" in argv, CommandResult(0, "FAILED 20260821T020000Z\n", ""))
        )
        executor = an_executor(tmp_path, backup=configured(), runner=runner)
        result = executor.execute(a_status_job())
        assert result.backup.state is BackupState.FAILED

    def test_the_file_not_existing_yet_is_never_run_not_a_crash(self, tmp_path):
        """Configured, but no backup has actually run yet — `cat` exits nonzero."""
        runner, _ = compose_ok(
            (lambda argv: "exec" in argv, CommandResult(1, "", "cat: No such file or directory"))
        )
        executor = an_executor(tmp_path, backup=configured(), runner=runner)
        result = executor.execute(a_status_job())
        assert result.backup.state is BackupState.NEVER_RUN

    def test_malformed_content_is_never_run_not_success(self, tmp_path):
        runner, _ = compose_ok(
            (lambda argv: "exec" in argv, CommandResult(0, "not a real status line", ""))
        )
        executor = an_executor(tmp_path, backup=configured(), runner=runner)
        result = executor.execute(a_status_job())
        assert result.backup.state is BackupState.NEVER_RUN
        assert result.backup.state is not BackupState.SUCCESS

    def test_empty_output_is_never_run(self, tmp_path):
        runner, _ = compose_ok((lambda argv: "exec" in argv, CommandResult(0, "", "")))
        executor = an_executor(tmp_path, backup=configured(), runner=runner)
        result = executor.execute(a_status_job())
        assert result.backup.state is BackupState.NEVER_RUN

    def test_a_command_timeout_is_never_run(self, tmp_path):
        import subprocess

        def runner(argv, *, cwd, timeout):
            if "exec" in argv:
                raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
            if "config" in argv:
                return CommandResult(0, '{"services": {}}', "")
            return CommandResult(0, "", "")

        executor = an_executor(tmp_path, backup=configured(), runner=runner)
        result = executor.execute(a_status_job())
        assert result.backup.state is BackupState.NEVER_RUN


class TestInfrastructureAndBackupAreIndependent:
    def test_infrastructure_health_is_unaffected_by_an_unconfigured_backup(self, tmp_path):
        """An unconfigured backup profile must never make the *infrastructure* observation
        look degraded — DH-027D's `config` call already excludes inactive-profile services
        from the expected set; this proves the composition in `_status` preserves that."""
        runner, _ = compose_ok()
        executor = an_executor(tmp_path, backup=None, runner=runner)
        result = executor.execute(a_status_job())
        assert result.infrastructure.health is InfrastructureHealth.HEALTHY
        assert result.backup.state is BackupState.NOT_CONFIGURED

    def test_a_failed_backup_marker_does_not_degrade_infrastructure_health(self, tmp_path):
        runner, _ = compose_ok(
            (lambda argv: "exec" in argv, CommandResult(0, "FAILED 20260821T020000Z\n", ""))
        )
        executor = an_executor(tmp_path, backup=configured(), runner=runner)
        result = executor.execute(a_status_job())
        assert result.infrastructure.health is InfrastructureHealth.HEALTHY, (
            "backup and infrastructure are different concerns and must not be collapsed"
        )
        assert result.backup.state is BackupState.FAILED


class TestNoSecretEverLeavesTheObservation:
    def test_the_report_carries_no_configured_value(self, tmp_path):
        settings = configured()
        runner, _ = compose_ok(
            (lambda argv: "exec" in argv, CommandResult(0, "SUCCESS 20260821T020000Z\n", ""))
        )
        executor = an_executor(tmp_path, backup=settings, runner=runner)
        result = executor.execute(a_status_job())
        assert settings.passphrase not in (result.backup.reason or "")
        assert settings.passphrase not in result.detail
