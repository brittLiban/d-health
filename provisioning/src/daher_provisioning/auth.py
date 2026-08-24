"""Scoped credential authentication for the provisioning API (ADR-021).

The portal is an **untrusted client**. It authenticates with a scoped credential over TLS,
not a shared secret pasted into both codebases, and that credential grants exactly the
facility-lifecycle operations it needs — never shell, Docker socket, or database access.

Design points that are security-relevant rather than stylistic:

- **Keys are stored hashed.** A compromised API host must not yield a usable credential for
  a *different* host. SHA-256 rather than bcrypt/argon2 is correct here and deliberate:
  those exist to slow brute force against *low-entropy human passwords*. These keys carry
  256 bits of entropy from ``secrets.token_urlsafe``, so there is nothing to brute-force
  and a deliberately slow hash would only make every request slower.
- **Comparison is constant-time**, so a timing signal cannot be used to recover a key byte
  by byte.
- **An unknown key id still performs a comparison.** Returning early would leak which ids
  exist through response timing.
- **The key value is never logged**, returned, or placed in an error. Errors identify the
  key by id, which is safe to record.
"""

from __future__ import annotations

import hashlib
import secrets as _secrets
from dataclasses import dataclass, field
from enum import StrEnum

# `dhp` = daher provisioning. The id travels in the clear so the server can look up exactly
# one candidate rather than comparing against every stored key.
KEY_PREFIX = "dhp"
_KEY_PARTS = 3


class Scope(StrEnum):
    """Exactly the capabilities provisioning needs. Nothing here implies host access."""

    FACILITY_CREATE = "facility:create"
    FACILITY_READ = "facility:read"
    CREDENTIALS_ROTATE = "credentials:rotate"
    FACILITY_DECOMMISSION = "facility:decommission"
    # UI-010/DH-008E: one scope for both directions. Enabling and disabling break-glass need
    # the same trust level — an operator authorized to declare an emergency is also the one
    # who ends it — so splitting this into two scopes would not buy an isolation the threat
    # model asks for, only two places to keep in sync.
    BREAK_GLASS = "facility:break_glass"


# What the portal holds. Deliberately not every scope — decommission stops a facility's
# stack, so it is granted explicitly rather than by belonging to the default set.
# BREAK_GLASS is withheld for the same reason: it is a privileged emergency action (§6),
# not a routine portal operation, and is issued only to whichever separate operator
# mechanism is authorized to invoke it.
PORTAL_SCOPES = frozenset({Scope.FACILITY_CREATE, Scope.FACILITY_READ, Scope.CREDENTIALS_ROTATE})


class AuthenticationError(RuntimeError):
    """The presented credential is missing, malformed, or wrong."""


class AuthorizationError(RuntimeError):
    """The credential is valid but does not carry the required scope."""


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApiKey:
    """A stored credential. Holds a hash, never the secret."""

    key_id: str
    key_hash: str
    scopes: frozenset[Scope]
    actor: str
    active: bool = True


@dataclass(frozen=True)
class Principal:
    """An authenticated caller."""

    key_id: str
    actor: str
    scopes: frozenset[Scope] = field(default_factory=frozenset)

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes


def issue_key(key_id: str, actor: str, scopes: frozenset[Scope]) -> tuple[str, ApiKey]:
    """Mint a credential.

    Returns ``(presentable_key, stored_record)``. The presentable value is the **only**
    time the secret exists in this process — it is handed to the operator out of band and
    never persisted here. Losing it means issuing a new one, which is the correct
    behaviour, not an inconvenience.
    """
    if not key_id or "_" in key_id:
        raise AuthenticationError("key id must be non-empty and contain no underscore")
    secret = _secrets.token_urlsafe(32)
    presentable = f"{KEY_PREFIX}_{key_id}_{secret}"
    return presentable, ApiKey(
        key_id=key_id, key_hash=hash_key(secret), scopes=frozenset(scopes), actor=actor
    )


def _split(presented: str) -> tuple[str, str]:
    parts = presented.split("_", 2)
    if len(parts) != _KEY_PARTS or parts[0] != KEY_PREFIX or not parts[1] or not parts[2]:
        raise AuthenticationError("malformed credential")
    return parts[1], parts[2]


class KeyStore:
    """Holds issued credentials. In-memory here; a durable store slots in behind it."""

    def __init__(self, keys: list[ApiKey] | None = None) -> None:
        self._keys: dict[str, ApiKey] = {key.key_id: key for key in (keys or [])}
        # A stable decoy so an unknown key id costs the same work as a known one.
        self._decoy_hash = hash_key(_secrets.token_urlsafe(32))

    def add(self, key: ApiKey) -> None:
        self._keys[key.key_id] = key

    def revoke(self, key_id: str) -> None:
        existing = self._keys.get(key_id)
        if existing is not None:
            self._keys[key_id] = ApiKey(
                key_id=existing.key_id,
                key_hash=existing.key_hash,
                scopes=existing.scopes,
                actor=existing.actor,
                active=False,
            )

    def authenticate(self, presented: str | None) -> Principal:
        """Verify a presented credential in constant time with respect to key existence."""
        if not presented:
            raise AuthenticationError("no credential presented")

        key_id, secret = _split(presented)
        record = self._keys.get(key_id)
        # Compare regardless: an early return here would tell an attacker which key ids
        # exist purely from how fast the request failed.
        expected = record.key_hash if record is not None else self._decoy_hash
        matched = _secrets.compare_digest(hash_key(secret), expected)

        if record is None or not matched:
            # The message names the id, never the secret. The id is safe to log.
            raise AuthenticationError(f"invalid credential for key id {key_id!r}")
        if not record.active:
            raise AuthenticationError(f"credential {key_id!r} is revoked")

        return Principal(key_id=record.key_id, actor=record.actor, scopes=record.scopes)


def require(principal: Principal, scope: Scope) -> None:
    """Enforce a scope. Raises rather than returning a boolean nobody checks."""
    if not principal.has(scope):
        raise AuthorizationError(
            f"credential {principal.key_id!r} lacks scope {scope.value!r}. Provisioning "
            f"credentials are scoped deliberately — widening one is a security decision, "
            f"not a fix for a 403 (ADR-021)."
        )
