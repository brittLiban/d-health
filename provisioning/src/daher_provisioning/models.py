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
    # Soft only. There is deliberately no `deleted` state reachable from the API surface —
    # destroying clinical data requires a separate, harder mechanism and sign-off (ADR-021).
    DECOMMISSIONED = "decommissioned"


class BackupState(StrEnum):
    NEVER_RUN = "never_run"
    SUCCESS = "success"
    FAILED = "failed"
    # A backup whose restore has never been exercised is not a backup you can rely on.
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


class FacilityStatus(ClosedModel):
    """Operational status. Health, backup, versions — never anything clinical.

    Note what is *absent* and must stay absent: patient counts, encounter counts, order or
    result volumes, specimen counts. Those all originate from patient records. A "just one
    summary stat" request is the exact thing ADR-021 forbids.
    """

    facility_id: str
    state: FacilityState
    services_running: int = Field(ge=0)
    services_expected: int = Field(ge=0)
    backup_state: BackupState
    backup_last_run: datetime | None = None
    restore_drill_last_performed: datetime | None = Field(
        default=None,
        description="A backup with no performed restore is not a verified backup.",
    )
    image_versions: dict[str, str] = Field(default_factory=dict)
    checked_at: datetime


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
    deploy_target: DeployTarget
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
