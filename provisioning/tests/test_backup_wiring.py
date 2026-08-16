"""Per-facility backup wiring (ADR-025).

The build prompt asks that a facility created through the API gets a scheduled backup
automatically. The tests here are mostly about the case where it *cannot* — because a
scheduler that crash-loops, or one that quietly writes local-only archives beside the data
it is protecting, are both worse than a facility that reports having no backup.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daher_provisioning.facility_env import BackupSettings, FacilityEnvError, render_env
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
