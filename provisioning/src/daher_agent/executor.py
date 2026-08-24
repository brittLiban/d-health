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
from datetime import UTC, datetime
from pathlib import Path

from daher_agent.backup_observation import BackupStatusError, parse_last_run_status
from daher_agent.infrastructure import (
    ComposeOutputError,
    derive_health,
    expected_services,
    one_shot_services,
    parse_ps,
    unknown_report,
)
from daher_agent.poller import ExecutionResult
from daher_agent.validation import revalidate_params
from daher_provisioning.facility_env import (
    ROTATABLE,
    BackupSettings,
    FacilityEnvError,
    read_env_value,
    render_env,
    rotate_env_values,
    set_break_glass_flag,
    set_sso_enabled,
    write_env,
)
from daher_provisioning.jobs import Job, JobVerb
from daher_provisioning.models import (
    AuthenticationMode,
    BackupReport,
    BackupState,
    DeployTarget,
    Facility,
    FacilityType,
)
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

# Fixed and local, exactly like `compose_file` above: never derived from a job, never
# supplied by a caller. `backup/backup.sh` writes exactly this path inside the container
# (`${BACKUP_OUTPUT_DIR:-/backup}/last-run.status`); `backup/README.md` documents reading it
# with this same command for a manual check (DH-027H).
BACKUP_STATUS_PATH = "/backup/last-run.status"


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
    # 64 KiB of stdout: `docker compose ps --all --format json` for a full clinical
    # stack is a few KiB, and truncating it would corrupt the JSON the parser needs.
    return CommandResult(completed.returncode, completed.stdout[-65536:], completed.stderr[-4000:])


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
        backup: BackupSettings | None = None,
        runner: Callable[..., CommandResult] = run_command,
    ) -> None:
        self.compose_file = Path(compose_file)
        self.facility_root = Path(facility_root)
        self.base_domain = base_domain
        self.docker_binary = docker_binary
        self.timeout_seconds = timeout_seconds
        self.stack_timeout_seconds = stack_timeout_seconds
        self.backup_timeout_seconds = backup_timeout_seconds
        # Operator-supplied at host bootstrap, never generated here (ADR-025, DH-027G). May
        # be None, or present but not `schedulable` (one half only) — either way, "backup is
        # actually protected" is `self.backup is not None and self.backup.schedulable`.
        self.backup = backup
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
            # `--no-deps` skips the compose health-graph wait entirely — measured directly
            # in bug-21 and bug-22, both landing in seconds. The fast bound is correct
            # specifically because of `--no-deps`, not despite it; if this ever grows a
            # dependency wait, this bound must move to `stack_timeout_seconds` with it.
            JobVerb.ENABLE_BREAK_GLASS: self.timeout_seconds,
            JobVerb.DISABLE_BREAK_GLASS: self.timeout_seconds,
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
            created_at=datetime.now(UTC),
        )
        # `self.backup` is this host's operator-supplied configuration (ADR-025, DH-027G) —
        # never from the job, which carries none. `render_env` wires it only when it can
        # actually succeed (a passphrase AND an off-site destination); otherwise the backup
        # profile is simply absent from the rendered file, exactly as if `backup=None`.
        contents = render_env(facility, secrets, base_domain=self.base_domain, backup=self.backup)
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

    def _set_break_glass(self, facility_id: str, *, enabled: bool) -> list[str]:
        """Flip `SSO_ENABLED` and recreate only `openmrs-backend` (UI-010/DH-008E).

        `enabled=True` here means break-glass is being turned ON, i.e. `SSO_ENABLED=false`
        — the inverse naming is deliberate: this method's parameter names the mode being
        requested, not the env value, so callers read `_set_break_glass(id, enabled=True)`
        as "turn break-glass on" rather than having to remember which boolean means what.

        Recreates the same one service bug-21 and bug-22 already proved this pattern
        against: `--no-deps` skips the compose health-graph wait entirely (both prior
        incidents measured this landing in seconds, not the 5400s stack bound), and
        touches no other container. `openmrs-backend`'s own `configure-sso.sh` and
        `exclude-authentication-when-sso.sh` — proven in UI-007/008/009 — do the actual
        module-lifecycle work from the new `SSO_ENABLED` value; this method's only job is
        the env write and the narrow recreate that lets that startup path run again.

        Does **not** touch Keycloak, OpenELIS, Caddy, the adapter, or any setup job — Caddy's
        `depends_on: daher-ui: condition: service_healthy`-style coupling does not apply here
        because `--no-deps` means compose does not even consult `openmrs-backend`'s own
        dependents, let alone recreate them (confirmed empirically in bug-21/bug-22: every
        other service's uptime was unchanged by a `--no-deps` recreate of one service).
        """
        path = self.env_path(facility_id)
        if not path.is_file():
            raise FacilityEnvError(f"no env file for {facility_id!r}; nothing to transition")

        contents = path.read_text(encoding="utf-8")
        contents = set_sso_enabled(contents, enabled=not enabled)
        contents = set_break_glass_flag(contents, active=enabled)
        write_env(path, contents, overwrite=True)
        return self._argv(facility_id, "up", "--detach", "--no-deps", "openmrs-backend")

    def _enable_break_glass(self, facility_id: str) -> list[str]:
        return self._set_break_glass(facility_id, enabled=True)

    def _disable_break_glass(self, facility_id: str) -> list[str]:
        return self._set_break_glass(facility_id, enabled=False)

    def _observe_authentication_mode(self, facility_id: str) -> AuthenticationMode:
        """Read the *configured* mode from the env file (UI-010/DH-008E).

        Deliberately not a live probe of the running container — see `AuthenticationMode`'s
        docstring for exactly what that means and does not mean. A missing or unreadable env
        file, or one that does not declare `SSO_ENABLED` at all, is UNKNOWN, never NORMAL:
        defaulting an unreadable check to "normal" would hide exactly the state an operator
        most needs to see.
        """
        path = self.env_path(facility_id)
        try:
            contents = path.read_text(encoding="utf-8")
        except OSError:
            return AuthenticationMode.UNKNOWN

        sso_enabled = read_env_value(contents, "SSO_ENABLED")
        break_glass = read_env_value(contents, "DAHER_BREAK_GLASS")
        if sso_enabled is None or break_glass is None:
            return AuthenticationMode.UNKNOWN
        # SSO on always wins if the two flags ever disagree (e.g. a hand-edited env file) —
        # oauth2login being the active module is the fact that actually matters, and that is
        # driven by SSO_ENABLED alone (configure-sso.sh), never by DAHER_BREAK_GLASS.
        if sso_enabled == "true":
            return AuthenticationMode.NORMAL
        if sso_enabled == "false" and break_glass == "true":
            return AuthenticationMode.BREAK_GLASS
        if sso_enabled == "false" and break_glass == "false":
            return AuthenticationMode.LOCAL_ONLY
        return AuthenticationMode.UNKNOWN

    def _status(self, facility_id: str) -> ExecutionResult:
        """Observe the deployment: infrastructure health, and now scheduled backup evidence
        alongside it (DH-027H). Both are read-only, both run whether or not a mutating job is
        also in flight (`poller.py`'s status slot), and combining them here — rather than a
        new verb — reuses the fleet's one existing "what does this host actually look like
        right now" seam instead of adding a second one beside it.
        """
        result = self._observe_infrastructure(facility_id)
        backup = self._observe_backup(facility_id)
        return ExecutionResult(result.succeeded, result.detail, result.infrastructure, backup)

    def _observe_infrastructure(self, facility_id: str) -> ExecutionResult:
        """Two read-only compose commands, both fixed and local.

        ⚠️ **A zero exit code is not health.** `docker compose ps` returns 0 while printing a
        service that is `exited` or `unhealthy`, so the verdict comes from the parsed states.
        This method therefore returns `succeeded=True` whenever the *observation* was made —
        including when that observation is DEGRADED. "The check ran" and "the stack is fine"
        are different facts and the report carries the second one.

        `config` is needed as well as `ps`, because `ps` can only show containers that exist;
        a service declared by the compose file and entirely absent is invisible without it.
        `config` also respects active profiles, so an unconfigured backup service is not
        counted as missing.
        """
        timeout = self.timeout_for(JobVerb.STATUS)
        try:
            config = self._run(
                self._argv(facility_id, "config", "--format", "json"),
                cwd=self.compose_file.parent,
                timeout=timeout,
            )
            listing = self._run(
                self._argv(facility_id, "ps", "--all", "--format", "json"),
                cwd=self.compose_file.parent,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                True, "status timed out", unknown_report("compose did not respond in time")
            )
        except Exception as error:  # noqa: BLE001
            return ExecutionResult(
                True,
                "status failed",
                unknown_report(f"compose unavailable: {type(error).__name__}"),
            )

        if config.returncode != 0:
            return ExecutionResult(
                True, "status failed", unknown_report("compose config failed; expected set unknown")
            )
        if listing.returncode != 0:
            return ExecutionResult(True, "status failed", unknown_report("compose ps failed"))

        try:
            report = derive_health(
                parse_ps(listing.stdout),
                expected=expected_services(config.stdout),
                one_shots=one_shot_services(config.stdout),
            )
        except ComposeOutputError as error:
            # Unreadable output is UNKNOWN, never healthy.
            return ExecutionResult(True, "status unparseable", unknown_report(str(error)))

        report = report.model_copy(
            update={"authentication_mode": self._observe_authentication_mode(facility_id)}
        )
        return ExecutionResult(
            True,
            f"{report.health.value}: {report.services_running}/{report.services_expected} services",
            report,
        )

    def _observe_backup(self, facility_id: str) -> BackupReport:
        """Read the nightly scheduler's own evidence (DH-027H) — the same read-only pattern
        `_observe_infrastructure` uses for `docker compose ps`/`config`, applied to the one
        artifact `backup/scheduler.sh` actually produces: `last-run.status`, documented and
        already used for exactly this manual check in `backup/README.md`.

        Not configured is checked locally first, exactly like `_backup_now` — no point
        exec'ing into a container that was never started because the profile was never
        active. Every other failure path (exec fails, times out, the file is absent,
        malformed, or unparseable) is fail-closed to `NEVER_RUN`, never `SUCCESS` — an
        operator reading "no evidence of a successful run" is the honest answer to all of
        those, and `BackupState` deliberately gains no new value to distinguish them further
        (see the module docstring in `backup_observation.py`).
        """
        now = datetime.now(UTC)
        if self.backup is None or not self.backup.schedulable:
            return BackupReport(
                state=BackupState.NOT_CONFIGURED,
                checked_at=now,
                reason="no passphrase and/or off-site destination configured on this host "
                "(ADR-025) — the scheduler was never enabled",
            )

        timeout = self.timeout_for(JobVerb.STATUS)
        try:
            result = self._run(
                self._argv(
                    facility_id,
                    "--profile",
                    "backup",
                    "exec",
                    "-T",
                    "backup",
                    "cat",
                    BACKUP_STATUS_PATH,
                ),
                cwd=self.compose_file.parent,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return BackupReport(
                state=BackupState.NEVER_RUN, checked_at=now, reason="backup status check timed out"
            )
        except Exception as error:  # noqa: BLE001
            return BackupReport(
                state=BackupState.NEVER_RUN,
                checked_at=now,
                reason=f"backup status unavailable: {type(error).__name__}",
            )

        if result.returncode != 0:
            # Covers both "genuinely never run" (the file does not exist yet) and "the
            # container could not be reached" — the same NEVER_RUN answer either way,
            # because both mean the same thing to an operator: no confirmed evidence.
            return BackupReport(
                state=BackupState.NEVER_RUN,
                checked_at=now,
                reason="no scheduled backup evidence found on this host",
            )

        try:
            return parse_last_run_status(result.stdout, observed_at=now)
        except BackupStatusError as error:
            return BackupReport(state=BackupState.NEVER_RUN, checked_at=now, reason=str(error))

    def _backup_now(self, facility_id: str) -> ExecutionResult:
        """Run one backup cycle on demand, or report why none was attempted (DH-027G).

        ⚠️ **Not configured is not a failure of the agent.** The agent correctly determined
        that this host has no passphrase and/or off-site destination, and — per ADR-025 —
        it must never *guess* or fall back to a local-only run to make this job "succeed".
        `succeeded=True` here means "the check was made and is trustworthy", exactly the
        convention `_status` already uses; it says nothing about whether the facility's data
        is actually protected. That verdict is `report.state`, not `ExecutionResult.succeeded`.

        A real attempt reuses the exact command `backup/README.md` documents for a manual
        run: `docker compose --profile backup run --rm backup`. `backup.sh` itself refuses to
        run without every required credential (`: "${VAR:?}"` guards) and to report an
        off-site upload as anything but `FAILED` when `BACKUP_REQUIRE_OFFSITE=true` (the
        pilot default) and no bucket is reachable — this method does not re-implement any of
        that; it only observes the exit code, the same boundary `_status` keeps around
        `docker compose`.
        """
        now = datetime.now(UTC)
        if self.backup is None or not self.backup.schedulable:
            return ExecutionResult(
                True,
                "backup not configured on this host",
                backup=BackupReport(
                    state=BackupState.NOT_CONFIGURED,
                    checked_at=now,
                    reason="no passphrase and/or off-site destination configured on this "
                    "host (ADR-025) — the scheduler was never enabled",
                ),
            )

        timeout = self.timeout_for(JobVerb.BACKUP_NOW)
        argv = self._argv(facility_id, "--profile", "backup", "run", "--rm", "backup")
        try:
            result = self._run(argv, cwd=self.compose_file.parent, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                False,
                f"backup_now timed out after {timeout}s",
                backup=BackupReport(
                    state=BackupState.FAILED, checked_at=now, reason=f"timed out after {timeout}s"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(
                False,
                f"execution error: {type(exc).__name__}",
                backup=BackupReport(
                    state=BackupState.FAILED,
                    checked_at=now,
                    reason=f"execution error: {type(exc).__name__}",
                ),
            )

        if result.returncode != 0:
            return ExecutionResult(
                False,
                f"exit {result.returncode}: {result.stderr[-300:]}",
                backup=BackupReport(
                    state=BackupState.FAILED,
                    checked_at=now,
                    reason=f"backup command exited {result.returncode}",
                ),
            )
        return ExecutionResult(
            True,
            result.stdout[-300:],
            backup=BackupReport(state=BackupState.SUCCESS, checked_at=now),
        )

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
            elif job.verb is JobVerb.ENABLE_BREAK_GLASS:
                argv = self._enable_break_glass(facility_id)
            elif job.verb is JobVerb.DISABLE_BREAK_GLASS:
                argv = self._disable_break_glass(facility_id)
            elif job.verb is JobVerb.START:
                argv = self._argv(facility_id, "up", "--detach")
            elif job.verb is JobVerb.STOP:
                # `stop`, never `down`: `down` removes containers and networks, and a
                # careless flag away from removing volumes. Decommission is soft (ADR-021).
                argv = self._argv(facility_id, "stop")
            elif job.verb is JobVerb.STATUS:
                # Handled separately: STATUS is the one verb whose answer is a parsed
                # structure rather than a command outcome (DH-027D).
                return self._status(facility_id)
            elif job.verb is JobVerb.BACKUP_NOW:
                # Handled separately, like STATUS: BACKUP_NOW's answer is a parsed evidence
                # report (DH-027G), and "not configured" must never fall through to a
                # generic `docker compose` invocation that would only fail loudly later.
                return self._backup_now(facility_id)
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
