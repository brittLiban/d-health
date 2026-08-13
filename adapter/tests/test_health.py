from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from daher_adapter.app import create_app
from daher_adapter.audit import AuditWriter
from daher_adapter.config import Settings


def test_health_requires_database_and_audit_volume(engine: Engine, tmp_path: Path) -> None:
    settings = Settings(
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
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))

    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "up"}
        ready = client.get("/health/ready")

    assert ready.status_code == 200
    assert ready.json() == {"status": "up"}
    assert settings.audit_path.exists()


def test_readiness_is_down_when_audit_path_is_unusable(engine: Engine, tmp_path: Path) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("blocked", encoding="utf-8")
    settings = Settings(
        timezone="Africa/Djibouti",
        db_host="unused",
        db_port=5432,
        db_name="adapter",
        db_user="adapter",
        db_password="not-a-real-secret",
        audit_path=blocked_parent / "audit.jsonl",
        retry_max_attempts=5,
        retry_multiplier_seconds=1,
        retry_max_seconds=30,
    )
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))

    with TestClient(app) as client:
        ready = client.get("/health/ready")

    assert ready.status_code == 503
    assert ready.json() == {"status": "down"}
