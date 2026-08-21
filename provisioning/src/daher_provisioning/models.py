"""Facility records and API response schemas.

⚠️ The single most important property of this module: **no response schema may ever carry
anything derived from a patient record** (ADR-021). That is enforced structurally rather
than by convention:

1. Every response model forbids extra fields, so one cannot be bolted on downstream.
2. ``tests/test_no_clinical_data.py`` walks every model and fails the build if a field is
   named for anything clinical.
3. The service holds no clinical database credentials at all, so there is nothing to put in
   such a field even if someone added one.

The third is the real guarantee. The first two catch mistakes; the third makes the mistake
unreachable.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FacilityType(StrEnum):
    """Hospitals and labs are independently provisionable; neither implies the other."""

    HOSPITAL = "hospital"
    LAB = "lab"


class FacilityState(StrEnum):
    PROVISIONING = "provisioning"
    RUNNING = "running"
    DEGRADED = "degraded"
    # Requested and the STOP job is queued, but no agent has reported stopping the stack yet
    # (DH-027E). Distinct from DECOMMISSIONED on purpose: the earlier code jumped straight to
    # DECOMMISSIONED on request, which told an operator a stack was stopped while it was
    # still serving. "Queued" is not "done" — the same rule the create path already follows.
    DECOMMISSIONING = "decommissioning"
    # Soft only. There is deliberately no `deleted` state reachable from the API surface —
    # destroying clinical data requires a separate, harder mechanism and sign-off (ADR-021).
    DECOMMISSIONED = "decommissioned"


class BackupState(StrEnum):
    # No passphrase and/or off-site destination exist on the facility host, so the scheduler
    # was never enabled (ADR-025) — reported by the agent, never inferred by the control
    # plane, which never holds the passphrase and so cannot know this on its own (DH-027G).
    NOT_CONFIGURED = "not_configured"
    NEVER_RUN = "never_run"
    SUCCESS = "success"
    FAILED = "failed"
    # A backup whose restore has never been exercised is not a backup you can rely on. Kept
    # for wire compatibility; restore-drill verification is represented instead by the
    # separate `restore_drill_last_performed` field below (DH-027G keeps that separation —
    # see the ticket's "restore drill status remains a separate property"), so this value is
    # never assigned by anything in this codebase.
    UNVERIFIED_NO_RESTORE_DRILL = "unverified_no_restore_drill"


class ClosedModel(BaseModel):
    """Base for everything crossing the API boundary.

    ``extra="forbid"`` is the structural half of the no-clinical-data guarantee: a field
    that is not declared here cannot be introduced by a dict that happens to carry one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class DeployTarget(ClosedModel):
    """Where a facility's stack runs.

    Deliberately a *field on the record* rather than an assumption in the provisioning
    logic (ADR-020). The default topology is one VM per facility, but nothing here knows
    or cares whether this host is dedicated or shared — which is what keeps the shared-host
    model available later without a rewrite.
    """

    host: str = Field(min_length=1, max_length=253, description="Hostname or address to deploy to")
    ssh_port: int = Field(default=22, ge=1, le=65535)
    dedicated: bool = Field(
        default=True,
        description="True when this host runs only this facility. Informational: the "
        "provisioning logic must not branch on it.",
    )


class FacilityIdentity(ClosedModel):
    """Administrative identity. Nothing here originates from a patient record."""

    country: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    facility_code: str | None = Field(
        default=None, max_length=64, description="Internal or official facility code"
    )


class Facility(ClosedModel):
    """The provisioning record for one facility."""

    facility_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
    display_name: str = Field(min_length=1, max_length=128)
    type: FacilityType
    deploy_target: DeployTarget
    identity: FacilityIdentity = Field(default_factory=FacilityIdentity)
    state: FacilityState = FacilityState.PROVISIONING
    created_at: datetime


class ServiceState(StrEnum):
    """One container's state, normalised from `docker compose ps` (DH-027D).

    Normalised rather than passed through raw so the control plane stores a small vocabulary
    it understands, not whatever a Docker version happened to print. `UNKNOWN` exists because
    an unrecognised state must not be silently read as working.
    """

    HEALTHY = "healthy"
    # Running with no healthcheck declared. Not the same as healthy, and not treated as it.
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STARTING = "starting"
    # Exited 0. Correct and expected for a one-shot setup job; a problem for anything else.
    EXITED_OK = "exited_ok"
    EXITED_ERROR = "exited_error"
    # Declared by the compose file but no container exists for it at all.
    MISSING = "missing"
    UNKNOWN = "unknown"


