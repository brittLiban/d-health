"""Durable storage for the provisioning control plane.

Everything before this was in-memory, which meant an API restart lost the entire fleet:
every facility record, every host registration, every agent enrolment. Recovery would have
been re-enrolling every VM by hand.

SQLite rather than a database server: this is one control plane on one host, the data is
small, and ACID with no daemon to run, patch, or back up separately is exactly the right
trade. If it ever outgrows that, the interfaces below are the seam.

⚠️ **No clinical credential is ever stored here.** Facility secrets are generated on the
facility host and written to its own env file (ADR-022); this database holds identities and
*hashes*. A test asserts no column is named for a password or secret — the same structural
guarantee as the no-clinical-data rule, applied to the store.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from daher_provisioning.auth import ApiKey, Scope
from daher_provisioning.enrolment import EnrolledAgent, EnrolmentToken
from daher_provisioning.hosts import RegisteredHost
from daher_provisioning.jobs import Job
from daher_provisioning.models import DeployTarget, Facility
from daher_provisioning.queue import JobStatus, QueuedJob

SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities (
    facility_id TEXT PRIMARY KEY,
    document    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hosts (
    host_id     TEXT PRIMARY KEY,
    target      TEXT NOT NULL,
    enrolled    INTEGER NOT NULL DEFAULT 0,
    facility_id TEXT
);
CREATE TABLE IF NOT EXISTS enrolment_tokens (
    host_id     TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    redeemed_at TEXT
);
CREATE TABLE IF NOT EXISTS enrolled_agents (
    host_id         TEXT PRIMARY KEY,
    credential_hash TEXT NOT NULL,
    enrolled_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    key_id   TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL,
    scopes   TEXT NOT NULL,
    actor    TEXT NOT NULL,
    active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS facility_health (
    facility_id TEXT PRIMARY KEY,
    document    TEXT NOT NULL,
    checked_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facility_backup (
    facility_id TEXT PRIMARY KEY,
    document    TEXT NOT NULL,
    checked_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    host_id      TEXT NOT NULL,
    document     TEXT NOT NULL,
    status       TEXT NOT NULL,
    delivered_to TEXT,
    claimed_at   TEXT,
    finished_at  TEXT,
    detail       TEXT NOT NULL DEFAULT '',
    sequence     INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs (host_id, status, sequence);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        # WAL so a reader never blocks the writer; FULL synchronous because losing an
        # acknowledged enrolment to a power cut would mean re-enrolling a host by hand.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._connection
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self._connection.execute(sql, params))

    def close(self) -> None:
        self._connection.close()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqliteFacilityStore:
    """Facility records. Same interface as the in-memory repository."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, facility: Facility) -> Facility:
        from daher_provisioning.repository import FacilityExists

        if self._db.query("SELECT 1 FROM facilities WHERE facility_id=?", (facility.facility_id,)):
            raise FacilityExists(
                f"facility {facility.facility_id!r} already exists. Facility ids are "
                f"permanent and namespace the volumes; reusing one would attach this "
                f"facility to another's patient database."
            )
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO facilities (facility_id, document) VALUES (?, ?)",
                (facility.facility_id, facility.model_dump_json()),
            )
        return facility

    def get(self, facility_id: str) -> Facility:
        from daher_provisioning.repository import FacilityNotFound

        rows = self._db.query("SELECT document FROM facilities WHERE facility_id=?", (facility_id,))
        if not rows:
            raise FacilityNotFound(f"no facility {facility_id!r}")
        return Facility.model_validate_json(rows[0]["document"])

    def list(self) -> list[Facility]:
        return [
            Facility.model_validate_json(row["document"])
            for row in self._db.query("SELECT document FROM facilities ORDER BY facility_id")
        ]

    def set_state(self, facility_id: str, state) -> Facility:
        updated = self.get(facility_id).model_copy(update={"state": state})
        with self._db.write() as conn:
            conn.execute(
                "UPDATE facilities SET document=? WHERE facility_id=?",
                (updated.model_dump_json(), facility_id),
            )
        return updated


