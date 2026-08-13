from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import URL

_DB_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigurationError(ValueError):
    """Configuration is missing or unsafe."""


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
