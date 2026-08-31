"""DH-011E4: live proof of the two properties SQLite's unit tests cannot provide -- real
PostgreSQL row-level locking (`SELECT ... FOR UPDATE`) and the repeated-dead-letter schema change.

`adapter/tests/test_ledger.py`'s `test_authorize_dead_letter_retry_refuses_a_stale_dead_letter_id`
and its siblings prove the *logic* of `authorize_dead_letter_retry` against SQLite, which silently
no-ops `.with_for_update()` -- it proves nothing about serialization under real concurrency. This
file proves that: two real threads, two real PostgreSQL connections, one dead letter, exactly one
resolution wins.

It also proves §3/§14 end to end against a real database: `move_to_dead_letter` -> `sequence=1` ->
`authorize_dead_letter_retry` -> `processing` -> `move_to_dead_letter` again -> `sequence=2`,
never colliding with the relaxed `dead_letters.processed_message_id` uniqueness this ticket's own
migration (`0003_dead_letter_resolutions`) introduced.

**Manual / opt-in only**, mirroring every other DH-011 integration test file in this directory:
skipped by default (including in CI) unless both `DH011E4_ADAPTER_DB_*` env vars below are set.
Never point this at a real facility -- nothing here checks that, only a disposable Postgres.

Usage::

    DH011E4_ADAPTER_DB_PORT=15549 \\
    DH011E4_ADAPTER_DB_PASSWORD=<that stack's generated password> \\
        pytest adapter/tests/integration/test_dh011e4_dead_letter_retry.py -v
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.ledger import LedgerRepository, LedgerStateError, MessageIdentity
from daher_adapter.models import DeadLetter, DeadLetterResolution, ProcessedMessage

ADAPTER_DB_PORT = os.environ.get("DH011E4_ADAPTER_DB_PORT")
ADAPTER_DB_PASSWORD = os.environ.get("DH011E4_ADAPTER_DB_PASSWORD")

pytestmark = pytest.mark.skipif(
    not (ADAPTER_DB_PORT and ADAPTER_DB_PASSWORD),
    reason="requires a live disposable Postgres (DH011E4_ADAPTER_DB_* env vars) -- never run in CI",
)


def identity(rid: str = "dh011e4-real-order") -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=rid,
        resource_version="1",
    )


@pytest.fixture
def sessions() -> sessionmaker:
    engine = create_engine(
        f"postgresql+psycopg://adapter:{ADAPTER_DB_PASSWORD}@localhost:{ADAPTER_DB_PORT}/adapter"
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.begin() as connection:
        for table in ("dead_letter_resolutions", "dead_letters", "processed_messages"):
            connection.exec_driver_sql(f"TRUNCATE TABLE {table} CASCADE")
    yield factory
    engine.dispose()


def test_two_concurrent_operators_only_one_authorization_wins(sessions: sessionmaker) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload={"status": "active"}, error=ValueError("bad mapping"), attempts=1
    )
    latest = repository.latest_dead_letter(identity())
    assert latest is not None

    def attempt(actor: str) -> str | None:
        try:
            repository.authorize_dead_letter_retry(
                identity(),
                dead_letter_id=latest.id,
                action="retry_after_correction",
                reason="corrected the cause",
                actor=actor,
                actor_uid=1001,
                actor_source="host_os_wrapper",
                resulting_state="processing",
            )
            return None
        except LedgerStateError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, ["operator-a", "operator-b"]))

    assert sorted(r is None for r in results) == [False, True], (
        "exactly one of the two concurrent attempts must succeed"
    )
    assert repository.get_state(identity()) == "processing"
    with sessions() as session:
        resolutions = session.scalars(select(DeadLetterResolution)).all()
        assert len(resolutions) == 1, "only the winner writes an audit row -- the loser refused"
        assert resolutions[0].resulting_state == "processing"


def test_two_concurrent_operators_never_produce_two_processing_transitions(
    sessions: sessionmaker,
) -> None:
    """A stronger, direct assertion than the previous test's row count: run the race N times
    fresh and confirm the ledger state is *always* singular, never corrupted by an interleaving
    that the row lock should have prevented."""

    def attempt(repository: LedgerRepository, rid: str, dead_letter_id: object) -> bool:
        try:
            repository.authorize_dead_letter_retry(
                identity(rid),
                dead_letter_id=dead_letter_id,
                action="retry_after_correction",
                reason="race",
                actor="racer",
                actor_uid=1001,
                actor_source="host_os_wrapper",
                resulting_state="processing",
            )
            return True
        except LedgerStateError:
            return False

    for round_number in range(5):
        rid = f"dh011e4-race-{round_number}"
        repository = LedgerRepository(sessions)
        repository.claim(identity(rid))
        repository.move_to_dead_letter(identity(rid), payload={}, error=ValueError("x"), attempts=1)
        latest = repository.latest_dead_letter(identity(rid))
        assert latest is not None

        with ThreadPoolExecutor(max_workers=4) as workers:
            futures = [workers.submit(attempt, repository, rid, latest.id) for _ in range(4)]
            wins = [future.result() for future in futures]

        assert sum(wins) == 1, f"round {round_number}: exactly one of four concurrent wins"
        assert repository.get_state(identity(rid)) == "processing"


def test_repeated_dead_letter_lifecycle_on_real_postgres(sessions: sessionmaker) -> None:
    """§3/§14 end to end: the relaxed unique constraint (migration `0003`) actually behaves
    correctly under a real transaction, not just SQLite's looser enforcement."""
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload={"attempt": 1}, error=ValueError("first"), attempts=1
    )
    first = repository.latest_dead_letter(identity())
    assert first is not None and first.sequence == 1

    repository.authorize_dead_letter_retry(
        identity(),
        dead_letter_id=first.id,
        action="retry_after_correction",
        reason="corrected once",
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        resulting_state="processing",
    )
    assert repository.get_state(identity()) == "processing"

    repository.move_to_dead_letter(
        identity(), payload={"attempt": 2}, error=RuntimeError("second"), attempts=1
    )
    second = repository.latest_dead_letter(identity())
    assert second is not None and second.sequence == 2

    with sessions() as session:
        rows = session.scalars(select(DeadLetter).order_by(DeadLetter.sequence)).all()
        assert [row.payload for row in rows] == [{"attempt": 1}, {"attempt": 2}]
        message = session.scalar(select(ProcessedMessage))
        assert message is not None and message.state == "dead_letter"

    repository.authorize_dead_letter_retry(
        identity(),
        dead_letter_id=second.id,
        action="retry_after_correction",
        reason="corrected the real cause this time",
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        resulting_state="processing",
    )
    assert repository.get_state(identity()) == "processing"
    with sessions() as session:
        resolutions = session.scalars(
            select(DeadLetterResolution).order_by(DeadLetterResolution.requested_at)
        ).all()
        assert len(resolutions) == 2
        assert [r.resulting_state for r in resolutions] == ["processing", "processing"]


