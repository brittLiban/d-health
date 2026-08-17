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
5. **Everything is bounded, but not all work is the same size.** A hung compose must not hang
   the agent forever, and a failure is a returned result rather than an exception that stops
   the poll loop. One bound for every verb cannot hold: `ps` finishes in a second, while
   bringing this stack up waits on a health graph whose first gate alone allows 1800s.
   See `TIMEOUTS` below — a single number was known bug 17.
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

# Fast verbs: `ps` and `stop` return in seconds. Keeping these tight is what makes a hung
# command visible quickly instead of occupying the agent for an hour.
DEFAULT_TIMEOUT_SECONDS = 900

# Verbs that wait on the clinical stack's health graph (known bug 17).
#
# `up --detach` does not return once containers are created: this compose file gates
# openmrs-frontend, caddy, the adapter and every setup job behind
# `condition: service_healthy` / `service_completed_successfully` on openmrs-backend, whose
# healthcheck alone declares `start_period: 1800s`. A measured cold boot is ≈49 minutes,
# almost all of it OCL concept imports, and a fresh host pulls images before any of that.
#
# 900s was therefore shorter than a single healthcheck's grace period in the very file this
# executor exists to run: the first genuine provisioning would have been reported `failed`
# while the stack was coming up correctly.
#
# ⚠️ Be precise about what 5400s is known to clear. The 2940s cold boot is the *backend*
# reaching its first HTTP 200. `up` then still waits, serialized behind it, for the setup
# jobs (demo preflight, maternity config, prenatal panel, OpenELIS hardening and password
# rotation, Keycloak reconciliation), openelis-webapp's own 180s gate, Keycloak's 120s, and
# — on a fresh host — the image pulls that happen before any of it. That leaves ≈2460s of
# headroom for the remainder, which is **unmeasured**: nothing has ever booted this stack
# through a provisioning job. The bound is a defensible estimate, not an observed figure.
# `tests/test_executor.py` asserts what can be asserted here — that it clears every declared
# grace period and the measured boot — so a future gate with a longer grace period fails the
# suite rather than the first deployment. It cannot assert the composition.
DEFAULT_STACK_TIMEOUT_SECONDS = 5400

# `run --rm backup` is neither: it scales with how much data the facility holds, not with
# boot time. ⚠️ This bound is **unmeasured** — no backup has ever been timed against a real
# clinical database, and DH-014's off-site upload leg is still unexercised. It is set above
# the fast bound only so a data-sized job does not re-create bug 17 by inheriting a bound
# chosen for `ps`. Replace it with a measured value when DH-014 produces one.
DEFAULT_BACKUP_TIMEOUT_SECONDS = 3600

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
        stack_timeout_seconds: int = DEFAULT_STACK_TIMEOUT_SECONDS,
        backup_timeout_seconds: int = DEFAULT_BACKUP_TIMEOUT_SECONDS,
        runner: Callable[..., CommandResult] = run_command,
    ) -> None:
        self.compose_file = Path(compose_file)
        self.facility_root = Path(facility_root)
        self.base_domain = base_domain
        self.docker_binary = docker_binary
        self.timeout_seconds = timeout_seconds
        self.stack_timeout_seconds = stack_timeout_seconds
        self.backup_timeout_seconds = backup_timeout_seconds
        self._run = runner

    def timeout_for(self, verb: JobVerb) -> int:
        """Resolve this verb's bound. Every verb is listed explicitly, on purpose.

        A `dict.get(verb, self.timeout_seconds)` here would hand any verb added later the
        bound chosen for `ps` — which is exactly how bug 17 happened the first time. An
        unlisted verb raises instead, and `execute` turns that into a clean refusal.
        """
        return {
            # Wait on the compose health graph, so they inherit the stack's boot time.
            JobVerb.PROVISION: self.stack_timeout_seconds,
            JobVerb.START: self.stack_timeout_seconds,
            # `--force-recreate` restarts every container, so the same gates apply again.
            JobVerb.ROTATE_TLS: self.stack_timeout_seconds,
            # Scales with stored data rather than with boot time. Unmeasured; see above.
            JobVerb.BACKUP_NOW: self.backup_timeout_seconds,
            # Return in seconds; keeping them tight makes a hung command visible fast.
            JobVerb.STATUS: self.timeout_seconds,
            JobVerb.STOP: self.timeout_seconds,
        }[verb]

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
            # Inside this block deliberately: an unlisted verb becomes a refusal here rather
            # than an unbounded or wrongly-bounded command below.
            timeout = self.timeout_for(job.verb)
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
            result = self._run(argv, cwd=self.compose_file.parent, timeout=timeout)
        except subprocess.TimeoutExpired:
            # A hung compose must not hang the agent. Report and move on. The verb is named
            # because "timed out after 5400s" and "after 900s" are different diagnoses.
            return ExecutionResult(False, f"{job.verb.value} timed out after {timeout}s")
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(False, f"execution error: {type(exc).__name__}")

        if result.returncode != 0:
            return ExecutionResult(False, f"exit {result.returncode}: {result.stderr[-300:]}")
        return ExecutionResult(True, result.stdout[-300:])
