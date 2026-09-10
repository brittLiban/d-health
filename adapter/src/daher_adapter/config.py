from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import URL

_DB_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# DH-011/BLOCKER-6 (Option F): OpenELIS's SITEYEARNUM accession format needs a real numeric
# facility site code before any ServiceRequest can be created (DH-011B, live-reconfirmed this
# pass: `site_information` still carries the upstream placeholder). `DEV01` is the shipped
# upstream default -- refusing it is the one thing this repo can safely check in code, because
# it is proven from the running config, not guessed. The real value is NEEDS LIBAN #7 and is
# never invented here.
_KNOWN_PLACEHOLDER_SITE_CODES = frozenset({"DEV01"})


class ConfigurationError(ValueError):
    """Configuration is missing or unsafe."""


def _optional_site_code(environment: Mapping[str, str], name: str) -> str | None:
    """`None` until the real facility value exists -- never a placeholder, never invented.

    Validates only what DH-011B actually proved from the running OpenELIS config
    (`SITEYEARNUM`, numeric, `DEV01` refused): non-empty, digits only, and not the known
    upstream placeholder. It does NOT enforce a specific digit count -- the constant-pool read
    established the *overall* generated accession is 13 digits, not how many of those belong to
    the site code itself versus the year/sequence portions, and that split was never confirmed
    from source. Asserting an unverified width here would be exactly the kind of invented
    facility semantics this ticket forbids.
    """
    value = environment.get(name, "").strip()
    if not value:
        return None
    if value in _KNOWN_PLACEHOLDER_SITE_CODES:
        raise ConfigurationError(
            f"{name} is still the upstream placeholder {value!r} -- the real facility site "
            f"code has not been supplied (NEEDS LIBAN #7, PROJECT_STATE.md BLOCKER-6)"
        )
    if not value.isdigit():
        raise ConfigurationError(
            f"{name} must be numeric -- OpenELIS's SITEYEARNUM accession format refuses a "
            f"non-numeric site code (DH-011B)"
        )
    return value


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _positive_int(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _bounded_int(
    environment: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int
) -> int:
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if not (minimum <= value <= maximum):
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _positive_float(environment: Mapping[str, str], name: str, default: float) -> float:
    raw = environment.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    timezone: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    audit_path: Path
    retry_max_attempts: int
    retry_multiplier_seconds: float
    retry_max_seconds: float
    openelis_site_code: str | None = None
    # DH-011E4: only the dead-letter retry CLI reads these -- the running FastAPI service does
    # not talk to OpenMRS at all yet (DH-011E is BUILT + VERIFIED, NOT ACTIVATED). Optional so
    # every existing deployment's `Settings.from_env()` keeps working unchanged.
    omrs_base_url: str | None = None
    omrs_auth: str | None = None
    # DH-EX-002: the Exchange's own OIDC identity boundary -- deliberately distinct from the
    # connector `daher` Keycloak realm (keycloak/realm-config/daher-realm.json), which stays
    # untouched. All three optional so every existing deployment keeps working unchanged; the
    # Exchange API fails closed (401 on every request) when any is unset, rather than refusing to
    # start -- see exchange/identity_auth.py's `UnconfiguredKeySource`.
    exchange_oidc_issuer: str | None = None
    exchange_oidc_audience: str | None = None
    exchange_oidc_jwks_url: str | None = None
    # Keycloak realm role name (relayed via the standard `realm_access.roles` claim) that grants
    # DHealth platform-admin authority. Never inferred from an organization role -- see
    # exchange/authorization.py.
    exchange_platform_admin_role: str = "dhealth-platform-admin"
    # Deliberately separate from `audit_path` above (the OpenMRS/OpenELIS connector's own audit
    # log) -- DH-EX-002's audit events are a different closed schema with no `direction` field;
    # sharing one file would force a reader to distinguish two unrelated shapes by inspection.
    # `None` resolves to a sibling file next to `audit_path` (see `resolved_exchange_audit_path`).
    exchange_audit_path: Path | None = None
    # DH-EX-005: the opaque patient order-access grant's technical TTL -- an access-security
    # default, not a clinical policy. 168h (7 days) is a conservative MVP default; bounded to
    # [1, 720] hours (1 hour .. 30 days) so a misconfigured value cannot silently produce an
    # unbounded-lifetime or effectively-instant-expiry grant.
    exchange_patient_access_ttl_hours: int = 168

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environment is None else environment
        timezone = env.get("TZ", "Africa/Djibouti")
        if timezone != "Africa/Djibouti":
            raise ConfigurationError("TZ must be Africa/Djibouti")

        db_name = _required(env, "ADAPTER_DB_NAME")
        db_user = _required(env, "ADAPTER_DB_USER")
        for name, value in (("ADAPTER_DB_NAME", db_name), ("ADAPTER_DB_USER", db_user)):
            if not _DB_IDENTIFIER.fullmatch(value):
                raise ConfigurationError(f"{name} is not a safe PostgreSQL identifier")

        return cls(
            timezone=timezone,
            db_host=_required(env, "ADAPTER_DB_HOST"),
            db_port=_positive_int(env, "ADAPTER_DB_PORT", 5432),
            db_name=db_name,
            db_user=db_user,
            db_password=_required(env, "ADAPTER_DB_PASSWORD"),
            audit_path=Path(env.get("ADAPTER_AUDIT_PATH", "/var/lib/daher/audit/audit.jsonl")),
            retry_max_attempts=_positive_int(env, "ADAPTER_RETRY_MAX_ATTEMPTS", 5),
            retry_multiplier_seconds=_positive_float(env, "ADAPTER_RETRY_MULTIPLIER_SECONDS", 1.0),
            retry_max_seconds=_positive_float(env, "ADAPTER_RETRY_MAX_SECONDS", 30.0),
            openelis_site_code=_optional_site_code(env, "OPENELIS_SITE_CODE"),
            omrs_base_url=env.get("ADAPTER_OMRS_BASE_URL", "").strip() or None,
            omrs_auth=env.get("ADAPTER_OMRS_AUTH", "").strip() or None,
            exchange_oidc_issuer=env.get("EXCHANGE_OIDC_ISSUER", "").strip() or None,
            exchange_oidc_audience=env.get("EXCHANGE_OIDC_AUDIENCE", "").strip() or None,
            exchange_oidc_jwks_url=env.get("EXCHANGE_OIDC_JWKS_URL", "").strip() or None,
            exchange_platform_admin_role=(
                env.get("EXCHANGE_PLATFORM_ADMIN_ROLE", "").strip() or "dhealth-platform-admin"
            ),
            exchange_audit_path=(
                Path(env["EXCHANGE_AUDIT_PATH"])
                if env.get("EXCHANGE_AUDIT_PATH", "").strip()
                else None
            ),
            exchange_patient_access_ttl_hours=_bounded_int(
                env, "EXCHANGE_PATIENT_ACCESS_TTL_HOURS", 168, minimum=1, maximum=720
            ),
        )

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def resolved_exchange_audit_path(self) -> Path:
        if self.exchange_audit_path is not None:
            return self.exchange_audit_path
        return self.audit_path.with_name("exchange-" + self.audit_path.name)

    @property
    def exchange_oidc_configured(self) -> bool:
        """`False` means the Exchange API must fail closed on every request -- never a partial
        or best-effort validation. See `exchange/identity_auth.py`."""
        return bool(
            self.exchange_oidc_issuer
            and self.exchange_oidc_audience
            and self.exchange_oidc_jwks_url
        )

    @property
    def service_request_delivery_ready(self) -> bool:
        """One of several required activation gates (§16) -- the only one a config value can
        represent. Patient-identifier policy, requester approval, test mapping, and the
        DH-011D correlation patch being live-proven are process/documentation gates, not
        environment values; they are recorded in PROJECT_STATE.md, not asserted here."""
        return self.openelis_site_code is not None
