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
from daher_provisioning.hosts import HostError, HostRegistry
from daher_provisioning.models import (
    CredentialReference,
    Facility,
    FacilityCreateRequest,
    FacilityCreateResponse,
    FacilityState,
    FacilityStatus,
)
from daher_provisioning.ratelimit import RateLimitExceeded, TokenBucketLimiter
from daher_provisioning.repository import FacilityExists, FacilityNotFound, FacilityRepository
from daher_provisioning.secrets import SecretGenerationError, validate_facility_id

# What a caller is told did not happen. Keeping these as explicit strings rather than
# leaving the list empty is the difference between "provisioned" and "recorded".
STEP_DEPLOY = "deploy: no job was enqueued; the host agent is not built (ADR-022)"
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
) -> FastAPI:
    facilities = repository or FacilityRepository()
    host_registry = hosts or HostRegistry()
    rate_limiter = limiter or TokenBucketLimiter(rate_per_second=1.0, burst=20)

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
        audit_log.record(
            actor=principal.actor,
            action=AuditAction.FACILITY_CREATE,
            outcome=AuditOutcome.SUCCESS,
            facility_id=facility_id,
            credential_set_id=credential_set_id,
            message="record created; deploy and DNS not performed",
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
            incomplete_steps=[STEP_DEPLOY, STEP_DNS, STEP_BACKUP],
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

    return app
