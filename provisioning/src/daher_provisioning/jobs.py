"""The job vocabulary shared between the API and the host agent (ADR-022).

A job is **parameters, never a spec**. There is no field here that can carry a compose
file, an image reference, a volume mount, a command, or anything else that would let the
issuer decide what runs. The verb selects one of a fixed set of operations the agent
already knows how to perform against a compose file that lives on the facility host.

That is the whole design. If a field is ever added here that lets the caller influence
*what* runs rather than *which* known operation runs, the blast-radius walkthrough in
ADR-022 stops being true — because Docker access is root access, and an issuer who can
shape a container spec is root on that host.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# A job older than this is refused. A breached API can enqueue anything it likes; it should
# not additionally be able to bank jobs and fire them later, or replay one it captured.
DEFAULT_MAX_AGE_SECONDS = 900


class JobVerb(StrEnum):
    """The complete set of operations an agent will perform. Nothing else is possible.

    Deliberately absent: anything resembling `run`, `exec`, `compose-file`, `image`, or
    `shell`. The agent has no code path for them, so no job can request one.
    """

    PROVISION = "provision"
    # Narrowly named on purpose. "rotate" would imply it rotates everything, and ADR-024
    # records exactly why it cannot: database credentials, the OpenMRS admin password and
    # the backup passphrase each need coordination the env file cannot provide.
    ROTATE_TLS = "rotate_tls"
    START = "start"
    STOP = "stop"
    STATUS = "status"
    BACKUP_NOW = "backup_now"


class JobParams(BaseModel):
    """Closed parameter set.

    ``extra="forbid"`` matters more here than anywhere else in the system: it is what makes
    "the issuer cannot smuggle in a field the agent might one day honour" a property of the
    type rather than a promise.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Reaches a Docker Compose project name, so it reaches a command boundary. Validated
    # here AND again by the agent — never trusted because the API "already checked".
    facility_id: str = Field(
        min_length=3, max_length=40, pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$"
    )
    facility_type: str = Field(default="hospital", pattern=r"^(hospital|lab)$")
    base_domain: str = Field(
        default="localhost", min_length=1, max_length=253, pattern=r"^[a-z0-9.-]+$"
    )


class Job(BaseModel):
    """One unit of work addressed to exactly one host."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    # The agent refuses any job not addressed to itself, so a job cannot be aimed at a host
    # by an issuer that got the routing wrong — or aimed deliberately at the wrong one.
    host_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
    verb: JobVerb
    params: JobParams
    issued_at: datetime
