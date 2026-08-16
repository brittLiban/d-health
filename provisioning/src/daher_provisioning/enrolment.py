"""Host enrolment — the one manual step that does not disappear (ADR-022).

A host cannot poll for work until it has an identity, and that first credential cannot be
delivered automatically without recreating the problem it solves. So an operator carries a
**single-use, short-lived enrollment token** to the VM by hand, once.

The property that makes this worth the manual step: **the agent generates its own long-lived
credential locally and sends only the hash.** The provisioning API never sees an agent's
secret, not even during enrolment — so a breached API host cannot impersonate any facility
agent, past or future.

Three refusals, each closing a way a leaked token would otherwise be useful:

1. **Single use.** A redeemed token is dead, even within its lifetime.
2. **Short lived.** A token that leaks into a shell history or a chat log expires on its own.
3. **Bound to one host.** A token minted for `host-a` cannot enrol `host-b`, so a leaked
   token cannot be used to introduce an attacker-controlled host into the fleet.
"""

from __future__ import annotations

import hashlib
import secrets as _secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

TOKEN_PREFIX = "dhe"  # noqa: S105 - a token prefix, not a secret
_TOKEN_PARTS = 3
DEFAULT_LIFETIME_SECONDS = 3600


class EnrolmentError(RuntimeError):
    """The enrollment token is missing, malformed, expired, spent, or for another host."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass
class EnrolmentToken:
    """A minted token. Holds a hash, never the secret."""

    host_id: str
    token_hash: str
    expires_at: datetime
    redeemed_at: datetime | None = None

    @property
    def spent(self) -> bool:
        return self.redeemed_at is not None


@dataclass(frozen=True)
class EnrolledAgent:
    """What the API keeps after enrolment: an identity and a hash. Never a secret."""

    host_id: str
    credential_hash: str
    enrolled_at: datetime


@dataclass
class EnrolmentService:
    lifetime_seconds: int = DEFAULT_LIFETIME_SECONDS
    _tokens: dict[str, EnrolmentToken] = field(default_factory=dict)
    _agents: dict[str, EnrolledAgent] = field(default_factory=dict)

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def mint(self, host_id: str) -> tuple[str, EnrolmentToken]:
        """Mint a token for exactly one host.

        Returns ``(presentable_token, record)``. The presentable value exists only here and
        in the operator's hands — it is not persisted, so losing it means minting another.
        """
        if not host_id or "_" in host_id:
            raise EnrolmentError("host id must be non-empty and contain no underscore")
        if host_id in self._agents:
            raise EnrolmentError(
                f"host {host_id!r} is already enrolled. Re-enrolling would replace a live "
                f"agent's credential; revoke it deliberately first."
            )

        secret = _secrets.token_urlsafe(32)
        record = EnrolmentToken(
            host_id=host_id,
            token_hash=_hash(secret),
            expires_at=self._now() + timedelta(seconds=self.lifetime_seconds),
        )
        self._tokens[host_id] = record
        return f"{TOKEN_PREFIX}_{host_id}_{secret}", record

    def redeem(self, presented: str, *, credential_hash: str) -> EnrolledAgent:
        """Redeem a token, registering the hash of a credential the agent generated itself.

        ``credential_hash`` is a hash, by contract. Passing a secret here would put an
        agent's credential on the API host, which is the thing this design exists to avoid.
        """
        parts = (presented or "").split("_", 2)
        if len(parts) != _TOKEN_PARTS or parts[0] != TOKEN_PREFIX or not parts[1] or not parts[2]:
            raise EnrolmentError("malformed enrollment token")
        host_id, secret = parts[1], parts[2]

        record = self._tokens.get(host_id)
        # Compare regardless of whether a token exists, so a wrong host id and a wrong
        # secret cost the same and neither can be distinguished by timing.
        expected = record.token_hash if record is not None else _hash(_secrets.token_urlsafe(32))
        matched = _secrets.compare_digest(_hash(secret), expected)

        if record is None or not matched:
            raise EnrolmentError(f"invalid enrollment token for host {host_id!r}")
        if record.spent:
            raise EnrolmentError(
                f"enrollment token for {host_id!r} was already redeemed. Tokens are "
                f"single-use: a replayed one is refused even inside its lifetime."
            )
        if self._now() > record.expires_at:
            raise EnrolmentError(
                f"enrollment token for {host_id!r} expired at {record.expires_at.isoformat()}. "
                f"Mint a new one rather than extending this."
            )
        if len(credential_hash) != 64:
            raise EnrolmentError(
                "credential_hash must be a 64-character SHA-256 hex digest. The agent "
                "generates its credential locally and sends only the hash — a secret must "
                "never reach the provisioning host."
            )

        record.redeemed_at = self._now()
        agent = EnrolledAgent(
            host_id=host_id, credential_hash=credential_hash, enrolled_at=record.redeemed_at
        )
        self._agents[host_id] = agent
        return agent

    def agent(self, host_id: str) -> EnrolledAgent:
        try:
            return self._agents[host_id]
        except KeyError as exc:
            raise EnrolmentError(f"host {host_id!r} has not enrolled") from exc

    def is_enrolled(self, host_id: str) -> bool:
        return host_id in self._agents


def generate_agent_credential(host_id: str) -> tuple[str, str]:
    """Run **on the facility host**. Returns ``(secret_to_keep, hash_to_send)``.

    The secret never leaves the host. Only the hash is sent to the API during redemption,
    which is why a breached provisioning host cannot impersonate any agent.
    """
    secret = _secrets.token_urlsafe(32)
    return f"dha_{host_id}_{secret}", _hash(secret)
