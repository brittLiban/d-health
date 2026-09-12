"""DH-EX-002's real `create_app()` wiring: the Exchange router is always mounted, and fails
closed (401 on every request) when Exchange OIDC is unconfigured, rather than the whole adapter
refusing to start or -- far worse -- accepting unverified tokens. Health endpoints must stay
unaffected by Exchange/Keycloak configuration or reachability either way.

`test_exchange_api.py` covers the full endpoint behavior against an injected `StaticKeySource`;
this file only proves the production `create_app()` assembly path itself.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
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


class _JWKSHandler(BaseHTTPRequestHandler):
    """Serves one fixed, valid-shaped JWKS document -- real loopback HTTP, not a mocked client."""

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's own required method name
        body = json.dumps({"keys": [{"kid": "test-key", "kty": "RSA"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_: str, *args: object) -> None:  # noqa: A002
        pass  # Silence per-request stderr noise during test runs.


class _EmptyJWKSHandler(_JWKSHandler):
    """Reachable, valid JSON, but not a usable JWKS document -- Keycloak misconfiguration shape."""

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"keys": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _unreachable_url() -> str:
    """A real, currently-unbound loopback port -- refuses connection immediately, rather than the
    OS-reserved port 1, which is nobody's actual JWKS endpoint but also nobody's guaranteed refusal
    on every platform."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    finally:
        probe.close()
    return f"http://127.0.0.1:{port}/realms/dhealth-exchange/protocol/openid-connect/certs"


@contextmanager
def _running_jwks_server(handler: type[BaseHTTPRequestHandler] = _JWKSHandler) -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/realms/dhealth-exchange/protocol/openid-connect/certs"
    finally:
        server.shutdown()
        thread.join()


def test_exchange_ready_reports_unconfigured_when_oidc_is_unset(
    engine: Engine, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    assert settings.exchange_oidc_configured is False
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))

    with TestClient(app) as client:
        response = client.get("/health/exchange-ready")

    assert response.status_code == 503
    assert response.json() == {"status": "down", "reason": "unconfigured"}


def test_exchange_ready_reports_unconfigured_when_only_partially_set(
    engine: Engine, tmp_path: Path
) -> None:
    # `exchange_oidc_configured` requires issuer, audience AND jwks_url together -- removing any
    # one of the three (a real deployment mistake this negative control stands in for) must fail
    # closed exactly like leaving all three unset, never a partial/best-effort validation.
    settings = _settings(
        tmp_path,
        exchange_oidc_issuer="https://id.example.org/realms/dhealth-exchange",
        exchange_oidc_audience="dhealth-exchange-api",
        exchange_oidc_jwks_url=None,
    )
    assert settings.exchange_oidc_configured is False
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))

    with TestClient(app) as client:
        response = client.get("/health/exchange-ready")

    assert response.status_code == 503
    assert response.json() == {"status": "down", "reason": "unconfigured"}


def test_exchange_ready_reports_jwks_unreachable(engine: Engine, tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        exchange_oidc_issuer="https://id.example.org/realms/dhealth-exchange",
        exchange_oidc_audience="dhealth-exchange-api",
        # Nothing listens here -- a real, immediate connection refusal, not a mock.
        exchange_oidc_jwks_url=_unreachable_url(),
    )
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))

    with TestClient(app) as client:
        response = client.get("/health/exchange-ready")

    assert response.status_code == 503
    assert response.json() == {"status": "down", "reason": "jwks_unreachable"}


def test_exchange_ready_reports_jwks_unreachable_when_keys_list_is_empty(
    engine: Engine, tmp_path: Path
) -> None:
    with _running_jwks_server(_EmptyJWKSHandler) as jwks_url:
        settings = _settings(
            tmp_path,
            exchange_oidc_issuer="https://id.example.org/realms/dhealth-exchange",
            exchange_oidc_audience="dhealth-exchange-api",
            exchange_oidc_jwks_url=jwks_url,
        )
        app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))
        with TestClient(app) as client:
            response = client.get("/health/exchange-ready")

    assert response.status_code == 503
    assert response.json() == {"status": "down", "reason": "jwks_unreachable"}


def test_exchange_ready_reports_up_when_jwks_is_reachable(engine: Engine, tmp_path: Path) -> None:
    with _running_jwks_server() as jwks_url:
        settings = _settings(
            tmp_path,
            exchange_oidc_issuer="https://id.example.org/realms/dhealth-exchange",
            exchange_oidc_audience="dhealth-exchange-api",
            exchange_oidc_jwks_url=jwks_url,
        )
        app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))
        with TestClient(app) as client:
            response = client.get("/health/exchange-ready")

    assert response.status_code == 200
    assert response.json() == {"status": "up"}


def test_exchange_ready_never_changes_health_ready_response_body(
    engine: Engine, tmp_path: Path
) -> None:
    # The adapter's own Docker healthcheck asserts `body == {"status": "up"}` verbatim
    # (docker-compose.yml) -- exchange-ready must be a sibling endpoint, never a field added here.
    settings = _settings(tmp_path)
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "up"}


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