class SqliteHealthStore:
    """Latest infrastructure check per facility (DH-027D).

    **Latest only, deliberately.** A fleet dashboard needs "what is true now"; a history of
    container states is a metrics problem, and building one here would turn the control plane
    into a telemetry platform for no current need. The audit log already records that each
    check happened.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, facility_id: str, report) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO facility_health (facility_id, document, checked_at) VALUES (?,?,?) "
                "ON CONFLICT(facility_id) DO UPDATE SET document=excluded.document, "
                "checked_at=excluded.checked_at",
                (facility_id, report.model_dump_json(), report.checked_at.isoformat()),
            )

    def latest(self, facility_id: str):
        from daher_provisioning.models import InfrastructureReport

        rows = self._db.query(
            "SELECT document FROM facility_health WHERE facility_id=?", (facility_id,)
        )
        return InfrastructureReport.model_validate_json(rows[0]["document"]) if rows else None


class InMemoryHealthStore:
    """Same interface, for tests and the default in-memory wiring."""

    def __init__(self) -> None:
        self._latest: dict = {}

    def record(self, facility_id: str, report) -> None:
        self._latest[facility_id] = report

    def latest(self, facility_id: str):
        return self._latest.get(facility_id)


class SqliteBackupStore:
    """Latest BACKUP_NOW report per facility (DH-027G).

    Same shape as `SqliteHealthStore` on purpose: one durable fact per facility, replaced
    on every new report, because "is this facility protected right now" is what an operator
    needs — not a history of every backup attempt. Never holds a passphrase, bucket name, or
    credential; `BackupReport` structurally cannot carry one (ADR-021's pattern, applied here).
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, facility_id: str, report) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO facility_backup (facility_id, document, checked_at) VALUES (?,?,?) "
                "ON CONFLICT(facility_id) DO UPDATE SET document=excluded.document, "
                "checked_at=excluded.checked_at",
                (facility_id, report.model_dump_json(), report.checked_at.isoformat()),
            )

    def latest(self, facility_id: str):
        from daher_provisioning.models import BackupReport

        rows = self._db.query(
            "SELECT document FROM facility_backup WHERE facility_id=?", (facility_id,)
        )
        return BackupReport.model_validate_json(rows[0]["document"]) if rows else None


class InMemoryBackupStore:
    """Same interface, for tests and the default in-memory wiring."""

    def __init__(self) -> None:
        self._latest: dict = {}

    def record(self, facility_id: str, report) -> None:
        self._latest[facility_id] = report

    def latest(self, facility_id: str):
        return self._latest.get(facility_id)


class SqliteHostStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def _row(self, host_id: str):
        from daher_provisioning.hosts import HostError

        rows = self._db.query("SELECT * FROM hosts WHERE host_id=?", (host_id,))
        if not rows:
            raise HostError(
                f"host {host_id!r} is not registered. Deploy targets are resolved against "
                f"hosts an operator enrolled, never taken from the request — otherwise a "
                f"caller could name any address it liked (ADR-022)."
            )
        return rows[0]

    def register(self, host_id: str, target: DeployTarget) -> RegisteredHost:
        from daher_provisioning.hosts import HOST_ID_PATTERN, HostError

        if not HOST_ID_PATTERN.match(host_id):
            raise HostError(
                f"host id {host_id!r} must be 3-64 characters of lowercase letters, digits "
                f"and hyphens"
            )
        if self._db.query("SELECT 1 FROM hosts WHERE host_id=?", (host_id,)):
            raise HostError(f"host {host_id!r} is already registered")
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO hosts (host_id, target, enrolled, facility_id) VALUES (?,?,0,NULL)",
                (host_id, target.model_dump_json()),
            )
        return RegisteredHost(host_id=host_id, target=target)

    def get(self, host_id: str) -> RegisteredHost:
        row = self._row(host_id)
        return RegisteredHost(
            host_id=row["host_id"],
            target=DeployTarget.model_validate_json(row["target"]),
            enrolled=bool(row["enrolled"]),
            facility_id=row["facility_id"],
        )

    def mark_enrolled(self, host_id: str) -> RegisteredHost:
        self._row(host_id)
        with self._db.write() as conn:
            conn.execute("UPDATE hosts SET enrolled=1 WHERE host_id=?", (host_id,))
        return self.get(host_id)

    def claim(self, host_id: str, facility_id: str) -> RegisteredHost:
        from daher_provisioning.hosts import HostError

        host = self.get(host_id)
        if not host.enrolled:
            raise HostError(
                f"host {host_id!r} is registered but its agent has not enrolled yet. "
                f"Run the bootstrap step on that host first (ADR-022)."
            )
        if host.facility_id is not None:
            raise HostError(
                f"host {host_id!r} already runs facility {host.facility_id!r}. Refusing to "
                f"deploy a second facility onto it: that would overwrite a live env file "
                f"and attach this facility to an existing patient database."
            )
        with self._db.write() as conn:
            # Conditional update: two concurrent claims cannot both succeed, because the
            # second finds facility_id already set and changes zero rows.
            cursor = conn.execute(
                "UPDATE hosts SET facility_id=? WHERE host_id=? AND facility_id IS NULL",
                (facility_id, host_id),
            )
            if cursor.rowcount != 1:
                raise HostError(f"host {host_id!r} was claimed concurrently")
        return self.get(host_id)

    def release(self, host_id: str, facility_id: str) -> bool:
        """Undo a claim this same request just made (DH-027B). Compensation, not an operation.

        Conditional in SQL rather than checked first, so a concurrent claim cannot slip
        between the read and the write: the `AND facility_id=?` predicate means a call
        naming a facility that no longer holds this host changes zero rows and returns
        False. See `HostRegistry.release` for why an unconditional release is not offered.
        """
        with self._db.write() as conn:
            cursor = conn.execute(
                "UPDATE hosts SET facility_id=NULL WHERE host_id=? AND facility_id=?",
                (host_id, facility_id),
            )
            return cursor.rowcount == 1

    def list(self) -> list[RegisteredHost]:
        return [
            self.get(row["host_id"])
            for row in self._db.query("SELECT host_id FROM hosts ORDER BY host_id")
        ]


class SqliteKeyStore:
    """API credentials. Stores hashes only — never a presentable key."""

    def __init__(self, db: Database, keys: list[ApiKey] | None = None) -> None:
        self._db = db
        import secrets as _secrets

        from daher_provisioning.auth import hash_key

        self._decoy_hash = hash_key(_secrets.token_urlsafe(32))
        for key in keys or []:
            self.add(key)

    def add(self, key: ApiKey) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO api_keys (key_id, key_hash, scopes, actor, active) "
                "VALUES (?,?,?,?,?)",
                (
                    key.key_id,
                    key.key_hash,
                    json.dumps([s.value for s in key.scopes]),
                    key.actor,
                    int(key.active),
                ),
            )

    def revoke(self, key_id: str) -> None:
        with self._db.write() as conn:
            conn.execute("UPDATE api_keys SET active=0 WHERE key_id=?", (key_id,))

    def authenticate(self, presented: str | None):
        import secrets as _secrets

        from daher_provisioning.auth import (
            KEY_PREFIX,
            AuthenticationError,
            Principal,
            hash_key,
        )

        if not presented:
            raise AuthenticationError("no credential presented")
        parts = presented.split("_", 2)
        if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[1] or not parts[2]:
            raise AuthenticationError("malformed credential")
        key_id, secret = parts[1], parts[2]

        rows = self._db.query("SELECT * FROM api_keys WHERE key_id=?", (key_id,))
        row = rows[0] if rows else None
        # Compare regardless, so an unknown key id costs the same work as a known one.
        expected = row["key_hash"] if row is not None else self._decoy_hash
        matched = _secrets.compare_digest(hash_key(secret), expected)

        if row is None or not matched:
            raise AuthenticationError(f"invalid credential for key id {key_id!r}")
        if not row["active"]:
            raise AuthenticationError(f"credential {key_id!r} is revoked")
        return Principal(
            key_id=row["key_id"],
            actor=row["actor"],
            scopes=frozenset(Scope(s) for s in json.loads(row["scopes"])),
        )


