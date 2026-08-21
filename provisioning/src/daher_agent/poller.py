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

⚠️ **This loop is synchronous, and that has a consequence worth knowing before it surprises
someone.** `poll_once` runs the executor inline, so while a `provision` is bringing a stack
up — up to `DEFAULT_STACK_TIMEOUT_SECONDS`, and a measured cold boot is ≈49 minutes — this
agent is not polling at all. Two things follow:

1. A `stop`, `status` or `backup_now` issued during that window is not acted on until the
   provision finishes.
2. By then it will usually be **refused as stale**, because `JobValidator` rejects any job
   older than `DEFAULT_MAX_AGE_SECONDS` (900s). That refusal is loud, audited, and safe —
   nothing is silently skipped — but the operator must re-issue the job.

This is an accepted limit of bug 17's fix, not an oversight: widening the staleness window
to paper over it would weaken ADR-022's replay protection, which is a security property and
therefore a decision rather than a tuning constant. Removing the limit properly means
running the long verb off this thread, which puts concurrency into the component whose value
is partly that it is simple. Recorded as known bug 18.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from daher_agent.validation import JobRefused, JobValidator
from daher_provisioning.jobs import Job
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

    def poll_once(self) -> str | None:
        """Fetch, validate and run at most one job. Returns the outcome, or None if idle.

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

        try:
            result = self._execute(job)
        except Exception as exc:
            logger.error("execution_failed job_id=%s", job.job_id, exc_info=True)
            self._safe_report(job.job_id, "failed", f"executor raised: {type(exc).__name__}")
            return "failed"

        status = "succeeded" if result.succeeded else "failed"
        self._safe_report(job.job_id, status, result.detail, result.infrastructure)
        return status

    def _safe_report(
        self,
        job_id: str,
        status: str,
        detail: str,
        infrastructure: InfrastructureReport | None = None,
    ) -> None:
        """Reporting is best effort. A failure to report must not stop the loop."""
        try:
            try:
                self._report(job_id, status, detail, infrastructure)
            except TypeError:
                # A reporter written before DH-027D takes three arguments. Falling back keeps
                # a mixed-version fleet reporting outcomes rather than silently failing every
                # report — the outcome still lands, only the structured evidence is dropped.
                self._report(job_id, status, detail)
        except Exception:
            logger.warning("report_failed job_id=%s", job_id, exc_info=True)
