"""The agent's real HTTP transport and entry point (ADR-022).

Outbound only. The agent opens connections to the provisioning API; nothing connects to it.
It listens on no port, which is the property the whole pull design exists to buy.

⚠️ Reading a job off a socket is the part unit tests could not exercise. `fetch` deliberately
reads the whole body and hands a fully-parsed object to validation, so a payload split
across TCP segments cannot be half-parsed — `httpx` reassembles before `.json()` returns.
That assumption is now checked by the two-container demonstration rather than assumed.

`poll_once` no longer blocks for the duration of a job (bug 18, `poller.py`), so this loop's
`time.sleep(poll_seconds)` cadence keeps claiming work even while a long `provision` runs in
the background. `poller.join()` is called before `transport.close()` below for exactly the
reason that fix creates: a job can still be executing, and reporting its outcome, after
`max_iterations` is reached, and closing the HTTP client under that report would lose it.

`backup_settings_from_env` reads whatever backup configuration the operator placed in this
process's own environment at host bootstrap (ADR-025, DH-027G) — never from a job, never
from the provisioning API, which never holds the passphrase.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx

from daher_agent.executor import (
    DEFAULT_BACKUP_TIMEOUT_SECONDS,
    DEFAULT_STACK_TIMEOUT_SECONDS,
    ComposeExecutor,
)
from daher_agent.poller import AgentPoller
from daher_agent.validation import JobValidator
from daher_provisioning.facility_env import BackupSettings

logger = logging.getLogger("daher_agent.runner")

DEFAULT_POLL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 15.0


class HttpTransport:
    def __init__(self, base_url: str, credential: str, *, verify: bool | str = True) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {credential}"}
        self._client = httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            verify=verify,
            # No redirects: a redirect is a way to be pointed somewhere else by whoever
            # answers, which is not something an outbound-only agent should honour.
            follow_redirects=False,
        )

    def fetch(self) -> dict | None:
        response = self._client.get(f"{self._base}/agent/jobs/claim", headers=self._headers)
        response.raise_for_status()
        body = response.json()
        job = body.get("job")
        return job if isinstance(job, dict) else None

    def report(
        self, job_id: str, status: str, detail: str, infrastructure=None, backup=None
    ) -> None:
        body: dict = {"status": status, "detail": detail}
        if infrastructure is not None:
            # Structured infrastructure evidence (DH-027D). Sent as its validated model dump,
            # never as raw Docker output — the control plane persists a schema it understands.
            body["infrastructure"] = infrastructure.model_dump(mode="json")
        if backup is not None:
            # Structured backup evidence (DH-027G). Never a passphrase, bucket name, or
            # credential — see `BackupReport`.
            body["backup"] = backup.model_dump(mode="json")
        self._client.post(
            f"{self._base}/agent/jobs/{job_id}/report",
            headers=self._headers,
            json=body,
        ).raise_for_status()

    def close(self) -> None:
        self._client.close()


def facility_exists(facility_root: Path):
    """A facility exists here if its env file does. Asks the filesystem, not the API."""

    def check(facility_id: str) -> bool:
        return (facility_root / facility_id / ".env").is_file()

    return check


def backup_settings_from_env(env: dict[str, str] | None = None) -> BackupSettings | None:
    """Read backup configuration the operator placed in this agent process's own environment.

    Never from a job, never from the provisioning API, never generated here (ADR-025): the
    operator supplies these at host bootstrap — the same manual, on-host step that already
    delivers `AGENT_CREDENTIAL` — so a passphrase that must survive losing this host is never
    created on it. Absent entirely (the common case until an operator adds them) this
    returns `None`, and provisioning proceeds with no backup wired, exactly as before.
    """
    values = env if env is not None else os.environ
    passphrase = values.get("BACKUP_ENCRYPTION_PASSPHRASE", "")
    s3_bucket = values.get("BACKUP_S3_BUCKET", "")
    if not passphrase and not s3_bucket:
        return None
    return BackupSettings(
        passphrase=passphrase,
        s3_bucket=s3_bucket,
        s3_endpoint=values.get("BACKUP_S3_ENDPOINT", ""),
        s3_access_key_id=values.get("BACKUP_S3_ACCESS_KEY_ID", ""),
        s3_secret_access_key=values.get("BACKUP_S3_SECRET_ACCESS_KEY", ""),
        retention_days=int(values.get("BACKUP_RETENTION_DAYS", "30")),
    )


def make_executor(
    facility_root: Path,
    *,
    compose_file: Path,
    base_domain: str,
    stack_timeout_seconds: int = DEFAULT_STACK_TIMEOUT_SECONDS,
    backup_timeout_seconds: int = DEFAULT_BACKUP_TIMEOUT_SECONDS,
    backup: BackupSettings | None = None,
):
    """Build the real executor: the compose file already on this host, run as an argv list.

    Never a shell string, and never a spec supplied by the caller (ADR-022).

    The two timeouts are settable because facility hardware differs: the ≈49-minute cold boot
    behind the defaults was measured on 4 vCPU, and a slower VM is a configuration problem,
    not a code change.

    ``backup`` is host-local, supplied by the operator at bootstrap (ADR-025) — never taken
    from a job, never generated here. See `backup_settings_from_env`.
    """
    return ComposeExecutor(
        compose_file=compose_file,
        facility_root=facility_root,
        base_domain=base_domain,
        stack_timeout_seconds=stack_timeout_seconds,
        backup_timeout_seconds=backup_timeout_seconds,
        backup=backup,
    ).execute


def run_forever(
    *,
    base_url: str,
    credential: str,
    host_id: str,
    facility_root: Path,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    execute=None,
    compose_file: Path | None = None,
    base_domain: str = "localhost",
    stack_timeout_seconds: int = DEFAULT_STACK_TIMEOUT_SECONDS,
    backup_timeout_seconds: int = DEFAULT_BACKUP_TIMEOUT_SECONDS,
    backup: BackupSettings | None = None,
    max_iterations: int | None = None,
) -> None:
    transport = HttpTransport(base_url, credential)
    validator = JobValidator(host_id=host_id, facility_exists=facility_exists(facility_root))
    poller = AgentPoller(
        validator=validator,
        fetch=transport.fetch,
        execute=execute
        or make_executor(
            facility_root,
            compose_file=compose_file or Path("/opt/daher/docker-compose.yml"),
            base_domain=base_domain,
            stack_timeout_seconds=stack_timeout_seconds,
            backup_timeout_seconds=backup_timeout_seconds,
            backup=backup,
        ),
        report=transport.report,
    )

    iterations = 0
    try:
        while max_iterations is None or iterations < max_iterations:
            outcome = poller.poll_once()
            if outcome is not None:
                logger.info("job_outcome=%s", outcome)
            iterations += 1
            time.sleep(poll_seconds)
    finally:
        poller.join()
        transport.close()


def main() -> int:  # pragma: no cover - exercised by the demonstration, not unit tests
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever(
        base_url=os.environ["PROVISIONING_URL"],
        credential=os.environ["AGENT_CREDENTIAL"],
        host_id=os.environ["HOST_ID"],
        facility_root=Path(os.environ.get("FACILITY_ROOT", "/var/lib/daher/facilities")),
        poll_seconds=float(os.environ.get("POLL_SECONDS", DEFAULT_POLL_SECONDS)),
        compose_file=Path(os.environ.get("COMPOSE_FILE", "/opt/daher/docker-compose.yml")),
        base_domain=os.environ.get("BASE_DOMAIN", "localhost"),
        stack_timeout_seconds=int(
            os.environ.get("STACK_TIMEOUT_SECONDS", DEFAULT_STACK_TIMEOUT_SECONDS)
        ),
        backup_timeout_seconds=int(
            os.environ.get("BACKUP_TIMEOUT_SECONDS", DEFAULT_BACKUP_TIMEOUT_SECONDS)
        ),
        backup=backup_settings_from_env(),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
