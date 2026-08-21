"""Turn `docker compose` output into an infrastructure health report (DH-027D).

Pure functions on purpose: every rule below is decided here, from text, so the rules can be
tested exhaustively without a Docker daemon. The agent supplies the text; this module never
runs a command.

**The rule this module exists to enforce:** a zero exit code from `docker compose ps` means
the *command* worked, not that the *stack* is healthy. Compose happily returns 0 while
printing a service that is `exited` or `unhealthy`. So health is derived from the parsed
service states and never from the command's exit status.

**Infrastructure only.** Service names, container states, counts, an image reference. Nothing
here reads or reports anything derived from a patient record (ADR-021).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from daher_provisioning.models import (
    InfrastructureHealth,
    InfrastructureReport,
    ServiceHealth,
    ServiceState,
)


class ComposeOutputError(ValueError):
    """The compose output could not be parsed into a usable report.

    Always becomes `UNKNOWN` health, never `HEALTHY`. Output we cannot read is not evidence
    that anything is working.
    """


def one_shot_services(config_json: str) -> set[str]:
    """Services other services wait to *complete*, i.e. the setup jobs.

    Derived from the compose file's own `depends_on: condition: service_completed_successfully`
    rather than from a hand-maintained list, because a hand-maintained list is wrong the first
    time someone adds a setup job. On the clinical stack this yields the seven setup jobs
    (demo preflight, maternity config, prenatal panel, OpenELIS certs, DB hardening, admin
    password, Keycloak reconciliation).

    ⚠️ A restart policy does **not** distinguish these — every service in the clinical compose
    file declares one, including the one-shots. That was checked rather than assumed.
    """
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as error:
        raise ComposeOutputError(f"compose config is not JSON: {error}") from error
    if not isinstance(config, Mapping):
        raise ComposeOutputError("compose config is not an object")

    services = config.get("services")
    if not isinstance(services, Mapping):
        raise ComposeOutputError("compose config has no services map")

    one_shots: set[str] = set()
    for spec in services.values():
        if not isinstance(spec, Mapping):
            continue
        depends = spec.get("depends_on")
        if not isinstance(depends, Mapping):
            continue
        for dependency, condition in depends.items():
            if (
                isinstance(condition, Mapping)
                and condition.get("condition") == "service_completed_successfully"
            ):
                one_shots.add(str(dependency))
    return one_shots


def expected_services(config_json: str) -> set[str]:
    """Every service the compose file declares *for the currently active profiles*.

    Profiles matter and are handled by Compose itself: `config` omits a profile-gated service
    unless that profile is active, so a facility without off-site backup is not marked
    degraded for lacking a backup container it was never meant to run.
    """
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as error:
        raise ComposeOutputError(f"compose config is not JSON: {error}") from error
    services = config.get("services") if isinstance(config, Mapping) else None
    if not isinstance(services, Mapping) or not services:
        raise ComposeOutputError("compose config declares no services")
    return {str(name) for name in services}


def _normalise(entry: Mapping[str, object]) -> ServiceHealth | None:
    name = entry.get("Service")
    if not isinstance(name, str) or not name:
        return None

    raw_state = str(entry.get("State", "")).lower()
    raw_health = str(entry.get("Health", "") or "").lower()
    exit_code = entry.get("ExitCode")
    exit_code = exit_code if isinstance(exit_code, int) else None

    if raw_state == "running":
        # Compose reports Health only when the service declares a healthcheck. "running with
        # no healthcheck" is deliberately NOT reported as healthy — we have no evidence.
        state = {
            "healthy": ServiceState.HEALTHY,
            "unhealthy": ServiceState.UNHEALTHY,
            "starting": ServiceState.STARTING,
        }.get(raw_health, ServiceState.RUNNING)
    elif raw_state in ("exited", "dead"):
        state = ServiceState.EXITED_OK if exit_code == 0 else ServiceState.EXITED_ERROR
    elif raw_state in ("created", "restarting", "paused", "removing"):
        state = ServiceState.STARTING if raw_state == "restarting" else ServiceState.UNKNOWN
    else:
        # An unrecognised state is UNKNOWN, never assumed working.
        state = ServiceState.UNKNOWN

    image = entry.get("Image")
    return ServiceHealth(
        name=name,
        state=state,
        image=image[:256] if isinstance(image, str) else None,
        exit_code=exit_code,
    )


def parse_ps(ps_output: str) -> list[ServiceHealth]:
    """Parse `docker compose ps --all --format json`.

    Compose emits **JSON lines** — one object per line — not a JSON array. Verified against
    the real command rather than assumed. An array is accepted too, because Compose has
    emitted both across versions and being strict about it would break on upgrade.
    """
    text = (ps_output or "").strip()
    if not text:
        return []

    entries: list[Mapping[str, object]] = []
    if text.startswith("["):
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as error:
            raise ComposeOutputError(f"compose ps is not JSON: {error}") from error
        if not isinstance(loaded, list):
            raise ComposeOutputError("compose ps array is not a list")
        entries = [e for e in loaded if isinstance(e, Mapping)]
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                loaded = json.loads(line)
            except json.JSONDecodeError as error:
                raise ComposeOutputError(f"compose ps line is not JSON: {error}") from error
            if isinstance(loaded, Mapping):
                entries.append(loaded)

    parsed = [normalised for e in entries if (normalised := _normalise(e)) is not None]
    if entries and not parsed:
        raise ComposeOutputError("compose ps returned entries but none carried a Service name")
    return parsed


def derive_health(
    services: Iterable[ServiceHealth],
    *,
    expected: set[str],
    one_shots: set[str],
    checked_at: datetime | None = None,
) -> InfrastructureReport:
    """Decide overall health from parsed service states.

    The rules, and why each exists:

    * A **long-running** service that is `UNHEALTHY`, `EXITED_*`, `MISSING` or `UNKNOWN`
      degrades the deployment. `EXITED_OK` counts here too — a long-running service that
      exited cleanly is still down.
    * A **one-shot** setup job that is `EXITED_OK` is **correct** and must not degrade
      anything; that is its successful terminal state. A one-shot that `EXITED_ERROR` does
      degrade, because a failed setup job means the stack came up wrong.
    * `STARTING` does not degrade — a stack mid-boot is not a broken stack — but it is not
      healthy either, so it yields `UNKNOWN` rather than `HEALTHY`.

    `services_running` counts services actually up (healthy, running, or starting), which is
    what an operator means by "11 of 11". One-shots are excluded from both counts so the
    ratio is not permanently 11/18 on a perfectly healthy stack.
    """
    observed = {service.name: service for service in services}
    long_running = sorted(expected - one_shots)

    reported: list[ServiceHealth] = []
    problems: list[str] = []
    starting: list[str] = []
    running = 0

    for name in long_running:
        service = observed.get(name)
        if service is None:
            service = ServiceHealth(name=name, state=ServiceState.MISSING)
            problems.append(f"{name} missing")
        elif service.state in (ServiceState.HEALTHY, ServiceState.RUNNING):
            running += 1
        elif service.state is ServiceState.STARTING:
            running += 1
            starting.append(name)
        else:
            problems.append(f"{name} {service.state.value}")
        reported.append(service)

    for name in sorted(one_shots):
        service = observed.get(name)
        if service is None:
            # A setup job that never ran is not necessarily wrong — compose removes exited
            # containers on some paths — so it is reported without degrading the deployment.
            service = ServiceHealth(name=name, state=ServiceState.MISSING)
        elif service.state is ServiceState.EXITED_ERROR:
            problems.append(f"{name} setup job failed")
        reported.append(service)

    if problems:
        health = InfrastructureHealth.DEGRADED
        reason = "; ".join(problems[:8])
    elif starting:
        health = InfrastructureHealth.UNKNOWN
        reason = f"still starting: {', '.join(starting[:8])}"
    else:
        health = InfrastructureHealth.HEALTHY
        reason = None

    return InfrastructureReport(
        health=health,
        services_expected=len(long_running),
        services_running=running,
        services=reported,
        checked_at=checked_at or datetime.now(UTC),
        reason=reason,
    )


def unknown_report(reason: str, *, checked_at: datetime | None = None) -> InfrastructureReport:
    """A check that could not be made. Explicitly UNKNOWN, never HEALTHY."""
    return InfrastructureReport(
        health=InfrastructureHealth.UNKNOWN,
        services_expected=0,
        services_running=0,
        services=[],
        checked_at=checked_at or datetime.now(UTC),
        reason=reason[:512],
    )
