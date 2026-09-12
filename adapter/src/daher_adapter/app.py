from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from sqlalchemy import Engine, text

from daher_adapter import __version__
from daher_adapter.audit import AuditWriter
from daher_adapter.config import Settings
from daher_adapter.database import build_engine, build_session_factory
from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.identity_auth import (
    JWKSKeySource,
    TokenValidator,
    UnconfiguredKeySource,
)
from daher_adapter.logging import configure_logging
from daher_adapter.recovery import ManagedRecoveryFence, PostgresRecoveryFence

logger = logging.getLogger("daher_adapter.health")


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    audit_writer: AuditWriter | None = None,
    recovery_fence: ManagedRecoveryFence | None = None,
) -> FastAPI:
    configure_logging()
    active_settings = settings or Settings.from_env()
    active_engine = engine or build_engine(active_settings)
    active_audit = audit_writer or AuditWriter(active_settings.audit_path)

    # DH-EX-002: the Exchange API is always mounted -- it fails closed (401 on every request) via
    # `UnconfiguredKeySource` when OIDC is not configured, rather than the whole adapter refusing
    # to start. This must never affect `/health/live` or `/health/ready` above, which stay
    # independent of Keycloak reachability.
    exchange_session_factory = build_session_factory(active_engine)
    exchange_audit = ExchangeAuditWriter(active_settings.resolved_exchange_audit_path)
    exchange_key_source = (
        JWKSKeySource(active_settings.exchange_oidc_jwks_url)
        if active_settings.exchange_oidc_configured
        else UnconfiguredKeySource()
    )
    exchange_token_validator = TokenValidator(
        issuer=active_settings.exchange_oidc_issuer or "",
        audience=active_settings.exchange_oidc_audience or "",
        key_source=exchange_key_source,
        platform_admin_role=active_settings.exchange_platform_admin_role,
    )
    exchange_router = build_exchange_router(
        session_factory=exchange_session_factory,
        token_validator=exchange_token_validator,
        audit_writer=exchange_audit,
        platform_admin_role=active_settings.exchange_platform_admin_role,
        patient_access_ttl_hours=active_settings.exchange_patient_access_ttl_hours,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_fence = recovery_fence
        if active_fence is None and active_engine.dialect.name == "postgresql":
            active_fence = PostgresRecoveryFence(active_engine)
        if active_fence is None:
            yield
            return
        with active_fence:
            application.state.recovery_fence = active_fence
            yield

    app = FastAPI(
        title="Daher Health adapter",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "up"}

    @app.get("/health/ready")
    def ready(response: Response) -> dict[str, str]:
        try:
            with active_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            active_audit.ensure_available()
            active_fence = getattr(app.state, "recovery_fence", None)
            if active_engine.dialect.name == "postgresql" and (
                active_fence is None or not active_fence.held
            ):
                raise RuntimeError("adapter process fence is not held")
        except Exception as error:  # Health must convert dependency failures into a 503.
            logger.error(
                "readiness_failed",
                extra={"error_type": type(error).__name__},
            )
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "down"}
        return {"status": "up"}

    # DH-EX-008: independent of `/health/ready` above by design -- that endpoint's exact response
    # body (`{"status": "up"}`) is asserted verbatim by this container's own Docker healthcheck
    # (docker-compose.yml), so it must never gain an Exchange-shaped field or fail merely because
    # Exchange OIDC is unconfigured or Keycloak is briefly unreachable. This endpoint is NOT routed
    # publicly through Caddy (see caddy/Caddyfile) and is NOT wired into any Docker healthcheck --
    # a brief Keycloak hiccup must not restart the adapter process, which owns unrelated
    # connector-track responsibilities that have nothing to do with Exchange.
    @app.get("/health/exchange-ready")
    def exchange_ready(response: Response) -> dict[str, str]:
        if not active_settings.exchange_oidc_configured:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "down", "reason": "unconfigured"}
        try:
            request = urllib.request.Request(  # noqa: S310 -- fixed operator-configured URL, not user input
                active_settings.exchange_oidc_jwks_url
            )
            with urllib.request.urlopen(request, timeout=5) as jwks_response:  # noqa: S310
                jwks_body = json.load(jwks_response)
            if not isinstance(jwks_body, dict) or not jwks_body.get("keys"):
                raise ValueError("JWKS response has no keys")
        except Exception as error:  # A reachability probe must convert any failure into a 503.
            logger.error(
                "exchange_readiness_failed",
                extra={"error_type": type(error).__name__},
            )
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "down", "reason": "jwks_unreachable"}
        return {"status": "up"}

    app.include_router(exchange_router)

    return app
