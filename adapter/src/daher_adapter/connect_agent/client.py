"""DH-CONNECT-AGENT-001 §45–§48 — the agent's side of the runtime API.

A thin HTTP client over the four endpoints, and nothing else. No retry policy (the runner owns
cadence), no queue (the store owns durability), no clinical logic (the adapter owns that).

**The HTTP client is injectable**, and that is what makes the agent testable without a network.
Passing ``starlette.testclient.TestClient(app)`` -- which *is* an ``httpx.Client``, with a
synchronous ASGI transport underneath -- runs the real agent client against the real server in one
process: no ports, no fixtures that mock what the client does, and a failing test means one of the
two sides is genuinely wrong rather than that a stub drifted.

(``httpx.ASGITransport`` is deliberately **not** what is injected. It implements only
``handle_async_request``, so a synchronous ``httpx.Client`` cannot drive it at all -- which this
agent, single-threaded by design, is. ``TestClient`` is the supported synchronous route into an ASGI
app, and is what the rest of this repository's API tests already use.)

**Credentials are read, never stored.** The client secret comes from the environment and lives only
in this object's memory for the life of the process (§9, §72). It is never written to the agent
store, never logged, and never included in an error message -- ``_redact`` exists because the
easiest way to leak a secret is an exception string that helpfully includes the request body.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# ``httpx2`` is the distribution this environment pins (httpx 2.x ships under that name); the
# fallback keeps the module importable anywhere the 1.x name is what is installed. Exactly the shape
# ``starlette.testclient`` uses, so the agent and the test client resolve the same library.
try:  # pragma: no cover - one branch runs per environment
    import httpx2 as httpx
except ModuleNotFoundError:  # pragma: no cover
    import httpx

logger = logging.getLogger("daher_adapter.connect_agent.client")

#: How long any single call may take. Short enough that a hung server does not hold a poll cycle
#: open indefinitely, long enough that a slow claim of a full batch completes. The agent's own
#: durability means a timed-out call is always safe to retry.
DEFAULT_TIMEOUT_SECONDS = 20.0


class RuntimeApiError(RuntimeError):
    """A call the server refused, with its status and its (non-sensitive) detail.

    ``retryable`` is decided here rather than by the caller because the distinction is about HTTP,
    not about clinical meaning: 5xx and a transport failure are worth trying again, 4xx is the
    server saying something that will still be true in fifteen seconds.
    """

    def __init__(self, status_code: int, detail: str, *, retryable: bool) -> None:
        super().__init__(f"runtime API returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable


class RuntimeUnreachable(RuntimeError):
    """The server could not be reached at all. Always retryable, never a clinical outcome."""


@dataclass(frozen=True, slots=True)
class HeartbeatAck:
    received_at: str
    health_state: str
    heartbeat_interval_seconds: int
    pending_outbound: int
    contract_version: str
    runtime_protocol_version: str


class TokenSource:
    """Where the agent's bearer token comes from. A seam, not an OAuth implementation.

    §9 warns against building a Keycloak administration subsystem for this ticket, and this is the
    line that honours it: the agent obtains a token the way its deployment provides one, and DHealth
    neither issues nor validates that exchange here. The default implementation reads a token
    supplied by configuration; a deployment doing client-credentials exchange supplies a subclass.

    Deliberately **not** cached across calls in the base class. A cache that does not understand
    expiry is worse than no cache -- it fails at the least convenient moment, roughly an hour in.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    def bearer(self) -> str:
        return self._token


class ConnectRuntimeClient:
    """The four calls, and the redaction discipline around them."""

    def __init__(
        self,
        *,
        base_url: str,
        tokens: TokenSource,
        http_client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """``http_client`` replaces the one this would otherwise build. See the module docstring.

        ``base_url`` is required even when a client is injected: the value is what ``status``
        reports, and an agent that could not say which server it is configured for would be an agent
        nobody can check.
        """
        self._tokens = tokens
        self._base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(base_url=self._base_url, timeout=timeout)

    def close(self) -> None:
        """Closes only a client this object created. An injected one belongs to whoever made it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ConnectRuntimeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- calls --------------------------------------------------------------------------------

    def heartbeat(
        self,
        *,
        runtime_instance_id: str,
        agent_version: str,
        contract_version: str,
        queue_depth: int,
        dead_letters: int,
        adapter_reachable: bool,
    ) -> HeartbeatAck:
        """Report facts about this agent. §13: counts and versions, never a patient payload."""
        body = self._post(
            "/heartbeat",
            {
                "runtime_instance_id": runtime_instance_id,
                "agent_version": agent_version,
                "contract_version": contract_version,
                "reported_at": datetime.now(UTC).isoformat(),
                "local_queue_depth": queue_depth,
                "local_dead_letters": dead_letters,
                "adapter_reachable": adapter_reachable,
            },
        )
        return HeartbeatAck(
            received_at=body["received_at"],
            health_state=body["health_state"],
            heartbeat_interval_seconds=int(body["heartbeat_interval_seconds"]),
            pending_outbound=int(body["pending_outbound"]),
            contract_version=body["contract_version"],
            runtime_protocol_version=body["runtime_protocol_version"],
        )

    def claim(self, *, runtime_instance_id: str, max_messages: int) -> dict[str, Any]:
        return self._post(
            "/claim",
            {"runtime_instance_id": runtime_instance_id, "max_messages": max_messages},
        )

    def acknowledge(
        self,
        *,
        runtime_instance_id: str,
        delivery_id: str,
        outcome: str,
        failure_category: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "runtime_instance_id": runtime_instance_id,
            "delivery_id": delivery_id,
            "outcome": outcome,
        }
        if failure_category is not None:
            body["failure_category"] = failure_category
        if detail is not None:
            # Truncated here as well as server-side. §57 forbids result values, patient names and
            # raw messages in failure text; an adapter that returns a whole exception repr should
            # not be able to push clinical content across the wire in a diagnostic field.
            body["detail"] = detail[:512]
        return self._post("/ack", body)

    def submit(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._post("/submit", {"envelope": envelope})

    # -- plumbing -----------------------------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"/exchange/v1/connect/runtime{path}"
        try:
            response = self._client.post(
                url, json=body, headers={"Authorization": f"Bearer {self._tokens.bearer()}"}
            )
        except httpx.HTTPError as error:
            # The exception type, not its string. httpx puts the full URL in some messages, and a
            # URL is the one part of this that could carry a query-string credential in a
            # misconfigured deployment.
            raise RuntimeUnreachable(f"{type(error).__name__} calling {path}") from None

        if response.status_code >= 500:
            raise RuntimeApiError(response.status_code, _detail(response), retryable=True)
        if response.status_code >= 400:
            raise RuntimeApiError(response.status_code, _detail(response), retryable=False)
        return response.json()


def _detail(response: httpx.Response) -> str:
    """The server's own short reason, or the status text. Never the request that produced it.

    Bounded because a validation error body is a list of per-field objects that can run to
    kilobytes, and this string ends up in logs and in the agent's own failure column.
    """
    try:
        payload = response.json()
    except ValueError:
        return response.reason_phrase or "unreadable response"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail[:512]
    if detail is not None:
        return str(detail)[:512]
    return response.reason_phrase or "no detail"
