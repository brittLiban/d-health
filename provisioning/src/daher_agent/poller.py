"""The facility agent's poll loop (ADR-022).

⚠️ **Status: WRITTEN, NOT DEMONSTRATED.** The loop logic below is unit-tested against
injected transport and executor. What is *not* proven on a single machine is the claim this
design actually rests on — "polls a separate host over a network and refuses hostile input
over the wire". That needs two hosts, or two containers on separate networks standing in
for them. Until that exists, treat this as written code, not verified behaviour.

Two properties the loop must hold, both of which are about *staying alive*:

1. **A refusal never stops the loop.** An agent that crashes on hostile input has been
   turned off by the attacker, and a host that is not polling is also not getting backups
   or updates. Refuse, report, keep polling.
2. **The agent reports outcomes, but the API's own record is authoritative** (ADR-021).
   What the agent says happened is a claim; the API logs what it observed.

The executor is injected so that shelling out to `docker compose` is never exercised by a
unit test pretending to be a deployment.

**Bug 18 — fixed.** `execute` used to run inline inside `poll_once`, so while a `provision`
brought a stack up — up to 5400s — the agent did not poll at all, and anything queued in
that window was refused as stale by the time the loop came back (`JobValidator` rejects any
job older than 900s). `execute` now runs on a background thread, so `poll_once` returns as
soon as the job is dispatched and the loop keeps calling `fetch` on its normal cadence.

**What did *not* change:** the fixed command/security model. `JobValidator` still runs
synchronously, on the calling thread, before anything is dispatched — a job is validated
exactly once, by exactly one thread, with the same rules as before. Nothing here builds a
command, touches `subprocess`, or widens the staleness window; those all remain
`executor.py` and `validation.py`, untouched. The queue's per-host FIFO dispatch order
(`JobQueue.claim`) is unchanged too — this only changes when the *agent* finishes acting on
what it already claimed, never the order the *API* hands work out in.

**The concurrency this buys, and the limit that stays on purpose.** `STATUS` is read-only
(`docker compose ps`/`config`), so it can run at any time, including while a mutating verb
is executing against the same compose project. Every other verb (`provision`, `start`,
`stop`, `rotate_tls`, `backup_now`) mutates that project, so **at most one runs at a time**
— a second one claimed while the first is still in flight is refused immediately, loudly,
rather than racing two `docker compose` invocations against the same project. That refusal
is reported the same way a validation refusal is: audited, and safe, and the operator must
re-issue the job — this was already an accepted cost of bug 17's fix; only the timing and
the reason are better now. Two slots (one mutating, one status) also bounds the thread
count: a flood of queued jobs cannot make this agent spawn unboundedly many threads.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from daher_agent.validation import JobRefused, JobValidator
from daher_provisioning.jobs import Job, JobVerb
from daher_provisioning.models import InfrastructureReport

logger = logging.getLogger("daher_agent.poller")


@dataclass(frozen=True)
class ExecutionResult:
    succeeded: bool
    detail: str = ""
    # Structured infrastructure evidence for STATUS (DH-027D). None for every other verb.
    # Deliberately typed rather than stuffed into `detail`: `detail` is a truncated string
    # and the control plane must validate what it persists, not scrape it.
    infrastructure: InfrastructureReport | None = None


# Fetch one job payload addressed to this host, or None. Raising is allowed and expected —
# the network is unreliable and the loop must survive that.
FetchJob = Callable[[], dict | None]
# Run a validated job. Only ever called with a job that passed every local refusal.
ExecuteJob = Callable[[Job], ExecutionResult]
# Report an outcome upstream. Best effort; failing to report must not stop the loop.
# (job_id, status, detail, infrastructure|None). The fourth argument is DH-027D's structured
# evidence; reporters that predate it are still accepted, see `_safe_report`.
ReportOutcome = Callable[..., None]


@dataclass
class _Slot:
    """One background execution slot. `job` is set while something occupies it."""

    job: Job | None = None
    thread: threading.Thread | None = None


@dataclass
class _Slots:
    """The agent's two execution slots (bug 18).

    `status` is read-only and never blocks or is blocked by anything. `mutating` covers
    every verb that runs `docker compose` against the facility's project — only one may be
    in flight, because two concurrent invocations against the same compose project is
    exactly the race ADR-022's "the compose file lives on the facility host" guarantee
    exists alongside, not a concurrency question that guarantee answers on its own.
    """

    status: _Slot = field(default_factory=_Slot)
    mutating: _Slot = field(default_factory=_Slot)

    def for_verb(self, verb: JobVerb) -> _Slot:
        return self.status if verb is JobVerb.STATUS else self.mutating


class AgentPoller:
    def __init__(
        self,
        *,
        validator: JobValidator,
        fetch: FetchJob,
        execute: ExecuteJob,
        report: ReportOutcome | None = None,
    ) -> None:
        self._validator = validator
        self._fetch = fetch
        self._execute = execute
        self._report = report or (lambda job_id, status, detail: None)
        self._lock = threading.Lock()
        self._report_lock = threading.Lock()
        self._slots = _Slots()

    def poll_once(self) -> str | None:
        """Fetch, validate and dispatch at most one job. Never blocks on execution.

        Returns immediately once a job is handed to its execution slot — the outcome is
        reported asynchronously, on the thread that ran it, when it finishes. Return values:

        * ``None`` — nothing was waiting.
        * ``"refused"`` — validation refused the job before it ever reached execution.
        * ``"busy"`` — the job's slot (mutating or status) is already occupied; refused
          without executing, for the same reason a hostile job is refused: loudly, and
          without racing the thing already running.
        * ``"dispatched"`` — handed to a background thread. Not an outcome; call `join` (as
          tests do) to observe what it reports.

        This never raises. Every failure mode — an unreachable API, a malformed payload, a
        hostile parameter, an executor that blew up — is caught, reported and survived,
        because the alternative is an agent that an attacker can switch off by sending it
        something bad.
        """
        try:
            payload = self._fetch()
        except Exception:
            logger.warning("fetch_failed", exc_info=True)
            return None

        if payload is None:
            return None

        try:
            job = self._validator.validate_and_accept(payload)
        except JobRefused as refusal:
            # A refusal is the boundary working, not a malfunction. Report it as such so it
            # is never read as "the deployment broke".
            job_id = str(payload.get("job_id", "unknown"))[:64]
            logger.warning("job_refused job_id=%s reason=%s", job_id, refusal)
            self._safe_report(job_id, "refused", str(refusal))
            return "refused"
        except Exception:
            logger.error("validation_error", exc_info=True)
            return "refused"

        slot = self._slots.for_verb(job.verb)
        is_status = job.verb is JobVerb.STATUS
        with self._lock:
            blocker = slot.job
            if blocker is None:
                slot.job = job

        # Reported outside the lock: `_safe_report` calls into a network client in
        # production, and a poll thread must never hold `self._lock` across I/O — a worker
        # finishing at the same moment would otherwise stall in its own `finally` waiting for
        # this lock, for as long as the report takes.
        if blocker is not None:
            kind = "a status check" if is_status else "a mutating operation"
            detail = (
                f"refused: {blocker.verb.value} job {blocker.job_id} is still executing on "
                f"this host; only one {kind} runs at a time (bug 18)"
            )
            logger.warning("job_busy job_id=%s blocked_by=%s", job.job_id, blocker.job_id)
            self._safe_report(job.job_id, "refused", detail)
            return "busy"

        thread = threading.Thread(target=self._run, args=(job, slot), daemon=True)
        slot.thread = thread
        thread.start()
        return "dispatched"

    def _run(self, job: Job, slot: _Slot) -> None:
        """Execute one dispatched job off the poll loop's thread, then report it."""
        try:
            result = self._execute(job)
        except Exception as exc:
            logger.error("execution_failed job_id=%s", job.job_id, exc_info=True)
            self._safe_report(job.job_id, "failed", f"executor raised: {type(exc).__name__}")
            return
        finally:
            with self._lock:
                slot.job = None
        status = "succeeded" if result.succeeded else "failed"
        logger.info("job_finished job_id=%s status=%s", job.job_id, status)
        self._safe_report(job.job_id, status, result.detail, result.infrastructure)

    def join(self, timeout: float | None = None) -> bool:
        """Wait for whatever is currently executing to finish.

        Used by tests to observe an async outcome deterministically, and by graceful
        shutdown (`run_forever`) so the transport is not closed under a report still in
        flight. Returns ``True`` once both slots are idle, ``False`` if `timeout` elapsed
        first. Untimed (the default) matches the pre-bug-18 behaviour, where the loop could
        not shut down mid-job either.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        for slot in (self._slots.status, self._slots.mutating):
            thread = slot.thread
            if thread is None:
                continue
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
        return not any(
            slot.thread is not None and slot.thread.is_alive()
            for slot in (self._slots.status, self._slots.mutating)
        )

    def _safe_report(
        self,
        job_id: str,
        status: str,
        detail: str,
        infrastructure: InfrastructureReport | None = None,
    ) -> None:
        """Reporting is best effort. A failure to report must not stop the loop.

        Serialised by its own lock: two slots can finish close together and both call this
        from different threads, and the reporter (an HTTP client in production) is not
        asked to prove it tolerates concurrent calls.
        """
        try:
            with self._report_lock:
                try:
                    self._report(job_id, status, detail, infrastructure)
                except TypeError:
                    # A reporter written before DH-027D takes three arguments. Falling back
                    # keeps a mixed-version fleet reporting outcomes rather than silently
                    # failing every report — the outcome still lands, only the structured
                    # evidence is dropped.
                    self._report(job_id, status, detail)
        except Exception:
            logger.warning("report_failed job_id=%s", job_id, exc_info=True)
