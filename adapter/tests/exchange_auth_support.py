"""DH-EX-002 test helpers for OIDC token validation -- shared by ``test_exchange_*.py``.

Deliberately not a ``conftest.py``, matching ``exchange_support.py``'s own rationale: plain
functions, zero effect on any other test file's collection or fixtures.

Every token here is signed with a **locally generated** RSA keypair (via the ``cryptography``
package, a ``PyJWT[crypto]`` transitive dependency) -- no test in this repository fetches a real
JWKS document or reaches the network. ``StaticKeySource`` (from ``exchange/identity_auth.py``)
wraps the public key directly, standing in for what ``JWKSKeySource`` would fetch in production.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from daher_adapter.exchange.identity_auth import KeySource, StaticKeySource, TokenValidator

ISSUER = "https://exchange-test.invalid/realms/dhealth-exchange"
AUDIENCE = "dhealth-exchange-api"
PLATFORM_ADMIN_ROLE = "dhealth-platform-admin"


def generate_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


class RaisingKeySource:
    """Simulates an unreachable/unknown signing key (e.g. a real JWKS fetch failure or an
    unrecognised ``kid``) -- `TokenValidator` must convert this into the same
    `TokenValidationError` as every other failure mode, never let it escape as a 500."""

    def get_signing_key(self, token: str) -> object:  # noqa: ARG002
        raise RuntimeError("simulated signing-key resolution failure")


def build_validator(
    public_key: RSAPublicKey,
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    platform_admin_role: str = PLATFORM_ADMIN_ROLE,
    leeway_seconds: int = 0,
    key_source: KeySource | None = None,
) -> TokenValidator:
    return TokenValidator(
        issuer=issuer,
        audience=audience,
        key_source=key_source or StaticKeySource(key=public_key),
        platform_admin_role=platform_admin_role,
        leeway_seconds=leeway_seconds,
    )


def make_token(
    private_key: RSAPrivateKey,
    *,
    subject: str | None = "user-1",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    roles: list[str] | None = None,
    email: str | None = None,
    name: str | None = None,
    expires_in: int = 300,
    not_before: int | None = None,
    issued_at: int | None = None,
    omit_exp: bool = False,
    omit_iat: bool = False,
    omit_sub: bool = False,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"iss": issuer, "aud": audience}
    if not omit_sub and subject is not None:
        claims["sub"] = subject
    if not omit_iat:
        claims["iat"] = issued_at if issued_at is not None else now
    if not omit_exp:
        claims["exp"] = now + expires_in
    if not_before is not None:
        claims["nbf"] = not_before
    if roles is not None:
        claims["realm_access"] = {"roles": roles}
    if email is not None:
        claims["email"] = email
    if name is not None:
        claims["name"] = name
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, private_key, algorithm="RS256")


def make_alg_none_token(
    *,
    subject: str = "user-1",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_in: int = 300,
) -> str:
    """Hand-built, unsigned ``alg: none`` token -- PyJWT's own ``encode`` refuses to produce this,
    which is itself evidence the library never emits it; the attack this test proves refused is a
    *received* token claiming ``alg: none``, so it must be constructed independently of PyJWT."""
    now = int(time.time())
    header = {"alg": "none", "typ": "JWT"}
    payload = {"iss": issuer, "aud": audience, "sub": subject, "iat": now, "exp": now + expires_in}

    def b64(data: dict[str, Any]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{b64(header)}.{b64(payload)}."
