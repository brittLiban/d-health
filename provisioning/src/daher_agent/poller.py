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
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from daher_agent.validation import JobRefused, JobValidator
from daher_provisioning.jobs import Job

logger = logging.getLogger("daher_agent.poller")


@dataclass(frozen=True)
class ExecutionResult:
    succeeded: bool
    detail: str = ""


# Fetch one job payload addressed to this host, or None. Raising is allowed and expected —
# the network is unreliable and the loop must survive that.
FetchJob = Callable[[], dict | None]
# Run a validated job. Only ever called with a job that passed every local refusal.
ExecuteJob = Callable[[Job], ExecutionResult]
# Report an outcome upstream. Best effort; failing to report must not stop the loop.
ReportOutcome = Callable[[str, str, str], None]


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
        self._safe_report(job.job_id, status, result.detail)
        return status

    def _safe_report(self, job_id: str, status: str, detail: str) -> None:
        """Reporting is best effort. A failure to report must not stop the loop."""
        try:
            self._report(job_id, status, detail)
        except Exception:
            logger.warning("report_failed job_id=%s", job_id, exc_info=True)
