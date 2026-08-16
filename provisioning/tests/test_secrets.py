"""Per-facility secret generation (ADR-021).

The properties that matter: every facility gets different credentials, each respects the
constraints the receiving system actually enforces, and no secret value can leak through a
repr into a log or stack trace.
"""

from __future__ import annotations

import dataclasses

import pytest

from daher_provisioning.secrets import (
    OPENELIS_SPECIALS,
    FacilitySecrets,
    SecretGenerationError,
    generate_facility_secrets,
    openelis_admin_password,
    openmrs_admin_password,
    validate_facility_id,
)


class TestFacilityId:
    def test_valid_ids_pass(self):
        for value in ("daher-bc", "facility-001", "abc"):
            assert validate_facility_id(value) == value

    @pytest.mark.parametrize(
        "value",
        ["Daher", "ab", "-leading", "trailing-", "has_underscore", "x" * 41, "spaces here"],
    )
    def test_invalid_ids_are_refused(self, value):
        with pytest.raises(SecretGenerationError):
            validate_facility_id(value)

    def test_error_explains_that_the_id_is_permanent(self):
        with pytest.raises(SecretGenerationError, match="cannot be changed once volumes exist"):
            validate_facility_id("BAD")


class TestOpenELISConstraints:
    """OpenELIS silently rejects passwords outside its alphabet — generate inside it."""

    def test_uses_only_the_accepted_alphabet(self):
        for _ in range(50):
            password = openelis_admin_password()
            assert all(c.isalnum() or c in OPENELIS_SPECIALS for c in password)

    def test_always_contains_a_required_special(self):
        for _ in range(50):
            assert any(c in OPENELIS_SPECIALS for c in openelis_admin_password())

    def test_short_lengths_are_refused(self):
        with pytest.raises(SecretGenerationError):
            openelis_admin_password(length=6)


class TestOpenMRSConstraints:
    def test_satisfies_complexity(self):
        for _ in range(50):
            password = openmrs_admin_password()
            assert any(c.islower() for c in password)
            assert any(c.isupper() for c in password)
            assert any(c.isdigit() for c in password)

    def test_never_contains_the_username(self):
        for _ in range(50):
            assert "admin" not in openmrs_admin_password().lower()


class TestFacilitySecretSet:
    def test_covers_every_variable_the_stack_requires(self):
        secrets = generate_facility_secrets("test-facility")
        required = {
            "OMRS_DB_PASSWORD",
            "OMRS_DB_ROOT_PASSWORD",
            "OMRS_ADMIN_PASSWORD",
            "OE_DB_PASSWORD",
            "OE_DB_SUPERUSER_PASSWORD",
            "OE_POSTGRES_PASSWORD",
            "OE_ADMIN_PASSWORD",
            "OE_KEYSTORE_PASSWORD",
            "OE_TRUSTSTORE_PASSWORD",
            "KEYCLOAK_DB_PASSWORD",
            "KEYCLOAK_ADMIN_PASSWORD",
            "ADAPTER_DB_PASSWORD",
            "BACKUP_ENCRYPTION_PASSPHRASE",
        }
        assert required <= set(secrets.values)

    def test_no_secret_is_reused_within_a_facility(self):
        values = list(generate_facility_secrets("test-facility").values.values())
        assert len(values) == len(set(values))

    def test_two_facilities_never_share_a_secret(self):
        a = generate_facility_secrets("facility-a").values
        b = generate_facility_secrets("facility-b").values
        assert set(a.values()).isdisjoint(set(b.values()))

    def test_keystore_password_is_never_the_published_default(self):
        """DH-021: an empty or defaulted value silently selected upstream's kspass."""
        secrets = generate_facility_secrets("test-facility").values
        assert secrets["OE_KEYSTORE_PASSWORD"] not in ("", "kspass")
        assert secrets["OE_TRUSTSTORE_PASSWORD"] not in ("", "tspass")

    def test_admin_password_is_never_the_published_default(self):
        secrets = generate_facility_secrets("test-facility").values
        assert secrets["OMRS_ADMIN_PASSWORD"] != "Admin123"
        assert secrets["OE_ADMIN_PASSWORD"] != "adminADMIN!"


class TestSecretsNeverLeakThroughRepr:
    """A default dataclass repr would print every secret into any stack trace."""

    def test_repr_redacts_values(self):
        secrets = generate_facility_secrets("test-facility")
        rendered = repr(secrets)
        assert "redacted" in rendered
        for value in secrets.values.values():
            assert value not in rendered

    def test_str_redacts_values(self):
        secrets = generate_facility_secrets("test-facility")
        rendered = str(secrets)
        for value in secrets.values.values():
            assert value not in rendered

    def test_repr_still_identifies_the_facility(self):
        assert "test-facility" in repr(generate_facility_secrets("test-facility"))

    def test_formatting_into_a_log_line_does_not_leak(self):
        secrets = generate_facility_secrets("test-facility")
        line = f"provisioned {secrets}"
        for value in secrets.values.values():
            assert value not in line


def test_invalid_facility_id_generates_nothing():
    with pytest.raises(SecretGenerationError):
        generate_facility_secrets("INVALID")


def test_secrets_object_is_frozen():
    secrets = generate_facility_secrets("test-facility")
    with pytest.raises(dataclasses.FrozenInstanceError):
        secrets.facility_id = "other"  # type: ignore[misc]


def test_dataclass_shape_is_stable():
    assert isinstance(generate_facility_secrets("test-facility"), FacilitySecrets)
