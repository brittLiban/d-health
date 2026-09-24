"""DH-NOTIFY-META-001R: what the public edge actually publishes, and what it must not.

DH-NOTIFY-META-001 built the Meta webhook and told operators, in
``docs/META-WHATSAPP-RUNBOOK.md``, to point Meta's callback at it. Nothing routed that path.
``exchange.<BASE_DOMAIN>`` forwarded ``/exchange/v1/*`` and answered 404 to everything else, so a
correctly configured callback terminated at the proxy. The failure mode is silent by construction --
a subscription that never verifies and a status callback that never arrives look exactly like a
provider that has sent nothing, which is also the true state of this deployment.

**What this file is and is not.** It reads ``caddy/Caddyfile`` as text and asserts on the routing
it declares. It is a static check: it cannot prove Caddy parses the file, cannot prove the upstream
answers, and cannot prove bytes survive the hop. Those were proven separately by running Caddy
2.11.4 against this exact file over a real HTTPS round trip -- see ``PROJECT_STATE.md``'s
DH-NOTIFY-META-001R entry for the transcript -- and that run is not repeatable inside this suite,
which has no Caddy binary and no Docker daemon. This file exists so the *route cannot silently
disappear* between such runs, which is the failure this ticket was written about.

The pairing matters more than either half: the Caddyfile is asserted to publish exactly the path
the FastAPI application is asserted to serve. Renaming one without the other turns this red.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from daher_adapter.app import create_app
from daher_adapter.config import Settings

CADDYFILE = Path(__file__).resolve().parents[2] / "caddy" / "Caddyfile"

#: The one public callback. Written out rather than imported so that changing the router's prefix
#: constant cannot quietly change what this file claims the edge publishes.
META_WEBHOOK_PATH = "/notifications/v1/meta/webhook"
EXCHANGE_PREFIX = "/exchange/v1/*"


def _site_block(hostname: str) -> str:
    """The body of one site block, by brace matching from its opening line."""
    source = CADDYFILE.read_text(encoding="utf-8")
    marker = f"{hostname} {{"
    start = source.index(marker) + len(marker)
    depth = 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"unbalanced braces in {hostname} block")


def _handled_paths(block: str) -> list[str]:
    """Every path matcher given to a ``handle`` directive in this block.

    Comment lines are stripped first: this file explains its own routing at length, and a path
    mentioned in prose is not a route.
    """
    without_comments = "\n".join(
        line for line in block.splitlines() if not line.strip().startswith("#")
    )
    return re.findall(r"^\s*handle\s+(\S+)\s*\{", without_comments, flags=re.MULTILINE)


@pytest.fixture(scope="module")
def exchange_block() -> str:
    return _site_block("exchange.{$BASE_DOMAIN}")


def test_the_meta_webhook_path_is_published_on_the_exchange_host(exchange_block: str) -> None:
    """A. The route the runbook tells an operator to configure is actually routed."""
    assert META_WEBHOOK_PATH in _handled_paths(exchange_block)


def test_the_meta_webhook_is_forwarded_to_the_adapter(exchange_block: str) -> None:
    """Published is not the same as forwarded: the handler must reach the adapter."""
    handler = re.search(
        rf"handle\s+{re.escape(META_WEBHOOK_PATH)}\s*\{{(.*?)\}}", exchange_block, flags=re.DOTALL
    )
    assert handler is not None, "the webhook handle block is missing"
    assert "reverse_proxy adapter:8000" in handler.group(1)


def test_the_exchange_api_route_is_unchanged(exchange_block: str) -> None:
    """B. The pre-existing public surface must survive this ticket exactly."""
    assert EXCHANGE_PREFIX in _handled_paths(exchange_block)
    handler = re.search(
        rf"handle\s+{re.escape(EXCHANGE_PREFIX)}\s*\{{(.*?)\}}", exchange_block, flags=re.DOTALL
    )
    assert handler is not None
    assert "reverse_proxy adapter:8000" in handler.group(1)


def test_the_exchange_host_publishes_nothing_else(exchange_block: str) -> None:
    """C. Fail-closed. The whole point of scoping this vhost is that a route added to the adapter
    later does not become public by omission, so the *set* is asserted, not merely its members."""
    assert sorted(_handled_paths(exchange_block)) == sorted([EXCHANGE_PREFIX, META_WEBHOOK_PATH])
    assert re.search(r"handle\s*\{\s*respond 404", exchange_block), (
        "the catch-all must answer 404 rather than falling through to the adapter"
    )


def test_the_notification_namespace_is_not_published_wholesale(exchange_block: str) -> None:
    """C. One path, not the prefix.

    ``/notifications/v1/*`` would hand the public edge every future route in that namespace --
    retry, enrolment, per-notification reads -- by omission. That is the exact mistake this vhost
    was scoped to avoid in the first place, and it is worth its own assertion because the wildcard
    is one character away from the correct line.
    """
    for path in _handled_paths(exchange_block):
        assert not path.startswith("/notifications") or path == META_WEBHOOK_PATH, (
            f"{path} publishes more of the notification namespace than the Meta callback"
        )


def test_adapter_health_endpoints_are_not_published(exchange_block: str) -> None:
    """C. Named explicitly because these are the routes most likely to be exposed for convenience,
    and because ``app.py``'s own comment promises they are not public."""
    published = _handled_paths(exchange_block)
    for path in ("/health/live", "/health/ready", "/health/exchange-ready"):
        assert not any(path.startswith(handled.rstrip("*")) for handled in published)


def test_the_application_serves_exactly_the_published_webhook_path(
    engine: Engine, tmp_path: Path
) -> None:
    """The other half of the pairing: the app really answers the path the edge publishes, on both
    methods Meta uses. Unconfigured here, so both are 403 -- that is the documented fail-closed
    state, and a 404 would mean the path published above reaches nothing."""
    settings = Settings(
        timezone="Africa/Djibouti",
        db_host="unused",
        db_port=5432,
        db_name="adapter",
        db_user="adapter",
        db_password="not-a-real-secret",
        audit_path=tmp_path / "audit" / "audit.jsonl",
        retry_max_attempts=5,
        retry_multiplier_seconds=1,
        retry_max_seconds=30,
    )
    with TestClient(create_app(settings, engine=engine)) as client:
        assert client.get(META_WEBHOOK_PATH).status_code == 403
        assert client.post(META_WEBHOOK_PATH, content=b"{}").status_code == 403
