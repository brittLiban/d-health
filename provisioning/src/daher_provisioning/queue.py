"""Per-host job queues (ADR-022).

Jobs are addressed to exactly one host and can only be claimed by that host. The agent
still re-validates everything it pulls — this queue is routing, not security. Anything here
that looked like a security control would be a control an attacker who owns the API can
simply not apply.

A claimed job is held, not deleted, so a crash between claiming and completing leaves the
job visible rather than silently lost. That mirrors the adapter's DH-025 reasoning: work
that vanished is worse than work that is stuck, because stuck is visible.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from daher_provisioning.jobs import Job


class JobStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    # A refusal is not a failure of the system; it is the boundary working. Kept distinct
    # so "the agent refused hostile input" never reads as "the deployment broke".
    REFUSED = "refused"
    FAILED = "failed"


@dataclass
class QueuedJob:
    job: Job
    status: JobStatus = JobStatus.QUEUED
    # ⚠️ Which host this was HANDED TO, recorded at claim time. Authorisation for reporting
    # must use this, never the job's self-declared `host_id` — a compromised issuer controls
    # the payload, so a misrouted job would otherwise be refused by the agent and then be
    # unreportable, leaving the most security-relevant event invisible to the audit log.
    # Found by the two-container demonstration; unit tests could not see it because they
    # injected a reporter that always succeeded.
    delivered_to: str | None = None
    claimed_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str = ""


class QueueError(RuntimeError):
    """The queue cannot satisfy this request."""


@dataclass
class JobQueue:
    _pending: dict[str, deque[str]] = field(default_factory=lambda: defaultdict(deque))
    _jobs: dict[str, QueuedJob] = field(default_factory=dict)

    def enqueue(self, job: Job) -> QueuedJob:
        if job.job_id in self._jobs:
            raise QueueError(f"job {job.job_id!r} is already queued")
        record = QueuedJob(job=job)
        self._jobs[job.job_id] = record
        self._pending[job.host_id].append(job.job_id)
        return record

    def claim(self, host_id: str) -> Job | None:
        """Hand the next job to the host it is addressed to. Never to any other host."""
        queue = self._pending.get(host_id)
        while queue:
            job_id = queue.popleft()
            record = self._jobs[job_id]
            if record.status is not JobStatus.QUEUED:
                continue
            record.status = JobStatus.CLAIMED
            record.claimed_at = datetime.now(UTC)
            record.delivered_to = host_id
            return record.job
        return None

    def finish(self, job_id: str, status: JobStatus, detail: str = "") -> QueuedJob:
        record = self._jobs.get(job_id)
        if record is None:
            raise QueueError(f"unknown job {job_id!r}")
        if status is JobStatus.QUEUED:
            raise QueueError("a job cannot be finished back into the queued state")
        record.status = status
        record.finished_at = datetime.now(UTC)
        record.detail = detail[:512]
        return record

    def get(self, job_id: str) -> QueuedJob:
        record = self._jobs.get(job_id)
        if record is None:
            raise QueueError(f"unknown job {job_id!r}")
        return record

    def pending_count(self, host_id: str) -> int:
        return sum(
            1
            for job_id in self._pending.get(host_id, ())
            if self._jobs[job_id].status is JobStatus.QUEUED
        )
