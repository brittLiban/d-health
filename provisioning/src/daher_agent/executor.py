"""Run the facility's compose stack (ADR-022).

This is the last seam where a parameter reaches a command boundary, so the rules are narrow
and absolute:

1. **Never a shell.** Every invocation is an argv *list* with ``shell=False``. There is no
   string for a metacharacter to live in, which removes command injection as a category
   rather than filtering for it.
2. **The compose file is fixed and local.** Its path comes from this host's configuration,
   never from the job. An issuer who could name a compose file could name any container
   spec, and Docker access is root access — that single rule is what keeps ADR-022's
   walkthrough true.
3. **The facility id is validated again here**, immediately before it becomes an argument.
   It has already been checked by the API and by the agent's job validator; this is the
   third, at the point where being wrong actually costs something.
4. **Secrets are generated on this host.** The job carries none, and the API has never held
   any. The executor writes the env file itself.
5. **Everything is bounded.** A hung compose must not hang the agent forever, and a failure
   is a returned result rather than an exception that stops the poll loop.
"""

from __future__ import annotations

import logging
import subprocess  # noqa: S404 - argv lists only, never a shell; see module docstring
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from daher_agent.poller import ExecutionResult
from daher_agent.validation import revalidate_params
from daher_provisioning.facility_env import (
    ROTATABLE,
    FacilityEnvError,
    render_env,
    rotate_env_values,
    write_env,
)
from daher_provisioning.jobs import Job, JobVerb
from daher_provisioning.models import DeployTarget, Facility, FacilityType
from daher_provisioning.secrets import generate_facility_secrets, validate_facility_id

logger = logging.getLogger("daher_agent.executor")

DEFAULT_TIMEOUT_SECONDS = 900
# Absolute, so a PATH entry planted earlier in the search order cannot substitute a
# different binary for the one this host means to run.
DEFAULT_DOCKER_BINARY = "/usr/bin/docker"


class ExecutorError(RuntimeError):
    """The executor refuses to run this job."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(argv: list[str], *, cwd: Path, timeout: int) -> CommandResult:
    """Run an argv list with no shell. The only place this module touches subprocess."""
    completed = subprocess.run(  # noqa: S603 - argv list, shell=False, fixed binary
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout[-4000:], completed.stderr[-4000:])


class ComposeExecutor:
    def __init__(
        self,
        *,
        compose_file: Path,
        facility_root: Path,
        base_domain: str,
        docker_binary: str = DEFAULT_DOCKER_BINARY,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        runner: Callable[..., CommandResult] = run_command,
    ) -> None:
        self.compose_file = Path(compose_file)
        self.facility_root = Path(facility_root)
        self.base_domain = base_domain
        self.docker_binary = docker_binary
        self.timeout_seconds = timeout_seconds
        self._run = runner

    def env_path(self, facility_id: str) -> Path:
        return self.facility_root / facility_id / ".env"

    def _argv(self, facility_id: str, *verb_args: str) -> list[str]:
        """Build the argv list.

        Note what cannot appear: no compose file from the job, no image, no command, no
        volume. `--project-name` and `--env-file` take values derived from an id that has
        been validated three times, and everything else is a constant.
        """
        return [
            self.docker_binary,
            "compose",
            "--file",
            str(self.compose_file),
            "--project-name",
            f"daher-{facility_id}",
            "--env-file",
            str(self.env_path(facility_id)),
            *verb_args,
        ]

    def _provision(self, facility_id: str, facility_type: str) -> list[str]:
        """Generate this facility's credentials locally and write its env file."""
        secrets = generate_facility_secrets(facility_id)
        facility = Facility(
            facility_id=facility_id,
            display_name=facility_id,
            type=FacilityType(facility_type),
            deploy_target=DeployTarget(host="localhost"),
            created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        contents = render_env(facility, secrets, base_domain=self.base_domain)
        # Refuses to overwrite. The agent already refuses to provision over an existing
        # facility; this is the same guarantee one layer lower, where the file actually is.
        write_env(self.env_path(facility_id), contents)
        return self._argv(facility_id, "up", "--detach")

    def _rotate_tls(self, facility_id: str) -> list[str]:
        """Rotate only what can be rotated safely (ADR-024).

        Fresh values for the TLS stores and Keycloak client secrets; everything else in the
        file is left exactly as it is, because rewriting a database password here would
        leave the stack unable to reach its own databases.
        """
        path = self.env_path(facility_id)
        if not path.is_file():
            raise FacilityEnvError(f"no env file for {facility_id!r}; nothing to rotate")

        fresh = generate_facility_secrets(facility_id).values
        replacements = {name: fresh[name] for name in ROTATABLE if name in fresh}
        updated = rotate_env_values(path.read_text(encoding="utf-8"), replacements)
        # The deliberate overwrite path: this is rotation, which is exactly the case
        # `write_env` reserves it for.
        write_env(path, updated, overwrite=True)
        # `up` re-runs the certs job, which finds a keystore it can no longer open and
        # regenerates it, and re-runs Keycloak reconciliation with the new client secrets.
        return self._argv(facility_id, "up", "--detach", "--force-recreate")

    def execute(self, job: Job) -> ExecutionResult:
        """Run one validated job. Returns a result; never raises into the poll loop."""
        try:
            # Third validation, immediately before the value becomes an argument.
            validate_facility_id(job.params.facility_id)
            revalidate_params(job.params)
        except Exception as exc:  # noqa: BLE001 - any validation failure is a refusal
            logger.error("executor_refused job_id=%s", job.job_id)
            return ExecutionResult(False, f"refused before execution: {exc}"[:400])

        facility_id = job.params.facility_id
        if not self.compose_file.is_file():
            return ExecutionResult(False, f"compose file {self.compose_file} not present on host")

        try:
            if job.verb is JobVerb.PROVISION:
                argv = self._provision(facility_id, job.params.facility_type)
            elif job.verb is JobVerb.ROTATE_TLS:
                argv = self._rotate_tls(facility_id)
            elif job.verb is JobVerb.START:
                argv = self._argv(facility_id, "up", "--detach")
            elif job.verb is JobVerb.STOP:
                # `stop`, never `down`: `down` removes containers and networks, and a
                # careless flag away from removing volumes. Decommission is soft (ADR-021).
                argv = self._argv(facility_id, "stop")
            elif job.verb is JobVerb.STATUS:
                argv = self._argv(facility_id, "ps", "--format", "json")
            elif job.verb is JobVerb.BACKUP_NOW:
                argv = self._argv(facility_id, "--profile", "backup", "run", "--rm", "backup")
            else:  # pragma: no cover - the enum makes this unreachable
                return ExecutionResult(False, f"unsupported verb {job.verb!r}")
        except FacilityEnvError as exc:
            return ExecutionResult(False, f"env refused: {exc}"[:400])
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(False, f"preparation failed: {type(exc).__name__}"[:400])

        try:
            result = self._run(argv, cwd=self.compose_file.parent, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            # A hung compose must not hang the agent. Report and move on.
            return ExecutionResult(False, f"timed out after {self.timeout_seconds}s")
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(False, f"execution error: {type(exc).__name__}")

        if result.returncode != 0:
            return ExecutionResult(False, f"exit {result.returncode}: {result.stderr[-300:]}")
        return ExecutionResult(True, result.stdout[-300:])
