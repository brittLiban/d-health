from pathlib import Path

import pytest

from daher_adapter.config import ConfigurationError, Settings


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


def test_settings_refuse_wrong_timezone() -> None:
    with pytest.raises(ConfigurationError, match="Africa/Djibouti"):
        Settings.from_env(environment(TZ="UTC"))


def test_settings_require_database_password() -> None:
    with pytest.raises(ConfigurationError, match="ADAPTER_DB_PASSWORD"):
        Settings.from_env(environment(ADAPTER_DB_PASSWORD=""))
