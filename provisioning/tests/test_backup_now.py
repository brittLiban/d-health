"""BACKUP_NOW's structured evidence (DH-027G).

STATUS already learned this lesson (DH-027D): a command's exit code and "the thing being
observed is actually true" are different facts. BACKUP_NOW has a sharper version of the same
problem — "not configured" must be reported as exactly that, never silently attempted against
a compose profile with no credentials (which would just fail loudly and unhelpfully), and
never conflated with "attempted and failed".
"""

from __future__ import annotations

from datetime import UTC, datetime

from daher_agent.executor import CommandResult, ComposeExecutor
from daher_provisioning.facility_env import BackupSettings
from daher_provisioning.jobs import Job, JobParams, JobVerb
from daher_provisioning.models import BackupState


def a_job(facility_id: str = "daher-bc") -> Job:
    return Job(
        job_id="job-00000001",
        host_id="host-a",
        verb=JobVerb.BACKUP_NOW,
        params=JobParams(facility_id=facility_id),
        issued_at=datetime.now(UTC),
    )


def an_executor(tmp_path, *, backup=None, runner=None):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    facility_root = tmp_path / "facilities"
    (facility_root / "daher-bc").mkdir(parents=True)
    (facility_root / "daher-bc" / ".env").write_text("FACILITY_ID=daher-bc\n", encoding="utf-8")
    return ComposeExecutor(
        compose_file=compose,
        facility_root=facility_root,
        base_domain="example.org",
        runner=runner or (lambda argv, *, cwd, timeout: CommandResult(0, "ok", "")),
        backup=backup,
    )


def configured() -> BackupSettings:
    return BackupSettings(passphrase="x" * 32, s3_bucket="daher-backups")


class TestNotConfigured:
    def test_no_settings_at_all_is_not_configured(self, tmp_path):
        executor = an_executor(tmp_path, backup=None)
        result = executor.execute(a_job())
        assert result.succeeded, "the agent correctly determined the state; that is success"
        assert result.backup.state is BackupState.NOT_CONFIGURED

    def test_a_passphrase_with_no_destination_is_not_configured(self, tmp_path):
        executor = an_executor(tmp_path, backup=BackupSettings(passphrase="x" * 32, s3_bucket=""))
        result = executor.execute(a_job())
        assert result.backup.state is BackupState.NOT_CONFIGURED

    def test_a_destination_with_no_passphrase_is_not_configured(self, tmp_path):
        executor = an_executor(
            tmp_path, backup=BackupSettings(passphrase="", s3_bucket="daher-backups")
        )
        result = executor.execute(a_job())
        assert result.backup.state is BackupState.NOT_CONFIGURED

    def test_not_configured_never_touches_the_command_runner(self, tmp_path):
        """The control that matters most: an unconfigured host must never attempt
        `docker compose --profile backup run --rm backup` at all — not run it and let it
        fail, which would be a confusing error instead of a clear, honest state."""
        calls = []

        def spy(argv, *, cwd, timeout):
            calls.append(argv)
            return CommandResult(0, "", "")

        executor = an_executor(tmp_path, backup=None, runner=spy)
        executor.execute(a_job())
        assert calls == []

    def test_the_reason_names_adr_025(self, tmp_path):
        executor = an_executor(tmp_path, backup=None)
        result = executor.execute(a_job())
        assert "ADR-025" in result.backup.reason


class TestARealAttempt:
    def test_success_reports_success(self, tmp_path):
        executor = an_executor(
            tmp_path,
            backup=configured(),
            runner=lambda argv, *, cwd, timeout: CommandResult(0, "backup ok", ""),
        )
        result = executor.execute(a_job())
        assert result.succeeded
        assert result.backup.state is BackupState.SUCCESS

    def test_the_command_is_the_documented_manual_run(self, tmp_path):
        calls = []

        def spy(argv, *, cwd, timeout):
            calls.append(argv)
            return CommandResult(0, "", "")

        executor = an_executor(tmp_path, backup=configured(), runner=spy)
        executor.execute(a_job())
        assert calls, "a configured host must actually attempt the backup"
        argv = calls[0]
        assert argv[-5:] == ["--profile", "backup", "run", "--rm", "backup"]

    def test_a_nonzero_exit_is_failed_not_not_configured(self, tmp_path):
        executor = an_executor(
            tmp_path,
            backup=configured(),
            runner=lambda argv, *, cwd, timeout: CommandResult(1, "", "disk full"),
        )
        result = executor.execute(a_job())
        assert not result.succeeded
        assert result.backup.state is BackupState.FAILED
        assert result.backup.state is not BackupState.NOT_CONFIGURED

    def test_a_timeout_is_failed(self, tmp_path):
        import subprocess

        def hangs(argv, *, cwd, timeout):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

        executor = an_executor(tmp_path, backup=configured(), runner=hangs)
        result = executor.execute(a_job())
        assert not result.succeeded
        assert result.backup.state is BackupState.FAILED
        assert "timed out" in result.backup.reason

    def test_an_execution_error_is_failed(self, tmp_path):
        def explodes(argv, *, cwd, timeout):
            raise OSError("docker not found")

        executor = an_executor(tmp_path, backup=configured(), runner=explodes)
        result = executor.execute(a_job())
        assert not result.succeeded
        assert result.backup.state is BackupState.FAILED

    def test_the_timeout_bound_is_the_backup_bound_not_the_stack_bound(self, tmp_path):
        seen = {}

        def spy(argv, *, cwd, timeout):
            seen["timeout"] = timeout
            return CommandResult(0, "", "")

        executor = an_executor(tmp_path, backup=configured(), runner=spy)
        executor.execute(a_job())
        assert seen["timeout"] == executor.backup_timeout_seconds


class TestNoSecretEverLeavesTheExecutor:
    def test_success_detail_carries_no_configured_value(self, tmp_path):
        """`detail` is truncated stdout — proving it stays clean of the values this
        executor actually held (the passphrase, the bucket) rather than merely asserting
        the model has no such field (that is `test_no_clinical_data.py`'s job)."""
        settings = configured()
        executor = an_executor(
            tmp_path,
            backup=settings,
            runner=lambda argv, *, cwd, timeout: CommandResult(0, "backup 20260101 SUCCESS", ""),
        )
        result = executor.execute(a_job())
        assert settings.passphrase not in result.detail
        assert settings.passphrase not in (result.backup.reason or "")

    def test_a_failure_reason_carries_no_configured_value(self, tmp_path):
        settings = configured()
        executor = an_executor(
            tmp_path,
            backup=settings,
            runner=lambda argv, *, cwd, timeout: CommandResult(1, "", "auth failed"),
        )
        result = executor.execute(a_job())
        assert settings.passphrase not in result.detail
        assert settings.passphrase not in (result.backup.reason or "")
