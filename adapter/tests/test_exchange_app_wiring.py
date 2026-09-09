"""DH-EX-002's real `create_app()` wiring: the Exchange router is always mounted, and fails
closed (401 on every request) when Exchange OIDC is unconfigured, rather than the whole adapter
refusing to start or -- far worse -- accepting unverified tokens. Health endpoints must stay
unaffected by Exchange/Keycloak configuration or reachability either way.

`test_exchange_api.py` covers the full endpoint behavior against an injected `StaticKeySource`;
this file only proves the production `create_app()` assembly path itself.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from daher_adapter.app import create_app
from daher_adapter.audit import AuditWriter
from daher_adapter.config import Settings


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base = dict(
        timezone="Africa/Djibouti",
        db_host="unused",
        db_port=5432,
        db_name="adapter",
        db_user="adapter",
        db_password="not-a-real-secret",
        audit_path=tmp_path / "audit" / "audit.jsonl",
        retry_max_attempts=5,
        retry_multiplier_seconds=1,
        retry_max_seconds=30,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_exchange_api_fails_closed_when_oidc_is_unconfigured(
    engine: Engine, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    assert settings.exchange_oidc_configured is False
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))

    with TestClient(app) as client:
        me = client.get("/exchange/v1/me", headers={"Authorization": "Bearer whatever-this-is"})
        create = client.post(
            "/exchange/v1/organizations",
            headers={"Authorization": "Bearer whatever-this-is"},
            json={"name": "X", "organization_type": "PROVIDER"},
        )

    assert me.status_code == 401
    assert create.status_code == 401


def test_health_endpoints_are_unaffected_by_exchange_configuration(
    engine: Engine, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))

    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "up"}
        assert client.get("/health/ready").status_code == 200


def test_exchange_router_is_mounted_under_the_versioned_prefix(
    engine: Engine, tmp_path: Path
) -> None:
    # Not-mounted would 404; mounted-but-unauthenticated is 401 -- the distinction this test
    # exists to prove, since `app.routes` wraps included routers in a form with no stable
    # `.path` attribute in this FastAPI/Starlette pin.
    settings = _settings(tmp_path)
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))
    with TestClient(app) as client:
        me = client.get("/exchange/v1/me")
        missing = client.get("/exchange/v1/this-route-does-not-exist")
    assert me.status_code == 401
    assert missing.status_code == 404
