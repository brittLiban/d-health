"""Per-facility backup wiring (ADR-025).

The build prompt asks that a facility created through the API gets a scheduled backup
automatically. The tests here are mostly about the case where it *cannot* — because a
scheduler that crash-loops, or one that quietly writes local-only archives beside the data
it is protecting, are both worse than a facility that reports having no backup.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daher_agent.executor import CommandResult, ComposeExecutor
from daher_provisioning.facility_env import BackupSettings, FacilityEnvError, render_env
from daher_provisioning.jobs import Job, JobParams, JobVerb
from daher_provisioning.models import DeployTarget, Facility, FacilityType
from daher_provisioning.secrets import generate_facility_secrets


def a_facility() -> Facility:
    return Facility(
        facility_id="daher-bc",
        display_name="Daher",
        type=FacilityType.HOSPITAL,
        deploy_target=DeployTarget(host="10.0.0.5"),
        created_at=datetime.now(UTC),
    )


def rendered(backup=None) -> str:
    return render_env(
        a_facility(),
        generate_facility_secrets("daher-bc"),
        base_domain="example.org",
        backup=backup,
    )


def complete() -> BackupSettings:
    return BackupSettings(
        passphrase="a-long-operator-supplied-passphrase", s3_bucket="daher-backups"
    )


class TestBackupIsScheduledOnlyWhenItCanSucceed:
    def test_a_complete_configuration_enables_the_scheduler(self):
        text = rendered(complete())
        assert "COMPOSE_PROFILES=backup" in text
        assert "BACKUP_S3_BUCKET=daher-backups" in text
        assert "BACKUP_REQUIRE_OFFSITE=true" in text

    def test_no_configuration_means_no_scheduler(self):
        """DH-022: an unconfigured scheduler restart-loops. Do not create that at fleet scale."""
        text = rendered(None)
        assert "COMPOSE_PROFILES=backup" not in text

    def test_a_passphrase_with_no_destination_is_not_schedulable(self):
        """Local-only archives beside the data are not a backup; they only look like one."""
        settings = BackupSettings(passphrase="x" * 32, s3_bucket="")
        assert not settings.schedulable
        assert "COMPOSE_PROFILES=backup" not in rendered(settings)

    def test_a_destination_with_no_passphrase_is_not_schedulable(self):
        settings = BackupSettings(passphrase="", s3_bucket="daher-backups")
        assert not settings.schedulable

    def test_asking_for_env_from_an_incomplete_configuration_is_refused(self):
        with pytest.raises(FacilityEnvError, match="cannot succeed"):
            BackupSettings(passphrase="", s3_bucket="").as_env()

    def test_the_refusal_names_both_halves(self):
        with pytest.raises(FacilityEnvError, match="off-site destination"):
            BackupSettings(passphrase="x" * 32, s3_bucket="").as_env()


class TestThePassphraseComesFromTheOperator:
    def test_it_is_not_generated_for_the_facility(self):
        """ADR-025: a key stored only on the backed-up machine cannot survive losing it."""
        assert "BACKUP_ENCRYPTION_PASSPHRASE" not in generate_facility_secrets("daher-bc").values

    def test_an_unconfigured_facility_has_no_passphrase_at_all(self):
        assert "BACKUP_ENCRYPTION_PASSPHRASE=" not in rendered(None)

    def test_a_configured_facility_uses_the_supplied_value(self):
        text = rendered(complete())
        assert "BACKUP_ENCRYPTION_PASSPHRASE=a-long-operator-supplied-passphrase" in text


def a_provision_job(facility_id: str = "daher-bc") -> Job:
    return Job(
        job_id="job-00000001",
        host_id="host-a",
        verb=JobVerb.PROVISION,
        params=JobParams(facility_id=facility_id),
        issued_at=datetime.now(UTC),
    )


class TestTheExecutorActuallyWiresItThrough:
    """DH-027G. `BackupSettings`/`render_env` were fully built and unit-tested (above), but
    `ComposeExecutor._provision()` never passed `backup=` to `render_env` — so a real
    provisioning run wrote an env file with no backup section no matter what the operator
    had configured on the host. That gap, not the rendering logic, is what this class proves
    closed: it runs the *real* executor, the same object `execute()` uses in production, and
    reads back the file it actually wrote.
    """

    def test_a_configured_host_writes_a_schedulable_env_file(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "facilities",
            base_domain="example.org",
            runner=lambda argv, *, cwd, timeout: CommandResult(0, "", ""),
            backup=complete(),
        )
        result = executor.execute(a_provision_job())
        assert result.succeeded

        written = (tmp_path / "facilities" / "daher-bc" / ".env").read_text(encoding="utf-8")
        assert "COMPOSE_PROFILES=backup" in written
        assert "BACKUP_S3_BUCKET=daher-backups" in written
        assert "BACKUP_ENCRYPTION_PASSPHRASE=a-long-operator-supplied-passphrase" in written

    def test_an_unconfigured_host_writes_no_backup_section(self, tmp_path):
        """The regression this class exists for: before the fix, this test would have
        passed too, for the wrong reason — `self.backup` was never read at all."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "facilities",
            base_domain="example.org",
            runner=lambda argv, *, cwd, timeout: CommandResult(0, "", ""),
        )
        assert executor.backup is None
        result = executor.execute(a_provision_job())
        assert result.succeeded

        written = (tmp_path / "facilities" / "daher-bc" / ".env").read_text(encoding="utf-8")
        assert "COMPOSE_PROFILES=backup" not in written
        assert "BACKUP_ENCRYPTION_PASSPHRASE=" not in written

    def test_a_partially_configured_host_also_writes_no_backup_section(self, tmp_path):
        """Half a configuration is not a backup (ADR-025) — proven here through the same
        executor path a real deployment uses, not just `BackupSettings.schedulable`."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "facilities",
            base_domain="example.org",
            runner=lambda argv, *, cwd, timeout: CommandResult(0, "", ""),
            backup=BackupSettings(passphrase="x" * 32, s3_bucket=""),
        )
        result = executor.execute(a_provision_job())
        assert result.succeeded

        written = (tmp_path / "facilities" / "daher-bc" / ".env").read_text(encoding="utf-8")
        assert "COMPOSE_PROFILES=backup" not in written