def test_no_duplicate_active_retry_sequential_reattempt_with_same_stale_id_refused(
    sessions: sessionmaker,
) -> None:
    """§9(G): once an authorization has won (real PostgreSQL, not SQLite), a second, sequential
    attempt against the *same* now-stale `dead_letter_id` must still be refused -- there is never
    a moment where two authorizations for the same dead letter are simultaneously valid."""
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload={}, error=ValueError("bad mapping"), attempts=1
    )
    latest = repository.latest_dead_letter(identity())
    assert latest is not None

    repository.authorize_dead_letter_retry(
        identity(),
        dead_letter_id=latest.id,
        action="retry_after_correction",
        reason="first, real authorization",
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        resulting_state="processing",
    )
    assert repository.get_state(identity()) == "processing"

    with pytest.raises(LedgerStateError):
        repository.authorize_dead_letter_retry(
            identity(),
            dead_letter_id=latest.id,
            action="retry_after_correction",
            reason="stale re-attempt, same dead_letter_id",
            actor="second-operator",
            actor_uid=1002,
            actor_source="host_os_wrapper",
            resulting_state="processing",
        )

    assert repository.get_state(identity()) == "processing", "state unchanged by the refused retry"
    with sessions() as session:
        resolutions = session.scalars(select(DeadLetterResolution)).all()
        assert len(resolutions) == 1, (
            "the refused re-attempt is not even recorded -- it fails the wrong-state check "
            "before authorize_dead_letter_retry ever reaches the point of writing an audit row"
        )
