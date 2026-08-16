"""Provisioning API surface (ADR-020, ADR-021).

⚠️ **This does not deploy anything yet.** `POST /facilities` registers a facility record,
generates its credentials, and reports what did *not* happen in `incomplete_steps`. The
Compose deploy driver and DNS provisioning do not exist. A 201 here means "recorded", not
"running", and the response says so rather than leaving a caller to infer it.

Everything else is real: the portal authenticates as an untrusted client with a scoped
credential, calls are rate limited, and **every action — including denials and failures —
is recorded by this service from what it actually did**, never from what the client claims.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from daher_provisioning.audit import AuditAction, AuditLog, AuditOutcome
from daher_provisioning.auth import (
    AuthenticationError,
    AuthorizationError,
    KeyStore,
    Principal,
    Scope,
    require,
)
from daher_provisioning.enrolment import EnrolmentError, EnrolmentService
from daher_provisioning.hosts import HostError, HostRegistry
from daher_provisioning.jobs import Job, JobParams, JobVerb
from daher_provisioning.models import (
    CredentialReference,
    Facility,
    FacilityCreateRequest,
    FacilityCreateResponse,
    FacilityState,
    FacilityStatus,
)
from daher_provisioning.network import SourceAllowlist, SourceNotAllowed
from daher_provisioning.queue import JobQueue, JobStatus
from daher_provisioning.ratelimit import RateLimitExceeded, TokenBucketLimiter
from daher_provisioning.repository import FacilityExists, FacilityNotFound, FacilityRepository
from daher_provisioning.secrets import SecretGenerationError, validate_facility_id

# What a caller is told did not happen. Keeping these as explicit strings rather than
# leaving the list empty is the difference between "provisioned" and "recorded".
STEP_DEPLOY = "deploy: no job was enqueued for this facility"
STEP_DNS = "dns: no domain or DNS provider is configured; no record was created (ADR-020)"
STEP_BACKUP = "backup: no scheduled backup was wired up for this facility"


def _slugify(display_name: str) -> str:
    allowed = [c.lower() if c.isalnum() else "-" for c in display_name]
    slug = "".join(allowed).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:40].strip("-")


def create_app(
    *,
    key_store: KeyStore,
    audit_log: AuditLog,
    repository: FacilityRepository | None = None,
    limiter: TokenBucketLimiter | None = None,
    hosts: HostRegistry | None = None,
    job_queue: JobQueue | None = None,
    enrolment: EnrolmentService | None = None,
    allowed_source_cidrs: list[str] | None = None,
    base_domain: str = "localhost",
) -> FastAPI:
    facilities = repository or FacilityRepository()
    host_registry = hosts or HostRegistry()
    queue = job_queue or JobQueue()
    enrolments = enrolment or EnrolmentService()
    rate_limiter = limiter or TokenBucketLimiter(rate_per_second=1.0, burst=20)
    allowlist = SourceAllowlist(allowed_source_cidrs)

    app = FastAPI(
        title="Daher Health provisioning",
        # No interactive docs: this service is reachable from a less-trusted network and a
        # schema browser is free reconnaissance.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def _client_ip(request: Request) -> str | None:
        return request.client.host if request.client else None

    async def authenticated(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Principal:
        """Authenticate, then rate limit. Both failures are audited."""
        # Source check first. An unauthorised source should not reach credential
        # comparison at all — otherwise it can still probe timing and rate limits from
        # anywhere on the internet (ADR-023).
        try:
            allowlist.check(_client_ip(request))
        except SourceNotAllowed as exc:
            audit_log.record(
                actor="blocked-source",
                action=AuditAction.AUTH_DENIED,
                outcome=AuditOutcome.DENIED,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "source not allowed") from exc

        presented = None
        if authorization and authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()

        try:
            principal = key_store.authenticate(presented)
        except AuthenticationError as exc:
            audit_log.record(
                actor="unauthenticated",
                action=AuditAction.AUTH_DENIED,
                outcome=AuditOutcome.DENIED,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credential") from exc

        try:
            rate_limiter.check(principal.key_id)
        except RateLimitExceeded as exc:
            audit_log.record(
                actor=principal.actor,
                action=AuditAction.RATE_LIMITED,
                outcome=AuditOutcome.DENIED,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate limit exceeded",
                headers={"Retry-After": str(int(exc.retry_after_seconds) + 1)},
            ) from exc

        return principal

    def _scoped(scope: Scope):
        async def dependency(
            request: Request, principal: Principal = Depends(authenticated)
        ) -> Principal:
            try:
                require(principal, scope)
            except AuthorizationError as exc:
                audit_log.record(
                    actor=principal.actor,
                    action=AuditAction.AUTH_DENIED,
                    outcome=AuditOutcome.DENIED,
                    message=str(exc)[:512],
                    source_ip=_client_ip(request),
                )
                raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient scope") from exc
            return principal

        return dependency

    @app.get("/health")
    async def health() -> JSONResponse:
        """Liveness only. Unauthenticated on purpose, and it reveals nothing."""
        return JSONResponse({"status": "up"})

    @app.post(
        "/facilities", response_model=FacilityCreateResponse, status_code=status.HTTP_201_CREATED
    )
    async def create_facility(
        body: FacilityCreateRequest,
        request: Request,
        principal: Principal = Depends(_scoped(Scope.FACILITY_CREATE)),
    ) -> FacilityCreateResponse:
        facility_id = body.facility_id or _slugify(body.display_name)

        # Resolve the named host against what an operator registered. An unknown host, an
        # un-enrolled one, or one already running a facility are all refusals — never an
        # overwrite (ADR-022).
        try:
            host = host_registry.get(body.host_id)
        except HostError as exc:
            audit_log.record(
                actor=principal.actor,
                action=AuditAction.FACILITY_CREATE,
                outcome=AuditOutcome.FAILURE,
                facility_id=facility_id,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

        # ⚠️ The API does NOT generate this facility's credentials (ADR-022). The agent on
        # the facility host generates them locally when it pulls its provisioning job, so a
        # breached API host does not hold — and has never held — any facility's database
        # passwords. Validate the id here; the secrets are the host's business.
        try:
            validate_facility_id(facility_id)
        except SecretGenerationError as exc:
            audit_log.record(
                actor=principal.actor,
                action=AuditAction.FACILITY_CREATE,
                outcome=AuditOutcome.FAILURE,
                facility_id=facility_id,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

        facility = Facility(
            facility_id=facility_id,
            display_name=body.display_name,
            type=body.type,
            deploy_target=host.target,
            identity=body.identity,
            state=FacilityState.PROVISIONING,
            created_at=datetime.now(UTC),
        )
        try:
            host_registry.claim(body.host_id, facility_id)
        except HostError as exc:
            audit_log.record(
                actor=principal.actor,
                action=AuditAction.FACILITY_CREATE,
                outcome=AuditOutcome.FAILURE,
                facility_id=facility_id,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

        try:
            facilities.add(facility)
        except FacilityExists as exc:
            audit_log.record(
                actor=principal.actor,
                action=AuditAction.FACILITY_CREATE,
                outcome=AuditOutcome.FAILURE,
                facility_id=facility_id,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

        credential_set_id = f"cs-{facility_id}-{int(facility.created_at.timestamp())}"

        # Enqueue the provisioning job for the host's agent. The job carries PARAMETERS
        # ONLY — no spec, no secrets. The agent generates its own credentials and runs the
        # compose file already on that host (ADR-022).
        deploy_queued = False
        try:
            queue.enqueue(
                Job(
                    job_id=f"job-{facility_id}-{int(facility.created_at.timestamp())}",
                    host_id=body.host_id,
                    verb=JobVerb.PROVISION,
                    params=JobParams(
                        facility_id=facility_id,
                        facility_type=body.type.value,
                        base_domain=base_domain,
                    ),
                    issued_at=facility.created_at,
                )
            )
            deploy_queued = True
        except Exception as exc:  # noqa: BLE001 - the record stands even if queueing fails
            audit_log.record(
                actor=principal.actor,
                action=AuditAction.FACILITY_CREATE,
                outcome=AuditOutcome.FAILURE,
                facility_id=facility_id,
                message=f"facility recorded but job not queued: {exc}"[:512],
                source_ip=_client_ip(request),
            )
        audit_log.record(
            actor=principal.actor,
            action=AuditAction.FACILITY_CREATE,
            outcome=AuditOutcome.SUCCESS,
            facility_id=facility_id,
            credential_set_id=credential_set_id,
            message="record created; provisioning job queued; DNS not performed",
            source_ip=_client_ip(request),
        )

        return FacilityCreateResponse(
            facility=facility,
            credentials=CredentialReference(
                facility_id=facility_id,
                credential_set_id=credential_set_id,
                issued_at=facility.created_at,
                retrieval_hint="Generated on the facility host by its agent, not here "
                "(ADR-022). This API never holds a facility credential.",
            ),
            dns_configured=False,
            incomplete_steps=([] if deploy_queued else [STEP_DEPLOY]) + [STEP_DNS, STEP_BACKUP],
        )

    @app.get("/facilities", response_model=list[Facility])
    async def list_facilities(
        request: Request,
        principal: Principal = Depends(_scoped(Scope.FACILITY_READ)),
    ) -> list[Facility]:
        audit_log.record(
            actor=principal.actor,
            action=AuditAction.FACILITY_LIST,
            outcome=AuditOutcome.SUCCESS,
            source_ip=_client_ip(request),
        )
        return facilities.list()

    @app.get("/facilities/{facility_id}/status", response_model=FacilityStatus)
    async def facility_status(
        facility_id: str,
        request: Request,
        principal: Principal = Depends(_scoped(Scope.FACILITY_READ)),
    ) -> FacilityStatus:
        try:
            facility = facilities.get(facility_id)
        except FacilityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

        audit_log.record(
            actor=principal.actor,
            action=AuditAction.FACILITY_STATUS,
            outcome=AuditOutcome.SUCCESS,
            facility_id=facility_id,
            source_ip=_client_ip(request),
        )
        # Counts are zero because nothing is deployed. When the driver lands these come
        # from the deployment surface — never from a clinical database.
        from daher_provisioning.models import BackupState

        return FacilityStatus(
            facility_id=facility_id,
            state=facility.state,
            services_running=0,
            services_expected=0,
            backup_state=BackupState.NEVER_RUN,
            checked_at=datetime.now(UTC),
        )

    @app.post("/facilities/{facility_id}/decommission", response_model=Facility)
    async def decommission(
        facility_id: str,
        request: Request,
        principal: Principal = Depends(_scoped(Scope.FACILITY_DECOMMISSION)),
    ) -> Facility:
        """Soft only. Stops and isolates a stack; never destroys data (ADR-021)."""
        try:
            updated = facilities.set_state(facility_id, FacilityState.DECOMMISSIONED)
        except FacilityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

        audit_log.record(
            actor=principal.actor,
            action=AuditAction.FACILITY_DECOMMISSION,
            outcome=AuditOutcome.SUCCESS,
            facility_id=facility_id,
            message="soft decommission recorded; no data destroyed",
            source_ip=_client_ip(request),
        )
        return updated

    async def agent_principal(
        request: Request, authorization: str | None = Header(default=None)
    ) -> str:
        """Authenticate a facility agent. Returns its host id.

        Deliberately a separate credential space from the portal's: an agent credential
        must not be able to drive the fleet, and a portal credential must not be able to
        claim a host's work.
        """
        try:
            allowlist.check(_client_ip(request))
        except SourceNotAllowed as exc:
            audit_log.record(
                actor="blocked-source",
                action=AuditAction.AUTH_DENIED,
                outcome=AuditOutcome.DENIED,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "source not allowed") from exc

        presented = None
        if authorization and authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()

        parts = (presented or "").split("_", 2)
        if len(parts) != 3 or parts[0] != "dha" or not parts[1] or not parts[2]:
            audit_log.record(
                actor="unauthenticated-agent",
                action=AuditAction.AUTH_DENIED,
                outcome=AuditOutcome.DENIED,
                message="malformed agent credential",
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid agent credential")

        host_id, secret = parts[1], parts[2]
        try:
            agent = enrolments.agent(host_id)
        except EnrolmentError as exc:
            audit_log.record(
                actor="unauthenticated-agent",
                action=AuditAction.AUTH_DENIED,
                outcome=AuditOutcome.DENIED,
                message=str(exc)[:512],
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid agent credential") from exc

        if not compare_digest(sha256(secret.encode("utf-8")).hexdigest(), agent.credential_hash):
            audit_log.record(
                actor=f"agent:{host_id}",
                action=AuditAction.AUTH_DENIED,
                outcome=AuditOutcome.DENIED,
                message="agent credential mismatch",
                source_ip=_client_ip(request),
            )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid agent credential")
        return host_id

    @app.get("/agent/jobs/claim")
    async def claim_job(host_id: str = Depends(agent_principal)) -> JSONResponse:
        """Hand this host its next job, if any. A host can only ever claim its own."""
        job = queue.claim(host_id)
        if job is None:
            return JSONResponse({"job": None})
        # Record who took what. ADR-021 requires every action be logged by the API from
        # what it observed; a claim is an action.
        audit_log.record(
            actor=f"agent:{host_id}",
            action=AuditAction.FACILITY_STATUS,
            outcome=AuditOutcome.SUCCESS,
            facility_id=getattr(getattr(job, "params", None), "facility_id", None),
            message=f"job {getattr(job, 'job_id', 'unknown')} claimed",
            source_ip=None,
        )
        return JSONResponse({"job": job.model_dump(mode="json")})

    @app.post("/agent/jobs/{job_id}/report")
    async def report_job(
        job_id: str,
        request: Request,
        host_id: str = Depends(agent_principal),
    ) -> JSONResponse:
        """Record an agent's claim about an outcome.

        ⚠️ This is the agent's *claim*. The API's own audit record is authoritative
        (ADR-021) — an agent that says "succeeded" has said so, not proven it.
        """
        body = await request.json()
        reported = str(body.get("status", ""))[:32]
        detail = str(body.get("detail", ""))[:512]
        mapping = {
            "succeeded": JobStatus.SUCCEEDED,
            "failed": JobStatus.FAILED,
            "refused": JobStatus.REFUSED,
        }
        if reported not in mapping:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown status")

        record = queue.get(job_id)
        # Authorise on who the job was DELIVERED to, not on what the payload claims. A
        # compromised issuer controls `job.host_id`, so using it here would make an agent's
        # refusal of a misrouted job unreportable — silencing exactly the event most worth
        # recording (found by the two-container demonstration, ADR-022).
        if record.delivered_to is not None and record.delivered_to != host_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "job was delivered to another host")

        # ⚠️ Read anything the audit needs BEFORE changing state, and read it defensively.
        # The demonstration caught this: the state change succeeded and then the audit call
        # raised while reaching into a field, leaving the job finished with no record of it.
        # An audit that can fail after the thing it records has already happened is worse
        # than no audit, because the gap is invisible.
        facility_id = getattr(getattr(record.job, "params", None), "facility_id", None)
        queue.finish(job_id, mapping[reported], detail)
        audit_log.record(
            actor=f"agent:{host_id}",
            action=AuditAction.FACILITY_STATUS,
            outcome=AuditOutcome.SUCCESS if reported == "succeeded" else AuditOutcome.FAILURE,
            facility_id=facility_id,
            message=f"agent reported {reported}: {detail}"[:512],
            source_ip=_client_ip(request),
        )
        return JSONResponse({"recorded": reported})

    app.state.queue = queue
    app.state.enrolments = enrolments
    app.state.hosts = host_registry
    return app