class SqliteEnrolmentStore:
    def __init__(self, db: Database, lifetime_seconds: int = 3600) -> None:
        self._db = db
        self.lifetime_seconds = lifetime_seconds

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def mint(self, host_id: str) -> tuple[str, EnrolmentToken]:
        import secrets as _secrets
        from datetime import timedelta

        from daher_provisioning.enrolment import TOKEN_PREFIX, EnrolmentError, _hash

        if not host_id or "_" in host_id:
            raise EnrolmentError("host id must be non-empty and contain no underscore")
        if self._db.query("SELECT 1 FROM enrolled_agents WHERE host_id=?", (host_id,)):
            raise EnrolmentError(
                f"host {host_id!r} is already enrolled. Re-enrolling would replace a live "
                f"agent's credential; revoke it deliberately first."
            )
        secret = _secrets.token_urlsafe(32)
        expires = self._now() + timedelta(seconds=self.lifetime_seconds)
        with self._db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO enrolment_tokens "
                "(host_id, token_hash, expires_at, redeemed_at) VALUES (?,?,?,NULL)",
                (host_id, _hash(secret), expires.isoformat()),
            )
        return f"{TOKEN_PREFIX}_{host_id}_{secret}", EnrolmentToken(
            host_id=host_id, token_hash=_hash(secret), expires_at=expires
        )

    def redeem(self, presented: str, *, credential_hash: str) -> EnrolledAgent:
        import secrets as _secrets

        from daher_provisioning.enrolment import TOKEN_PREFIX, EnrolmentError, _hash

        parts = (presented or "").split("_", 2)
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX or not parts[1] or not parts[2]:
            raise EnrolmentError("malformed enrollment token")
        host_id, secret = parts[1], parts[2]

        rows = self._db.query("SELECT * FROM enrolment_tokens WHERE host_id=?", (host_id,))
        row = rows[0] if rows else None
        expected = row["token_hash"] if row is not None else _hash(_secrets.token_urlsafe(32))
        if row is None or not _secrets.compare_digest(_hash(secret), expected):
            raise EnrolmentError(f"invalid enrollment token for host {host_id!r}")
        if row["redeemed_at"]:
            raise EnrolmentError(
                f"enrollment token for {host_id!r} was already redeemed. Tokens are "
                f"single-use: a replayed one is refused even inside its lifetime."
            )
        expires = _parse(row["expires_at"])
        if expires and self._now() > expires:
            raise EnrolmentError(
                f"enrollment token for {host_id!r} expired at {expires.isoformat()}. "
                f"Mint a new one rather than extending this."
            )
        if len(credential_hash) != 64:
            raise EnrolmentError(
                "credential_hash must be a 64-character SHA-256 hex digest. The agent "
                "generates its credential locally and sends only the hash — a secret must "
                "never reach the provisioning host."
            )

        now = self._now()
        with self._db.write() as conn:
            # Conditional: a concurrent second redemption changes zero rows and is refused,
            # so single-use survives two requests arriving together.
            cursor = conn.execute(
                "UPDATE enrolment_tokens SET redeemed_at=? WHERE host_id=? AND redeemed_at IS NULL",
                (now.isoformat(), host_id),
            )
            if cursor.rowcount != 1:
                raise EnrolmentError(f"enrollment token for {host_id!r} was already redeemed")
            conn.execute(
                "INSERT OR REPLACE INTO enrolled_agents (host_id, credential_hash, enrolled_at) "
                "VALUES (?,?,?)",
                (host_id, credential_hash, now.isoformat()),
            )
        return EnrolledAgent(host_id=host_id, credential_hash=credential_hash, enrolled_at=now)

    def agent(self, host_id: str) -> EnrolledAgent:
        from daher_provisioning.enrolment import EnrolmentError

        rows = self._db.query("SELECT * FROM enrolled_agents WHERE host_id=?", (host_id,))
        if not rows:
            raise EnrolmentError(f"host {host_id!r} has not enrolled")
        return EnrolledAgent(
            host_id=rows[0]["host_id"],
            credential_hash=rows[0]["credential_hash"],
            enrolled_at=_parse(rows[0]["enrolled_at"]),
        )

    def is_enrolled(self, host_id: str) -> bool:
        return bool(self._db.query("SELECT 1 FROM enrolled_agents WHERE host_id=?", (host_id,)))


