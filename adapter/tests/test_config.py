from pathlib import Path

import pytest

from daher_adapter.config import DEPLOYMENT_TIMEZONE, ConfigurationError, Settings


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "TZ": "Africa/Djibouti",
        "ADAPTER_DB_HOST": "openelis-db",
        "ADAPTER_DB_PORT": "5432",
        "ADAPTER_DB_NAME": "adapter",
        "ADAPTER_DB_USER": "adapter",
        "ADAPTER_DB_PASSWORD": "not-a-real-secret",
        "ADAPTER_AUDIT_PATH": "/tmp/audit.jsonl",
    }
    values.update(overrides)
    return values


def test_settings_build_url_without_string_interpolation() -> None:
    settings = Settings.from_env(environment(ADAPTER_DB_PASSWORD="p@ss:/word"))

    assert settings.database_url.password == "p@ss:/word"
    assert settings.audit_path == Path("/tmp/audit.jsonl")
    assert settings.retry_max_attempts == 5


def test_settings_accept_any_real_timezone() -> None:
    """DHEALTH-FACILITY-PROFILE-001/ADR-074 replaced an equality check with a validity check.

    This test previously asserted the opposite -- that `TZ="UTC"` was **refused**, because
    `Settings.from_env` required `TZ == Africa/Djibouti`. That was coherent while one
    deployment-global timezone decided every facility's schedule. It is incoherent now that each
    facility carries its own: a deployment hosting a clinic in Vancouver would not have booted.

    The deployment timezone is still a real fact (log timestamps, the starting value for a newly
    created facility's profile), so it is still validated -- just no longer constrained to one
    value.
    """
    for name in ("UTC", "America/Vancouver", "Africa/Nairobi", DEPLOYMENT_TIMEZONE):
        assert Settings.from_env(environment(TZ=name)).timezone == name


def test_settings_refuse_a_timezone_that_is_not_one() -> None:
    """Validity is still enforced: an unparseable `TZ` is a configuration error worth refusing to
    boot on, and an **offset** is refused along with nonsense -- `UTC+3` carries no daylight-saving
    rule, so accepting it would be accepting a value that is wrong twice a year."""
    for name in ("Mars/Olympus_Mons", "UTC+3", "+03:00", "Not/A/Zone"):
        with pytest.raises(ConfigurationError, match="IANA timezone"):
            Settings.from_env(environment(TZ=name))


def test_settings_require_database_password() -> None:
    with pytest.raises(ConfigurationError, match="ADAPTER_DB_PASSWORD"):
        Settings.from_env(environment(ADAPTER_DB_PASSWORD=""))