class InfrastructureHealth(StrEnum):
    """What the last real check said about the deployment — NOT its lifecycle phase.

    Deliberately separate from `FacilityState` (DH-027D). "Provisioning succeeded yesterday"
    and "the stack is healthy right now" are different claims, and collapsing them is how a
    dashboard ends up showing a green badge for a facility that has been down for a day.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    # No usable check: never run, the agent could not run it, or the output was unparseable.
    # Never a synonym for healthy.
    UNKNOWN = "unknown"


class ServiceHealth(ClosedModel):
    """One service's observed state. Infrastructure only."""

    name: str = Field(min_length=1, max_length=128)
    state: ServiceState
    # Compose's own `Health` string is kept only when present; absence means no healthcheck.
    image: str | None = Field(default=None, max_length=256)
    exit_code: int | None = None


class InfrastructureReport(ClosedModel):
    """A single real check of one facility's deployment (DH-027D).

    ⚠️ **Infrastructure only, and the no-clinical-data test walks this model.** Service names,
    container states, counts and an image reference. Never a patient count, an encounter, a
    result, a specimen or anything else derived from a patient record — the "just one summary
    stat" request is exactly what ADR-021 forbids.

    Note the field naming: `checked_at` rather than anything containing "observation", because
    the clinical-vocabulary guard substring-matches "observation" and would flag it. That is
    the guard working, not an obstacle to route around.
    """

    health: InfrastructureHealth
    services_expected: int = Field(ge=0)
    services_running: int = Field(ge=0)
    services: list[ServiceHealth] = Field(default_factory=list)
    checked_at: datetime
    # Why the health is what it is, in operator language. Never raw Docker JSON.
    reason: str | None = Field(default=None, max_length=512)


class BackupReport(ClosedModel):
    """A single BACKUP_NOW outcome (DH-027G), reported by the agent that ran it.

    Infrastructure-only, exactly like `InfrastructureReport`: never an archive path, a
    bucket name, a passphrase, an access key, or anything patient-derived. The control
    plane learns *whether* backup is configured only through this report — it never holds
    the passphrase itself (ADR-025), so it cannot determine `NOT_CONFIGURED` on its own.
    """

    state: BackupState
    checked_at: datetime
    reason: str | None = Field(default=None, max_length=512)


class FacilityStatus(ClosedModel):
    """Operational status. Health, backup, versions — never anything clinical.

    Note what is *absent* and must stay absent: patient counts, encounter counts, order or
    result volumes, specimen counts. Those all originate from patient records. A "just one
    summary stat" request is the exact thing ADR-021 forbids.
    """

    facility_id: str
    # Lifecycle phase — what was requested and confirmed for this facility over time.
    state: FacilityState
    # What the LAST REAL CHECK said about the deployment. Deliberately separate from `state`
    # (DH-027D): "provisioning succeeded yesterday" and "the stack is healthy now" are
    # different claims, and one green badge for both is how a dashboard lies.
    health: InfrastructureHealth = InfrastructureHealth.UNKNOWN
    health_reason: str | None = Field(default=None, max_length=512)
    services: list[ServiceHealth] = Field(default_factory=list)
    services_running: int = Field(ge=0)
    services_expected: int = Field(ge=0)
    # What the LAST BACKUP_NOW REPORT said (DH-027G) — never inferred, since the control
    # plane never holds the passphrase that would let it know whether backup is configured
    # (ADR-025). Defaults to NEVER_RUN when no such report has ever arrived; that default is
    # a "no evidence yet" state, not a claim that backup is either working or absent.
    backup_state: BackupState
    backup_last_run: datetime | None = None
    restore_drill_last_performed: datetime | None = Field(
        default=None,
        description="A backup with no performed restore is not a verified backup.",
    )
    image_versions: dict[str, str] = Field(default_factory=dict)
    # None when no check has ever been reported. Never `now()` — a fresh timestamp for an
    # observation that never happened is the exact false green this ticket exists to remove.
    checked_at: datetime | None = None


class CredentialReference(ClosedModel):
    """A pointer to issued credentials — never the credentials themselves (ADR-021).

    Returning secret values inline would put them in every access log, proxy log, and
    browser history between here and the operator.
    """

    facility_id: str
    credential_set_id: str
    issued_at: datetime
    retrieval_hint: str = Field(
        description="How an operator retrieves these out of band. Never a secret value."
    )


class FacilityCreateRequest(ClosedModel):
    display_name: str = Field(min_length=1, max_length=128)
    type: FacilityType
    # ⚠️ A host ID, NOT an address. The caller names a host an operator already registered;
    # the API resolves it. Accepting an address here would let a breached portal point
    # provisioning at any host it chose (ADR-022).
    host_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
    identity: FacilityIdentity = Field(default_factory=FacilityIdentity)
    facility_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$",
        description="Optional explicit id. Permanent once volumes exist (DH-026).",
    )


class FacilityCreateResponse(ClosedModel):
    facility: Facility
    credentials: CredentialReference
    # DNS/TLS is unconfigured until a real domain and provider exist (ADR-020). This flag
    # exists so a caller is told the step did not run, rather than inferring success from a
    # 201 — a provisioning step that silently "succeeds" against nothing is a false green.
    dns_configured: bool = False
    incomplete_steps: list[str] = Field(default_factory=list)
