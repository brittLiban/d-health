"""Per-facility secret generation.

Every facility gets its own credentials. None is ever reused across facilities, and none
is ever returned inline in an API response, written to the audit log, or included in an
error message — responses carry a *reference*, never a value (ADR-021).

The generators here encode constraints the clinical stack actually enforces, discovered by
running it rather than by reading documentation:

- OpenELIS accepts only letters, digits and ``* $ # !`` in its admin password, needs at
  least seven characters, and at least one of those specials. A general base64 generator
  produces values it silently rejects.
- OpenMRS enforces complexity (upper, lower, digit) and refuses a password containing the
  username.
- ``FACILITY_ID`` becomes a Docker Compose project name, so it is restricted to lowercase
  alphanumerics and hyphens.
"""

from __future__ import annotations

import re
import secrets as _secrets
import string
from dataclasses import dataclass, field

# Compose lowercases and restricts project names; anything else fails at `up` time with a
# message that does not obviously point back to this value.
FACILITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")

# OpenELIS's documented accepted specials. Verified against its own validator, not guessed.
OPENELIS_SPECIALS = "*$#!"

_ALNUM = string.ascii_letters + string.digits


class SecretGenerationError(ValueError):
    """A requested secret cannot be generated within the target system's constraints."""


def _rand(alphabet: str, length: int) -> str:
    return "".join(_secrets.choice(alphabet) for _ in range(length))


def general_secret(length: int = 40) -> str:
    """A high-entropy secret for anything without a restrictive alphabet."""
    if length < 24:
        raise SecretGenerationError("refusing to generate a secret shorter than 24 characters")
    return _secrets.token_urlsafe(length)[:length]


def openmrs_admin_password(length: int = 24) -> str:
    """OpenMRS requires upper, lower and a digit, and rejects the username as a substring."""
    if length < 12:
        raise SecretGenerationError("OpenMRS admin password must be at least 12 characters")
    while True:
        candidate = _rand(_ALNUM, length)
        if (
            any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
            and any(c.isdigit() for c in candidate)
            and "admin" not in candidate.lower()
        ):
            return candidate


def openelis_admin_password(length: int = 24) -> str:
    """OpenELIS's alphabet is narrower than a general generator's; respect it or be rejected."""
    if length < 7:
        raise SecretGenerationError("OpenELIS admin password must be at least 7 characters")
    alphabet = _ALNUM + OPENELIS_SPECIALS
    while True:
        candidate = _rand(alphabet, length)
        if any(c in OPENELIS_SPECIALS for c in candidate) and any(c.isalnum() for c in candidate):
            return candidate


def validate_facility_id(facility_id: str) -> str:
    """A facility id is an identity, not a display name, and it is permanent (DH-026)."""
    if not FACILITY_ID_PATTERN.match(facility_id):
        raise SecretGenerationError(
            f"facility id {facility_id!r} must be 3-40 characters of lowercase letters, "
            f"digits and hyphens, starting and ending alphanumeric — it becomes a Docker "
            f"Compose project name and cannot be changed once volumes exist"
        )
    return facility_id


@dataclass(frozen=True)
class FacilitySecrets:
    """The full credential set for one facility.

    ⚠️ Never log, serialise, or return this object. It exists to be written once into a
    facility's env file and then referenced by id. ``__repr__`` is overridden because the
    default dataclass repr would print every secret into any stack trace that touches it.
    """

    facility_id: str
    values: dict[str, str] = field(repr=False)

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load-bearing
        return (
            f"FacilitySecrets(facility_id={self.facility_id!r}, "
            f"values=<{len(self.values)} redacted>)"
        )

    def __str__(self) -> str:  # pragma: no cover - same reason
        return self.__repr__()


def generate_facility_secrets(facility_id: str) -> FacilitySecrets:
    """Generate every credential one facility's clinical stack needs.

    The names match the clinical stack's `.env` contract exactly. A missing name here is a
    stack that fails at `up` with a `${VAR:?}` guard, which is the intended loud failure
    rather than a silent default.
    """
    validate_facility_id(facility_id)

    values = {
        # OpenMRS / MariaDB
        "OMRS_DB_PASSWORD": general_secret(),
        "OMRS_DB_ROOT_PASSWORD": general_secret(),
        "OMRS_ADMIN_PASSWORD": openmrs_admin_password(),
        # OpenELIS / PostgreSQL — three genuinely separate credentials (ADR-001)
        "OE_DB_PASSWORD": general_secret(),
        "OE_DB_SUPERUSER_PASSWORD": general_secret(),
        "OE_POSTGRES_PASSWORD": general_secret(),
        "OE_ADMIN_PASSWORD": openelis_admin_password(),
        # Internal TLS material (DH-021) — must never be the published kspass/tspass
        "OE_KEYSTORE_PASSWORD": general_secret(32),
        "OE_TRUSTSTORE_PASSWORD": general_secret(32),
        # Keycloak
        "KEYCLOAK_DB_PASSWORD": general_secret(),
        "KEYCLOAK_ADMIN_PASSWORD": general_secret(),
        "KEYCLOAK_OPENMRS_CLIENT_SECRET": general_secret(),
        "KEYCLOAK_OPENELIS_CLIENT_SECRET": general_secret(),
        "KEYCLOAK_ADAPTER_CLIENT_SECRET": general_secret(),
        # DH-EX-008: separate realm (`dhealth-exchange`), separate client, never
        # reused from the daher-realm secrets above.
        "KEYCLOAK_EXCHANGE_API_CLIENT_SECRET": general_secret(),
        # Adapter
        "ADAPTER_DB_PASSWORD": general_secret(),
        # ⚠️ BACKUP_ENCRYPTION_PASSPHRASE is deliberately ABSENT (ADR-025). Generating it
        # here would put the only copy on the machine being backed up, so losing that host
        # — the disaster backups exist for — would make every off-site archive permanently
        # undecryptable. The operator supplies it during host bootstrap instead, so it
        # exists in two places by construction.
    }
    return FacilitySecrets(facility_id=facility_id, values=values)
