"""Demonstration API server: the REAL provisioning app, pre-seeded and served.

Nothing here stubs the transport. It wires the actual `create_app` with a genuinely enrolled
agent credential, then serves it so a separate container can poll it over a real socket.

The one addition is a harness hatch, `/demo/enqueue-raw`, which puts an **arbitrary payload**
on the queue — including payloads the typed API would refuse to construct. That is the point:
ADR-022's residual risk is *a compromised API enqueues hostile parameters*, so the
demonstration has to be able to act like a compromised API. Without this hatch the demo would
only ever prove the agent accepts jobs the API already validated, which proves nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from daher_provisioning.app import create_app
from daher_provisioning.audit import AuditLog
from daher_provisioning.auth import PORTAL_SCOPES, KeyStore, issue_key
from daher_provisioning.enrolment import EnrolmentService, generate_agent_credential
from daher_provisioning.hosts import HostRegistry
from daher_provisioning.models import DeployTarget
from daher_provisioning.queue import JobQueue, JobStatus

HOST_ID = os.environ.get("DEMO_HOST_ID", "host-a")
SHARED = Path(os.environ.get("DEMO_SHARED", "/shared"))


class RawJob:
    """A payload that is NOT a valid Job, wrapped so the queue can still route it.

    This is how the harness impersonates a compromised API: the agent must receive the
    hostile bytes over the wire and refuse them itself.
    """

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.job_id = str(payload.get("job_id", "unknown"))
        self.host_id = str(payload.get("host_id", HOST_ID))

    def model_dump(self, mode: str = "json") -> dict:
        return self._payload


class RawRecord:
    def __init__(self, payload: dict) -> None:
        self.job = RawJob(payload)
        # The real enum, so the real queue.claim() routes this without being patched.
        self.status = JobStatus.QUEUED
        self.claimed_at = None
        self.finished_at = None
        self.detail = ""


def build():
    enrolments = EnrolmentService()
    queue = JobQueue()
    hosts = HostRegistry()
    hosts.register(HOST_ID, DeployTarget(host="agent"))

    # Enrol exactly as the real flow does: the agent generates its own credential and the
    # API only ever receives the hash.
    token, _ = enrolments.mint(HOST_ID)
    agent_secret, credential_hash = generate_agent_credential(HOST_ID)
    enrolments.redeem(token, credential_hash=credential_hash)
    hosts.mark_enrolled(HOST_ID)

    portal_key, portal_record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)

    SHARED.mkdir(parents=True, exist_ok=True)
    (SHARED / "agent_credential").write_text(agent_secret, encoding="utf-8")
    (SHARED / "portal_key").write_text(portal_key, encoding="utf-8")

    app = create_app(
        key_store=KeyStore([portal_record]),
        audit_log=AuditLog(SHARED / "audit.jsonl"),
        hosts=hosts,
        job_queue=queue,
        enrolment=enrolments,
    )

    @app.post("/demo/enqueue-raw")
    async def enqueue_raw(payload: dict, queue_as: str | None = None) -> dict:
        """Impersonate a compromised API by queueing an arbitrary payload.

        ``queue_as`` deliberately decouples *which host's queue a job lands in* from *which
        host the job says it is for*. That is how a compromised or buggy API would misroute
        work, and it is the only way to make the agent's own "is this addressed to me?"
        check fire over a real socket — routing alone would otherwise filter it first, and
        the demonstration would silently never test that refusal.
        """
        job_id = str(payload.get("job_id", "unknown"))
        target = queue_as or str(payload.get("host_id", HOST_ID))
        queue._jobs[job_id] = RawRecord(payload)  # noqa: SLF001
        queue._pending[target].append(job_id)  # noqa: SLF001
        return {"queued": job_id, "delivered_to": target}

    @app.get("/demo/job/{job_id}")
    async def job_state(job_id: str) -> dict:
        record = queue._jobs.get(job_id)  # noqa: SLF001
        if record is None:
            return {"known": False}
        return {
            "known": True,
            "status": str(getattr(record.status, "value", record.status)),
            "detail": record.detail,
        }

    (SHARED / "ready").write_text("ok", encoding="utf-8")
    return app


if __name__ == "__main__":
    import subprocess

    tls = SHARED / "tls"
    # Generate the demonstration's CA and certificates, including the two deliberately
    # wrong ones the controls need. A TLS test that only presents the correct certificate
    # proves nothing — `verify=False` would pass it identically.
    subprocess.run(["/bin/sh", "/app/demo/make_certs.sh", str(tls)], check=True)  # noqa: S603
    (SHARED / "ready").write_text("ok", encoding="utf-8")

    uvicorn.run(
        build(),
        host="0.0.0.0",  # noqa: S104
        port=8443,
        log_level="warning",
        ssl_certfile=str(tls / "api.pem"),
        ssl_keyfile=str(tls / "api.key"),
    )
