"""DH-CONNECT-AGENT-001 §26–§30 — the agent's own durable store.

Stdlib ``sqlite3``, one file, no server. §26 forbids browser storage and flat JSON files as the
authoritative queue and forbids requiring partner PostgreSQL to run the agent; SQLite is what is
left that is actually transactional, and it is enough -- one agent, one writer, modest volume.

**Not a second Alembic environment.** ``MIGRATIONS`` is an ordered list of DDL steps and
``schema_version`` records how many have been applied. An agent installed at a partner site is
upgraded by replacing a binary, not by someone running a migration tool over SSH, so the schema has
to advance on startup or not at all. Alembic would have brought a config file, a versions directory
and an entry point that must be run separately -- three more things to go wrong on a machine nobody
from DHealth can log into.

Four tables (§27):

``inbox``    messages claimed from DHealth, persisted **before** they are handed to the adapter and
             before they are acknowledged. The ordering is the whole guarantee: a crash after the
             claim and before the ACK means DHealth redelivers, and the local row is what makes the
             redelivery a no-op instead of a second order.
``outbox``   events the adapter produced, persisted before they are submitted, kept until DHealth
             confirms. A crash here means the agent resubmits; the server's replay key collapses it.
``receipts`` what this agent has already applied to its own adapter, by source event id. The local
             mirror of ``connector_message_receipts`` -- without it, at-least-once delivery plus a
             restart turns one order into two orders in the partner's LIS.
``meta``     the runtime instance id and the schema version. Small, and deliberately not a place for
             anything else to accumulate.

**No PHI beyond what the messages themselves carry**, and no credentials ever. The client secret is
read from the environment at startup and never written here (§9, §72).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Ordered, append-only. Index *is* the schema version: never reorder, never edit an applied entry,
#: and add by appending. An edited entry is a migration that has already run at a partner site and
#: will not run again, so the two installations diverge silently -- the failure mode every
#: hand-rolled migration table eventually produces if the list is treated as editable.
MIGRATIONS: Sequence[str] = (
    """
    CREATE TABLE inbox (
        delivery_id      TEXT PRIMARY KEY,
        message_type     TEXT NOT NULL,
        source_event_id  TEXT NOT NULL,
        replay_scope     TEXT NOT NULL,
        envelope_json    TEXT NOT NULL,
        state            TEXT NOT NULL,
        attempt_count    INTEGER NOT NULL DEFAULT 0,
        failure_category TEXT,
        failure_detail   TEXT,
        received_at      TEXT NOT NULL,
        handled_at       TEXT,
        acknowledged_at  TEXT
    )
    """,
    """
    CREATE TABLE outbox (
        id               TEXT PRIMARY KEY,
        message_type     TEXT NOT NULL,
        source_event_id  TEXT NOT NULL,
        envelope_json    TEXT NOT NULL,
        state            TEXT NOT NULL,
        attempt_count    INTEGER NOT NULL DEFAULT 0,
        failure_detail   TEXT,
        created_at       TEXT NOT NULL,
        submitted_at     TEXT
    )
    """,
    """
    CREATE TABLE receipts (
        source_event_id  TEXT NOT NULL,
        replay_scope     TEXT NOT NULL,
        fingerprint      TEXT NOT NULL,
        applied_at       TEXT NOT NULL,
        PRIMARY KEY (replay_scope, source_event_id)
    )
    """,
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX uq_outbox_event ON outbox (source_event_id)",
    "CREATE INDEX ix_inbox_state ON inbox (state)",
    "CREATE INDEX ix_outbox_state ON outbox (state)",
)


class InboxState:
    """Where an inbound message is in the agent's own pipeline.

    ``RECEIVED`` and ``HANDLED`` are separate states, and that separation is the crash window §75
    is about. A message that has been handed to the adapter but not yet acknowledged to DHealth must
    be recognisable on restart as "already done locally, still owed an acknowledgement" -- one state
    could not express that, and the agent would either re-run the adapter or silently drop the ACK.
    """

    RECEIVED = "RECEIVED"
    HANDLED = "HANDLED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED = "FAILED"


class OutboxState:
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class InboxRow:
    delivery_id: str
    message_type: str
    source_event_id: str
    replay_scope: str
    envelope_json: str
    state: str
    attempt_count: int
    failure_category: str | None
    failure_detail: str | None


@dataclass(frozen=True, slots=True)
class OutboxRow:
    id: str
    message_type: str
    source_event_id: str
    envelope_json: str
    state: str
    attempt_count: int


def _now() -> str:
    """UTC, offset-aware, ISO-8601. §59 -- and stored as text because SQLite has no instant type.

    Written through one function so every timestamp in this database has one shape. A store where
    half the rows are naive and half are not is a store nobody can order correctly.
    """
    return datetime.now(UTC).isoformat()


class AgentStore:
    """The agent's durable state. One connection, one writer, explicit transactions.

    ``check_same_thread=False`` is deliberately **not** set: the runner is single-threaded by design
    (see the package docstring), and allowing cross-thread use would invite a second writer into a
    database whose correctness argument assumes there is one.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        if self._path.parent != Path("") and str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self._path), isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        # WAL keeps a reader (a `status` command) from blocking the runner mid-cycle, and survives a
        # process kill more gracefully than the rollback journal. FULL synchronous because the whole
        # point of this file is that a message persisted before a crash is still there afterwards --
        # NORMAL would trade exactly that guarantee for speed this workload does not need.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # -- schema -------------------------------------------------------------------------------

    def _migrate(self) -> None:
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = self._connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._connection.execute("INSERT INTO schema_version (version) VALUES (0)")
            applied = 0
        else:
            applied = int(row["version"])

        if applied > len(MIGRATIONS):
            # A store written by a *newer* agent. Refusing is the only safe answer: the newer schema
            # may hold columns this build would silently drop on write, and the rows in it are
            # undelivered clinical messages. §51 -- queued work is never deleted, and that includes
            # not quietly rewriting it to fit an older shape.
            raise AgentStoreTooNew(
                f"store schema version {applied} was written by a newer agent "
                f"(this build understands {len(MIGRATIONS)}); upgrade the agent rather than "
                "downgrading it with queued work in the store"
            )

        for index in range(applied, len(MIGRATIONS)):
            with self.transaction():
                self._connection.execute(MIGRATIONS[index])
                self._connection.execute("UPDATE schema_version SET version = ?", (index + 1,))

    @property
    def schema_version(self) -> int:
        row = self._connection.execute("SELECT version FROM schema_version").fetchone()
        return int(row["version"])

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit BEGIN/COMMIT, because ``isolation_level=None`` turns autocommit on.

        Every multi-statement sequence in this module is wrapped. The one that matters most is
        "record the receipt and mark the row handled" -- if those could commit separately, a crash
        between them would leave the agent having applied a message it does not remember applying.
        """
        self._connection.execute("BEGIN")
        try:
            yield self._connection
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    def close(self) -> None:
        self._connection.close()

    # -- meta ---------------------------------------------------------------------------------

    def runtime_instance_id(self) -> str:
        """This installation's stable runtime identity, minted once and then never changed.

        Minted here rather than taken from configuration so that it cannot be shared: two agents
        given the same value in a config file would claim each other's leases, and the symptom would
        be messages mysteriously delivered twice. Persisting it is what makes a restart the *same*
        runtime rather than a new one (§74).
        """
        row = self._connection.execute(
            "SELECT value FROM meta WHERE key = 'runtime_instance_id'"
        ).fetchone()
        if row is not None:
            return str(row["value"])
        minted = f"agent-{uuid.uuid4().hex[:16]}"
        with self.transaction():
            self._connection.execute(
                "INSERT INTO meta (key, value) VALUES ('runtime_instance_id', ?)", (minted,)
            )
        return minted

    # -- inbox --------------------------------------------------------------------------------

    def record_inbound(
        self,
        *,
        delivery_id: str,
        message_type: str,
        source_event_id: str,
        replay_scope: str,
        envelope_json: str,
        attempt_count: int,
    ) -> bool:
        """Persist a claimed message. Returns ``False`` if it was already known.

        **This happens before the adapter sees it and before DHealth is acknowledged**, and the
        ordering is not negotiable: a crash between claim and persist loses nothing, because DHealth
        still holds the row and will redeliver. A crash between persist and ACK also loses nothing,
        because the redelivery finds this row and skips the adapter. Reverse the order and the
        window between them becomes a window in which a clinical order vanishes.
        """
        with self.transaction():
            existing = self._connection.execute(
                "SELECT state FROM inbox WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if existing is not None:
                self._connection.execute(
                    "UPDATE inbox SET attempt_count = ? WHERE delivery_id = ?",
                    (attempt_count, delivery_id),
                )
                return False
            self._connection.execute(
                "INSERT INTO inbox (delivery_id, message_type, source_event_id, replay_scope, "
                "envelope_json, state, attempt_count, received_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    delivery_id,
                    message_type,
                    source_event_id,
                    replay_scope,
                    envelope_json,
                    InboxState.RECEIVED,
                    attempt_count,
                    _now(),
                ),
            )
        return True

    def mark_handled(
        self, delivery_id: str, *, replay_scope: str, source_event_id: str, fingerprint: str
    ) -> None:
        """The adapter accepted it. Receipt and state change commit together, or neither does."""
        with self.transaction():
            self._connection.execute(
                "UPDATE inbox SET state = ?, handled_at = ? WHERE delivery_id = ?",
                (InboxState.HANDLED, _now(), delivery_id),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO receipts (replay_scope, source_event_id, fingerprint, "
                "applied_at) VALUES (?, ?, ?, ?)",
                (replay_scope, source_event_id, fingerprint, _now()),
            )

    def mark_acknowledged(self, delivery_id: str) -> None:
        with self.transaction():
            self._connection.execute(
                "UPDATE inbox SET state = ?, acknowledged_at = ? WHERE delivery_id = ?",
                (InboxState.ACKNOWLEDGED, _now(), delivery_id),
            )

    def mark_inbound_failed(self, delivery_id: str, *, category: str, detail: str | None) -> None:
        with self.transaction():
            self._connection.execute(
                "UPDATE inbox SET state = ?, failure_category = ?, failure_detail = ? "
                "WHERE delivery_id = ?",
                (InboxState.FAILED, category, (detail or "")[:512] or None, delivery_id),
            )

    def already_applied(self, replay_scope: str, source_event_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT fingerprint FROM receipts WHERE replay_scope = ? AND source_event_id = ?",
            (replay_scope, source_event_id),
        ).fetchone()
        return None if row is None else str(row["fingerprint"])

    def unacknowledged(self) -> list[InboxRow]:
        """Rows the adapter has handled that DHealth has not been told about.

        Read on startup (§74). This is the crash window made recoverable: the work is done, the
        acknowledgement is owed, and nothing else in the system knows that but this table.
        """
        rows = self._connection.execute(
            "SELECT * FROM inbox WHERE state = ? ORDER BY received_at", (InboxState.HANDLED,)
        ).fetchall()
        return [_to_inbox(row) for row in rows]

    def pending_inbound(self) -> list[InboxRow]:
        rows = self._connection.execute(
            "SELECT * FROM inbox WHERE state = ? ORDER BY received_at", (InboxState.RECEIVED,)
        ).fetchall()
        return [_to_inbox(row) for row in rows]

    # -- outbox -------------------------------------------------------------------------------

    def enqueue_outbound(self, *, message_type: str, source_event_id: str, envelope: dict) -> bool:
        """Persist an adapter-produced event before trying to send it.

        Returns ``False`` when this source event is already queued or sent. The adapter is allowed
        to report the same event twice -- a poller re-reading a window will -- and the agent is
        where that becomes harmless rather than where it becomes a duplicate clinical message.
        """
        with self.transaction():
            existing = self._connection.execute(
                "SELECT id FROM outbox WHERE source_event_id = ?", (source_event_id,)
            ).fetchone()
            if existing is not None:
                return False
            self._connection.execute(
                "INSERT INTO outbox (id, message_type, source_event_id, envelope_json, state, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    message_type,
                    source_event_id,
                    json.dumps(envelope, sort_keys=True),
                    OutboxState.PENDING,
                    _now(),
                ),
            )
        return True

    def pending_outbound(self, limit: int = 20) -> list[OutboxRow]:
        rows = self._connection.execute(
            "SELECT * FROM outbox WHERE state = ? ORDER BY created_at LIMIT ?",
            (OutboxState.PENDING, limit),
        ).fetchall()
        return [_to_outbox(row) for row in rows]

    def mark_submitted(self, outbox_id: str) -> None:
        with self.transaction():
            self._connection.execute(
                "UPDATE outbox SET state = ?, submitted_at = ? WHERE id = ?",
                (OutboxState.SUBMITTED, _now(), outbox_id),
            )

    def mark_outbound_failed(
        self, outbox_id: str, *, detail: str | None, terminal: bool = False
    ) -> None:
        """Record a failed submission. ``terminal`` decides whether it will be tried again.

        **Neither branch deletes anything.** The row stays, it stays visible in ``status``, and
        §56's "fail closed and leave unresolved" holds: an outbound clinical event the partner's
        system produced is not DHealth's to discard.

        ``terminal=True`` stops the *resubmission*, and that distinction matters more than it looks.
        A server refusal that is not retryable -- a rejected envelope, a tenant mismatch, a
        contradicted event -- will be refused identically on every cycle, and the server audits each
        refusal. An agent looping at two cycles a minute therefore writes two security-audit rows a
        minute, forever, for one misconfigured message: exactly the §64 noise problem seen from the
        other side, burying the events that matter. Stopping is not giving up; it is not shouting.
        """
        with self.transaction():
            self._connection.execute(
                "UPDATE outbox SET attempt_count = attempt_count + 1, failure_detail = ?, "
                "state = ? WHERE id = ?",
                (
                    (detail or "")[:512] or None,
                    OutboxState.FAILED if terminal else OutboxState.PENDING,
                    outbox_id,
                ),
            )

    # -- reporting ----------------------------------------------------------------------------

    def queue_depth(self) -> int:
        """What the heartbeat reports: work this agent still owes somebody."""
        row = self._connection.execute(
            "SELECT (SELECT COUNT(*) FROM inbox WHERE state IN (?, ?)) + "
            "(SELECT COUNT(*) FROM outbox WHERE state = ?) AS depth",
            (InboxState.RECEIVED, InboxState.HANDLED, OutboxState.PENDING),
        ).fetchone()
        return int(row["depth"])

    def undeliverable_count(self) -> int:
        """Work this agent has given up on by itself, in either direction.

        Named for what it counts rather than "dead letters", because the central queue has a genuine
        ``DEAD_LETTER`` state and these are not the same thing: an inbox row marked ``FAILED`` for a
        transient reason is still owned by DHealth, still has retries left, and will be redelivered.
        Reporting it as a dead letter would degrade the facility's health for something that has not
        gone wrong yet.

        So only terminal categories count. ``REMOTE_UNAVAILABLE`` and ``TEMPORARY`` are the
        partner's system being briefly unreachable, which is what the retry budget is for.
        """
        row = self._connection.execute(
            "SELECT (SELECT COUNT(*) FROM inbox WHERE state = ? AND "
            "(failure_category IS NULL OR failure_category NOT IN (?, ?))) + "
            "(SELECT COUNT(*) FROM outbox WHERE state = ?) AS n",
            (InboxState.FAILED, "REMOTE_UNAVAILABLE", "TEMPORARY", OutboxState.FAILED),
        ).fetchone()
        return int(row["n"])


class AgentStoreTooNew(RuntimeError):
    """The store was written by a newer agent than this one. §51: refuse rather than rewrite."""


def _to_inbox(row: sqlite3.Row) -> InboxRow:
    return InboxRow(
        delivery_id=str(row["delivery_id"]),
        message_type=str(row["message_type"]),
        source_event_id=str(row["source_event_id"]),
        replay_scope=str(row["replay_scope"]),
        envelope_json=str(row["envelope_json"]),
        state=str(row["state"]),
        attempt_count=int(row["attempt_count"]),
        failure_category=row["failure_category"],
        failure_detail=row["failure_detail"],
    )


def _to_outbox(row: sqlite3.Row) -> OutboxRow:
    return OutboxRow(
        id=str(row["id"]),
        message_type=str(row["message_type"]),
        source_event_id=str(row["source_event_id"]),
        envelope_json=str(row["envelope_json"]),
        state=str(row["state"]),
        attempt_count=int(row["attempt_count"]),
    )
