"""The provisioning portal (ADR-020, ADR-023).

A self-contained, server-rendered FastAPI app. **The browser never talks to the provisioning
API** — it talks to this, and this calls the API server-side with the scoped credential. A
provisioning credential in browser JavaScript is one XSS away from being stolen, and an
admin console is exactly the sort of page that gets less scrutiny than a public app.

It shows health, backup status and versions. It shows **nothing from a patient record**, and
cannot: the API it calls has no clinical database credentials and its response schemas are
closed (ADR-021).
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from daher_portal.security import (
    USER_SESSION_KEY,
    AuthError,
    Operator,
    check_csrf,
    issue_csrf,
    operator_from_env,
    verify_password,
)
from daher_provisioning.ratelimit import RateLimitExceeded, TokenBucketLimiter

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

BACKUP_CLASSES = {
    "success": "ok",
    "failed": "bad",
    "never_run": "warn",
    "unverified_no_restore_drill": "warn",
}


class ProvisioningClient:
    """Server-side client for the provisioning API. Holds the credential; the browser never does."""

    def __init__(self, base_url: str, credential: str, *, verify: bool | str = True) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {credential}"}
        self._client = httpx.Client(timeout=15, verify=verify, follow_redirects=False)

    def facilities(self) -> list[dict]:
        response = self._client.get(f"{self._base}/facilities", headers=self._headers)
        response.raise_for_status()
        return response.json()

    def status(self, facility_id: str) -> dict:
        response = self._client.get(
            f"{self._base}/facilities/{facility_id}/status", headers=self._headers
        )
        response.raise_for_status()
        return response.json()

    def create(self, payload: dict) -> httpx.Response:
        return self._client.post(f"{self._base}/facilities", headers=self._headers, json=payload)


def create_portal(
    *,
    client: ProvisioningClient,
    operator: Operator | None = None,
    session_secret: str | None = None,
    hosts: list[str] | None = None,
    login_limiter: TokenBucketLimiter | None = None,
) -> FastAPI:
    account = operator or operator_from_env()
    secret = session_secret or os.environ.get("PORTAL_SESSION_SECRET") or os.urandom(32).hex()
    limiter = login_limiter or TokenBucketLimiter(rate_per_second=0.2, burst=8)
    available_hosts = hosts or []

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="daher_portal",
        https_only=True,
        same_site="strict",  # a cross-site POST must not carry the session
        max_age=8 * 3600,
    )

    def signed_in(request: Request) -> str | None:
        return request.session.get(USER_SESSION_KEY)

    def render(request: Request, template: str, **context) -> HTMLResponse:
        response = TEMPLATES.TemplateResponse(
            request, template, {"operator": signed_in(request), **context}
        )
        # Health and backup status must never be served from a cache. A stale "backup
        # succeeded" presented as current is worse than showing nothing.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        # No inline scripts anywhere in the templates, so this can be strict.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.get("/login")
    async def login_form(request: Request) -> HTMLResponse:
        if signed_in(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html", csrf_token=issue_csrf(request.session), error=None)

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(""),
    ) -> Response:
        try:
            check_csrf(request.session, csrf_token)
            # Rate limit by source, so a stolen username cannot be brute forced quietly.
            limiter.check(request.client.host if request.client else "unknown")
            verify_password(account, username, password)
        except (AuthError, RateLimitExceeded) as exc:
            return render(
                request,
                "login.html",
                csrf_token=issue_csrf(request.session),
                error=str(exc) if isinstance(exc, RateLimitExceeded) else "Invalid credentials.",
            )

        # New session id on privilege change, so a fixated pre-login cookie is useless.
        request.session.clear()
        request.session[USER_SESSION_KEY] = username
        issue_csrf(request.session)
        return RedirectResponse("/", status_code=303)

    @app.get("/logout")
    async def logout(request: Request) -> Response:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/")
    async def index(request: Request) -> Response:
        if not signed_in(request):
            return RedirectResponse("/login", status_code=303)

        error = None
        rows: list[dict] = []
        try:
            for facility in client.facilities():
                status = client.status(facility["facility_id"])
                rows.append(
                    {
                        "facility_id": facility["facility_id"],
                        "display_name": facility["display_name"],
                        "type": facility["type"],
                        "state": status["state"],
                        "services_running": status["services_running"],
                        "services_expected": status["services_expected"],
                        "backup_state": status["backup_state"].replace("_", " "),
                        "backup_class": BACKUP_CLASSES.get(status["backup_state"], "warn"),
                        "restore_drill": status.get("restore_drill_last_performed") or "never",
                        "drill_class": "ok"
                        if status.get("restore_drill_last_performed")
                        else "bad",
                    }
                )
        except Exception:
            # Show the failure rather than an empty table. An empty list and an unreachable
            # API look identical, and one of them means "your fleet is fine".
            error = "Could not reach the provisioning API. This list may be incomplete."

        return render(
            request,
            "facilities.html",
            facilities=rows,
            hosts=available_hosts,
            csrf_token=issue_csrf(request.session),
            error=error,
        )

    @app.post("/facilities")
    async def create_facility(
        request: Request,
        display_name: str = Form(...),
        type: str = Form(...),  # noqa: A002 - matches the API field name
        host_id: str = Form(...),
        csrf_token: str = Form(""),
    ) -> Response:
        if not signed_in(request):
            return RedirectResponse("/login", status_code=303)
        try:
            check_csrf(request.session, csrf_token)
        except AuthError:
            return RedirectResponse("/", status_code=303)

        response = client.create({"display_name": display_name, "type": type, "host_id": host_id})
        if response.status_code >= 400:
            detail = response.json().get("detail", "The provisioning API refused this request.")
            return render(
                request,
                "facilities.html",
                facilities=[],
                hosts=available_hosts,
                csrf_token=issue_csrf(request.session),
                error=str(detail)[:400],
            )
        return RedirectResponse("/", status_code=303)

    return app
