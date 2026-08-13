from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.models import Base


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = tmp_path / "adapter-test.sqlite"
    test_engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
