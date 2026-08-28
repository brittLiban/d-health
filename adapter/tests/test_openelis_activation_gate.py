"""DH-011/BLOCKER-6 §15-16: the site-code activation gate. `OPENELIS_SITE_CODE` is optional --
it does not exist yet -- but once supplied it must be genuinely numeric and never the shipped
upstream placeholder. `Settings.service_request_delivery_ready` is the one config-derived
activation gate; the other §16 gates (patient identifier policy, requester approval, test
mapping, the DH-011D correlation patch being live-proven) are process/documentation state, not
environment values, and are deliberately not represented here.
"""

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


def test_missing_site_code_is_none_not_an_error() -> None:
    settings = Settings.from_env(environment())

    assert settings.openelis_site_code is None
    assert settings.service_request_delivery_ready is False


def test_dev01_placeholder_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="placeholder"):
        Settings.from_env(environment(OPENELIS_SITE_CODE="DEV01"))


def test_non_numeric_site_code_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="numeric"):
        Settings.from_env(environment(OPENELIS_SITE_CODE="DJ001"))


def test_a_genuine_numeric_site_code_is_accepted_and_activation_ready() -> None:
    # Not a real facility value -- a synthetic numeric string only shaped like one, used to
    # prove the gate opens for a well-formed value and never to stand in for NEEDS LIBAN #7.
    settings = Settings.from_env(environment(OPENELIS_SITE_CODE="19"))

    assert settings.openelis_site_code == "19"
    assert settings.service_request_delivery_ready is True


def test_settings_still_builds_without_the_new_field_breaking_existing_behavior() -> None:
    settings = Settings.from_env(environment())

    assert settings.audit_path == Path("/tmp/audit.jsonl")
    assert settings.retry_max_attempts == 5
