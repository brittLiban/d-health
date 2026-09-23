"""DH-CONNECT-AGENT-001 §72–§73 — the agent's configuration, and the one thing it refuses to store.

Everything comes from environment variables, and the secret comes from there and stops there: it
is held in memory for the life of the process, never written to the agent store, never logged, never
echoed by ``status``. §72 forbids committed ``.env`` credentials, and nothing in this repository
contains one -- the values below are read at startup from whatever the deployment provides.

**Safe startup** (§73). A misconfigured agent must refuse to start rather than run half-configured:
an agent that starts without a server URL does nothing while looking healthy to whoever deployed it,
which is worse than a process that exits with a message. Every required value is checked here, once,
and the error names the variable.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: Where the agent's own durable store lives. A path the partner controls: the agent runs on their
#: hardware, and DHealth has no say in their filesystem layout.
DEFAULT_STORE_PATH = "/var/lib/dhealth-connect-agent/agent.sqlite"

#: The cadence the agent aims for. The server's staleness window is three of these, so a single
#: missed cycle never flips a working facility out of routing.
DEFAULT_POLL_SECONDS = 30


class AgentConfigurationError(RuntimeError):
    """A value the agent needs and was not given. Always fatal at startup, never at request time."""


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """What the agent was told. Note what is **not** here.

    There is no ``organization_name``, no facility display name, and no way to say "act for this
    other facility". ``organization_id`` and ``installation_id`` are present only because an
    outbound envelope must name them, and the server ignores both in favour of the credential (§8)
    -- so a wrong value here produces a refused message and a loud failure, not a cross-tenant
    write.

    There is also no ``allow_insecure``, no ``verify_ssl=False`` escape hatch and no way to disable
    the credential. An agent that could be told to skip verification is an agent somebody will tell
    to skip verification on the day the certificate expires.
    """

    server_url: str
    client_id: str
    #: Held in memory only. Never persisted, never logged, never returned by ``status``.
    token: str
    organization_id: uuid.UUID
    installation_id: uuid.UUID
    source_system: str
    store_path: Path
    poll_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AgentSettings:
        source = env if env is not None else os.environ
        return cls(
            server_url=_required(source, "DHEALTH_CONNECT_SERVER_URL"),
            client_id=_required(source, "DHEALTH_CONNECT_CLIENT_ID"),
            token=_required(source, "DHEALTH_CONNECT_TOKEN"),
            organization_id=_required_uuid(source, "DHEALTH_CONNECT_ORGANIZATION_ID"),
            installation_id=_required_uuid(source, "DHEALTH_CONNECT_INSTALLATION_ID"),
            source_system=_required(source, "DHEALTH_CONNECT_SOURCE_SYSTEM"),
            store_path=Path(source.get("DHEALTH_CONNECT_STORE_PATH", DEFAULT_STORE_PATH)),
            poll_seconds=_bounded_int(
                source, "DHEALTH_CONNECT_POLL_SECONDS", DEFAULT_POLL_SECONDS, 5, 600
            ),
        )

    def redacted(self) -> dict[str, str]:
        """Everything safe to print. Used by ``status`` and by the startup log line.

        The token is absent rather than masked. A masked secret still tells a reader its length and
        first characters, and more importantly it trains everyone that secrets appear in this output
        at all -- so the next field somebody adds gets masked instead of omitted.
        """
        return {
            "server_url": self.server_url,
            "client_id": self.client_id,
            "organization_id": str(self.organization_id),
            "installation_id": str(self.installation_id),
            "source_system": self.source_system,
            "store_path": str(self.store_path),
            "poll_seconds": str(self.poll_seconds),
        }


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise AgentConfigurationError(f"{name} is required")
    return value


def _required_uuid(env: Mapping[str, str], name: str) -> uuid.UUID:
    raw = _required(env, name)
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be a UUID") from error


def _bounded_int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be an integer") from error
    if not low <= value <= high:
        raise AgentConfigurationError(f"{name} must be between {low} and {high}")
    return value
