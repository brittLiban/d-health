from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.models import Base


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = tmp_path / "adapter-test.sqlite"
    test_engine = create_engine(f"sqlite:///{database}")

    # DH-EX-002R: pysqlite (the stdlib `sqlite3` DBAPI) manages its own implicit transactions
    # underneath SQLAlchemy's, which silently breaks SAVEPOINT rollback semantics unless disabled
    # -- this is SQLAlchemy's own documented required workaround ("Serializable isolation /
    # Savepoints / Transactional DDL" in the SQLite dialect docs), not a project-specific hack.
    # Discovered because it broke exactly the scenario it describes: a `Session.begin_nested()`
    # SAVEPOINT that completes normally, followed by a *later*, unrelated failure in the same
    # outer transaction, failed to roll back the savepoint's already-flushed row under plain
    # pysqlite defaults -- verified reproducible in isolation, and verified ABSENT against real
    # PostgreSQL via psycopg (this repo's actual runtime driver), so this is purely a test-only
    # SQLite/pysqlite gap, not a production correctness issue. Fixing it here makes this shared
    # fixture actually exercise the rollback behavior the whole suite is written to assume,
    # exactly the class of gap DH-EX-002R's transactional-audit control specifically tests for.
    @event.listens_for(test_engine, "connect")
    def _sqlite_disable_pysqlite_implicit_transactions(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(test_engine, "begin")
    def _sqlite_emit_explicit_begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
