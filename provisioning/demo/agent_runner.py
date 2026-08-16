"""Demonstration agent: the REAL poller and validator, over a real socket.

The executor is substituted (nothing here should start a clinical stack), but everything
that matters for ADR-022 — the HTTP transport, the local validation, the refusal path — is
the production code path.

It records what it did to a shared file so the harness can assert on **why** a job was
refused, not merely that it was. A refusal for the wrong reason would pass a naive check.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from daher_agent.poller import AgentPoller, ExecutionResult
from daher_agent.runner import HttpTransport
from daher_agent.validation import JobRefused, JobValidator
from daher_provisioning.jobs import Job

SHARED = Path(os.environ.get("DEMO_SHARED", "/shared"))
HOST_ID = os.environ.get("HOST_ID", "host-a")
OUTCOMES = SHARED / "agent_outcomes.jsonl"
EXECUTED = SHARED / "agent_executed.jsonl"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("demo.agent")


def note(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def main() -> None:
    while not (SHARED / "agent_credential").is_file():
        time.sleep(0.5)
    credential = (SHARED / "agent_credential").read_text(encoding="utf-8").strip()

    transport = HttpTransport(os.environ["PROVISIONING_URL"], credential)
    validator = JobValidator(host_id=HOST_ID, facility_exists=lambda fid: False)

    def execute(job: Job) -> ExecutionResult:
        # Deliberately does NOT start a stack. Reaching here at all is the assertion: only
        # a job that survived every local refusal may be executed.
        note(EXECUTED, {"job_id": job.job_id, "facility_id": job.params.facility_id})
        return ExecutionResult(True, "demo executor")

    def report(job_id: str, status: str, detail: str) -> None:
        note(OUTCOMES, {"job_id": job_id, "status": status, "detail": detail})
        transport.report(job_id, status, detail)

    poller = AgentPoller(
        validator=validator, fetch=transport.fetch, execute=execute, report=report
    )

    # Wrap validation so the harness can see the refusal REASON, not just the outcome.
    original_validate = validator.validate_and_accept

    def traced(payload: dict):
        try:
            return original_validate(payload)
        except JobRefused as refusal:
            note(
                OUTCOMES,
                {
                    "job_id": str(payload.get("job_id", "unknown")),
                    "status": "refused",
                    "reason_class": type(refusal).__name__,
                    "reason": str(refusal),
                },
            )
            raise

    validator.validate_and_accept = traced  # type: ignore[method-assign]

    logger.info("agent polling %s as %s", os.environ["PROVISIONING_URL"], HOST_ID)
    (SHARED / "agent_ready").write_text("ok", encoding="utf-8")
    while True:
        poller.poll_once()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
