"""The facility agent's local job validation — the real security boundary (ADR-022).

ADR-022's residual risk is stated plainly: *an attacker who owns the provisioning API can
enqueue a job whose parameters are hostile.* Containment rests entirely on this module.

So the rule here is absolute: **every field of a job is untrusted input, regardless of the
job having arrived authenticated.** "The API already validated it" is exactly the reasoning
that would make this file decorative. The API's checks protect against mistakes; these
checks protect against the API itself.

Five refusals, each one a thing a breached API would otherwise get:

1. A job not addressed to this host.
2. A verb outside the fixed set.
3. A parameter that fails local re-validation.
4. A job that is stale or replayed — so a captured job cannot be banked and fired later.
5. **Provisioning over a facility that already exists here.** The API has the same check,
   but a breached API can skip its own; this one it cannot reach.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from daher_provisioning.jobs import DEFAULT_MAX_AGE_SECONDS, Job, JobParams, JobVerb

# Anything that could change the meaning of a value once it reaches a command, a compose
# project name, or an env file line. The typed patterns in `jobs.py` already exclude these;
# this is a second, independent net, because the cost of being wrong here is root.
FORBIDDEN_CHARACTERS = set(";|&$`><\n\r\0'\"\\ \t")

FACILITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


class JobRefused(RuntimeError):
    """The agent will not run this job. The reason is safe to log."""


class ReplayRefused(JobRefused):
    """This job was already seen, or is too old to act on."""


class FacilityExistsLocally(JobRefused):
    """Refusing to provision over a facility that already exists on this host."""


def _reject_dangerous_characters(name: str, value: str) -> None:
    bad = FORBIDDEN_CHARACTERS.intersection(value)
    if bad:
        raise JobRefused(
            f"parameter {name!r} contains characters that must never reach a command or "
            f"env file: {sorted(bad)!r}"
        )


def revalidate_params(params: JobParams) -> None:
    """Re-check parameters locally. Never trust that the issuer validated them."""
    if not FACILITY_ID_PATTERN.match(params.facility_id):
        raise JobRefused(
            f"facility_id {params.facility_id!r} fails local validation. It becomes a "
            f"Docker Compose project name, so it is validated at both ends rather than "
            f"trusted because the issuer checked."
        )
    for name, value in (
        ("facility_id", params.facility_id),
        ("facility_type", params.facility_type),
        ("base_domain", params.base_domain),
    ):
        _reject_dangerous_characters(name, value)


class JobValidator:
    """Decides whether a pulled job may run on this host.

    ``facility_exists`` is injected so the agent can ask the real filesystem whether a
    facility is already deployed here, and tests can ask something deterministic.
    """

    def __init__(
        self,
        *,
        host_id: str,
        facility_exists: Callable[[str], bool],
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.host_id = host_id
        self._facility_exists = facility_exists
        self._max_age = timedelta(seconds=max_age_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._seen: set[str] = set()

    def parse(self, payload: dict) -> Job:
        """Parse an untrusted payload into a Job, or refuse it.

        A malformed payload is a refusal, not an exception that escapes as a 500 — the
        agent must stay running and keep refusing rather than crash-loop into a state where
        it stops polling.
        """
        try:
            return Job.model_validate(payload)
        except ValidationError as exc:
            raise JobRefused(
                f"job payload failed schema validation: {exc.error_count()} error(s)"
            ) from exc

    def validate(self, job: Job) -> Job:
        """Run every refusal. Returns the job only if all of them pass."""
        if job.host_id != self.host_id:
            raise JobRefused(
                f"job {job.job_id!r} is addressed to host {job.host_id!r}, not to this host "
                f"({self.host_id!r}). Refusing rather than assuming it was misrouted."
            )

        if job.verb not in set(JobVerb):  # pragma: no cover - enum makes this unreachable
            raise JobRefused(f"unknown verb {job.verb!r}")

        revalidate_params(job.params)

        now = self._clock()
        age = now - job.issued_at
        if age > self._max_age:
            raise ReplayRefused(
                f"job {job.job_id!r} is {age.total_seconds():.0f}s old, older than the "
                f"{self._max_age.total_seconds():.0f}s limit. A captured job must not be "
                f"bankable for later."
            )
        if age < -timedelta(seconds=60):
            raise ReplayRefused(
                f"job {job.job_id!r} is dated in the future; refusing rather than trusting "
                f"the issuer's clock"
            )

        if job.job_id in self._seen:
            raise ReplayRefused(f"job {job.job_id!r} was already processed on this host")

        if job.verb is JobVerb.PROVISION and self._facility_exists(job.params.facility_id):
            raise FacilityExistsLocally(
                f"facility {job.params.facility_id!r} already exists on this host. Refusing "
                f"to provision over it: that would overwrite a live env file and point a "
                f"'new' facility at an existing patient database. The issuer has the same "
                f"check, but a compromised issuer can skip its own — this one it cannot."
            )

        return job

    def accept(self, job: Job) -> None:
        """Mark a job as processed, so a replay of it is refused."""
        self._seen.add(job.job_id)

    def validate_and_accept(self, payload: dict) -> Job:
        job = self.validate(self.parse(payload))
        self.accept(job)
        return job