class SqliteJobQueue:
    """Durable job queue.

    Persisting this is what makes "held, not deleted" survive an **API restart**, not just an
    agent crash. A claimed job that vanished when the control plane restarted would be the
    same silent loss the in-memory queue was written to avoid.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def enqueue(self, job: Job) -> QueuedJob:
        from daher_provisioning.queue import QueueError

        if self._db.query("SELECT 1 FROM jobs WHERE job_id=?", (job.job_id,)):
            raise QueueError(f"job {job.job_id!r} is already queued")
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, host_id, document, status, sequence) "
                "VALUES (?,?,?,?, (SELECT COALESCE(MAX(sequence),0)+1 FROM jobs))",
                (job.job_id, job.host_id, job.model_dump_json(), JobStatus.QUEUED.value),
            )
        return QueuedJob(job=job)

    def claim(self, host_id: str) -> Job | None:
        rows = self._db.query(
            "SELECT job_id, document FROM jobs WHERE host_id=? AND status=? "
            "ORDER BY sequence LIMIT 1",
            (host_id, JobStatus.QUEUED.value),
        )
        if not rows:
            return None
        with self._db.write() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status=?, claimed_at=?, delivered_to=? "
                "WHERE job_id=? AND status=?",
                (
                    JobStatus.CLAIMED.value,
                    datetime.now(UTC).isoformat(),
                    host_id,
                    rows[0]["job_id"],
                    JobStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                return None  # claimed concurrently
        return Job.model_validate_json(rows[0]["document"])

    def get(self, job_id: str) -> QueuedJob:
        from daher_provisioning.queue import QueueError

        rows = self._db.query("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if not rows:
            raise QueueError(f"unknown job {job_id!r}")
        row = rows[0]
        return QueuedJob(
            job=Job.model_validate_json(row["document"]),
            status=JobStatus(row["status"]),
            delivered_to=row["delivered_to"],
            claimed_at=_parse(row["claimed_at"]),
            finished_at=_parse(row["finished_at"]),
            detail=row["detail"],
        )

    def finish(self, job_id: str, status: JobStatus, detail: str = "") -> QueuedJob:
        from daher_provisioning.queue import QueueError

        if status is JobStatus.QUEUED:
            raise QueueError("a job cannot be finished back into the queued state")
        self.get(job_id)
        with self._db.write() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, finished_at=?, detail=? WHERE job_id=?",
                (status.value, datetime.now(UTC).isoformat(), detail[:512], job_id),
            )
        return self.get(job_id)

    def pending_count(self, host_id: str) -> int:
        rows = self._db.query(
            "SELECT COUNT(*) AS n FROM jobs WHERE host_id=? AND status=?",
            (host_id, JobStatus.QUEUED.value),
        )
        return int(rows[0]["n"])
