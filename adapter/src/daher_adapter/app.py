from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from sqlalchemy import Engine, text

from daher_adapter import __version__
from daher_adapter.audit import AuditWriter
from daher_adapter.config import Settings
from daher_adapter.database import build_engine
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

    return app
