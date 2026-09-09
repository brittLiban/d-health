"""DH-EX-002: OIDC bearer-token validation for the Exchange API.

No hand-written cryptography anywhere in this module -- signature verification, algorithm
allowlisting, and claim validation (``exp``, ``nbf``, ``iss``, ``aud``) are all delegated to
``PyJWT`` (with its ``cryptography`` backend for RS256). This module's own job is narrow: turn a
raw ``Authorization`` header into a ``ValidatedToken`` or a single ``TokenValidationError`` --
every failure mode collapses to the same exception type and, at the API layer, the same ``401``,
deliberately, so no response ever distinguishes *why* a token was rejected (a wrong issuer looks
identical to an expired token to a caller -- avoids leaking validation internals).

Key loading is behind the ``KeySource`` protocol specifically so tests never need network access:
``JWKSKeySource`` wraps ``jwt.PyJWKClient`` for production; ``StaticKeySource`` wraps a
locally-generated key for tests. Neither this module nor its tests fetch a real JWKS document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import jwt
from jwt import PyJWKClient

DEFAULT_ALGORITHMS: tuple[str, ...] = ("RS256",)


class TokenValidationError(RuntimeError):
    """Any bearer-token validation failure -- missing header, malformed token, bad signature,
    wrong issuer/audience, expired, not-yet-valid, missing ``sub``, or an unreachable/unknown
    signing key. Deliberately one exception type: the API layer maps every instance to ``401``
    without inspecting which specific check failed, so no response leaks *why* a token failed."""


@dataclass(frozen=True, slots=True)
class ValidatedToken:
    issuer: str
    subject: str
    email: str | None
    display_name: str | None
    roles: frozenset[str]

    def has_role(self, role: str) -> bool:
        return role in self.roles


class KeySource(Protocol):
    def get_signing_key(self, token: str) -> object:
        """Returns an object with a ``.key`` attribute holding the verification key material, or
        raises on any failure (unreachable JWKS, unknown key id, and so on)."""
        ...


class JWKSKeySource:
    """Production key source -- fetches and caches signing keys from a real JWKS endpoint via
    PyJWT's own client. Never used in this repository's tests."""

    def __init__(self, jwks_url: str) -> None:
        self._client = PyJWKClient(jwks_url, cache_keys=True)

    def get_signing_key(self, token: str) -> object:
        return self._client.get_signing_key_from_jwt(token)


@dataclass(frozen=True, slots=True)
class StaticKeySource:
    """Test-only key source: wraps one locally-generated key, never touches the network. The
    object returned must expose ``.key`` (e.g. an ``authlib``/``cryptography`` public key object
    or PEM string) matching what ``jwt.decode`` accepts for the configured algorithm."""

    key: object

    def get_signing_key(self, token: str) -> object:  # noqa: ARG002 -- protocol shape
        return self


class UnconfiguredKeySource:
    """Used when ``Settings.exchange_oidc_configured`` is `False` -- every request fails closed
    with the exact same `TokenValidationError` any other invalid token produces, rather than the
    application refusing to start or (worse) silently accepting every token."""

    def get_signing_key(self, token: str) -> object:  # noqa: ARG002
        raise TokenValidationError("Exchange OIDC is not configured")


class TokenValidator:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_source: KeySource,
        algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS,
        leeway_seconds: int = 0,
        platform_admin_role: str = "dhealth-platform-admin",
    ) -> None:
        if not algorithms:
            raise ValueError("at least one algorithm is required")
        if "none" in {algorithm.lower() for algorithm in algorithms}:
            raise ValueError("algorithm 'none' is never permitted")
        self._issuer = issuer
        self._audience = audience
        self._key_source = key_source
        self._algorithms = algorithms
        self._leeway_seconds = leeway_seconds
        self._platform_admin_role = platform_admin_role

    def validate_header(self, authorization_header: str | None) -> ValidatedToken:
        token = self._extract_bearer_token(authorization_header)
        try:
            signing_key = self._key_source.get_signing_key(token)
        except TokenValidationError:
            raise
        except Exception as error:
            raise TokenValidationError("unable to resolve signing key") from error

        try:
            claims = jwt.decode(
                token,
                key=signing_key.key,  # type: ignore[attr-defined]
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.ExpiredSignatureError as error:
            raise TokenValidationError("token expired") from error
        except jwt.ImmatureSignatureError as error:
            raise TokenValidationError("token not yet valid") from error
        except jwt.InvalidIssuerError as error:
            raise TokenValidationError("wrong issuer") from error
        except jwt.InvalidAudienceError as error:
            raise TokenValidationError("wrong audience") from error
        except jwt.InvalidAlgorithmError as error:
            raise TokenValidationError("unsupported algorithm") from error
        except jwt.InvalidSignatureError as error:
            raise TokenValidationError("invalid signature") from error
        except jwt.MissingRequiredClaimError as error:
            raise TokenValidationError("missing required claim") from error
        except jwt.DecodeError as error:
            raise TokenValidationError("malformed token") from error
        except jwt.PyJWTError as error:
            raise TokenValidationError("invalid token") from error

        subject = claims.get("sub")
        if not subject or not isinstance(subject, str):
            raise TokenValidationError("token has no subject")

        return ValidatedToken(
            issuer=claims["iss"],
            subject=subject,
            email=claims.get("email"),
            display_name=claims.get("name"),
            roles=_extract_realm_roles(claims),
        )

    @staticmethod
    def _extract_bearer_token(authorization_header: str | None) -> str:
        if not authorization_header:
            raise TokenValidationError("missing Authorization header")
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise TokenValidationError("malformed Authorization header")
        return token.strip()


def _extract_realm_roles(claims: dict[str, object]) -> frozenset[str]:
    """Keycloak's standard shape: ``realm_access.roles``. Absent/malformed claim -> no roles,
    never an error -- a token missing this claim simply grants no platform-admin authority."""
    realm_access = claims.get("realm_access")
    if not isinstance(realm_access, dict):
        return frozenset()
    roles = realm_access.get("roles")
    if not isinstance(roles, list):
        return frozenset()
    return frozenset(role for role in roles if isinstance(role, str))
