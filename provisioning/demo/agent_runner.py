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
from daher_agent.executor import ComposeExecutor
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
    while not ((SHARED / "agent_credential").is_file() and (SHARED / "tls" / "real-ca.pem").is_file()):
        time.sleep(0.5)
    credential = (SHARED / "agent_credential").read_text(encoding="utf-8").strip()

    # ⚠️ `verify` is the whole point of the TLS leg. It is read from the environment so the
    # controls can point it at a BOGUS CA and prove the agent refuses — a demonstration that
    # only ever presents the correct certificate would pass identically with verification
    # disabled, and would therefore prove nothing.
    verify: bool | str = os.environ.get("AGENT_CA_BUNDLE") or True
    transport = HttpTransport(os.environ["PROVISIONING_URL"], credential, verify=verify)
    validator = JobValidator(host_id=HOST_ID, facility_exists=lambda fid: False)

    compose_file = Path(os.environ.get("FACILITY_COMPOSE", "")) if os.environ.get(
        "FACILITY_COMPOSE"
    ) else None
    real = (
        ComposeExecutor(
            compose_file=compose_file,
            facility_root=Path(os.environ.get("FACILITY_ROOT", "/facilities")),
            base_domain=os.environ.get("BASE_DOMAIN", "localhost"),
        ).execute
        if compose_file
        else None
    )

    def execute(job: Job) -> ExecutionResult:
        """Run the REAL executor when a compose file is present, otherwise record only.

        With `FACILITY_COMPOSE` set, this invokes `docker compose` against a real daemon —
        which is the part unit tests could never exercise.
        """
        note(EXECUTED, {"job_id": job.job_id, "facility_id": job.params.facility_id})
        if real is None:
            return ExecutionResult(True, "demo executor (no compose file mounted)")
        result = real(job)
        note(EXECUTED, {"job_id": job.job_id, "real_executor": True,
                        "succeeded": result.succeeded, "detail": result.detail[:200]})
        return result

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
