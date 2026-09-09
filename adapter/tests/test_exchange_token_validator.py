"""DH-EX-002's required authentication-negative matrix, at the `TokenValidator` unit level.

Every failure mode below must raise the same `TokenValidationError` -- `exchange/identity_auth.py`
deliberately collapses every distinct cause into one exception type so a caller (and, at the API
layer, an HTTP response) never leaks *why* a token was rejected. Positive-path and claim-extraction
behavior (roles, email, display name) is proven alongside the negatives in the same file since both
exercise the same `TokenValidator.validate_header`.
"""

from __future__ import annotations

import time

import pytest
from exchange_auth_support import (
    AUDIENCE,
    ISSUER,
    RaisingKeySource,
    build_validator,
    generate_keypair,
    make_alg_none_token,
    make_token,
)

from daher_adapter.exchange.identity_auth import (
    StaticKeySource,
    TokenValidationError,
    TokenValidator,
)


@pytest.fixture(scope="module")
def keypair():
    return generate_keypair()


@pytest.fixture(scope="module")
def other_keypair():
    return generate_keypair()


def test_valid_token_resolves_issuer_subject_and_claims(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(
        private_key,
        subject="alice",
        roles=["clinician", "dhealth-platform-admin"],
        email="alice@example.test",
        name="Alice Example",
    )

    result = validator.validate_header(f"Bearer {token}")

    assert result.issuer == ISSUER
    assert result.subject == "alice"
    assert result.email == "alice@example.test"
    assert result.display_name == "Alice Example"
    assert result.has_role("dhealth-platform-admin")
    assert not result.has_role("sys-admin")


def test_missing_authorization_header_is_rejected(keypair) -> None:
    _, public_key = keypair
    validator = build_validator(public_key)
    with pytest.raises(TokenValidationError):
        validator.validate_header(None)


def test_malformed_authorization_header_is_rejected(keypair) -> None:
    _, public_key = keypair
    validator = build_validator(public_key)
    with pytest.raises(TokenValidationError):
        validator.validate_header("not-a-bearer-token")
    with pytest.raises(TokenValidationError):
        validator.validate_header("Bearer ")
    with pytest.raises(TokenValidationError):
        validator.validate_header("Basic dXNlcjpwYXNz")


def test_malformed_token_body_is_rejected(keypair) -> None:
    _, public_key = keypair
    validator = build_validator(public_key)
    with pytest.raises(TokenValidationError):
        validator.validate_header("Bearer not-even-a-jwt")


def test_alg_none_token_is_rejected(keypair) -> None:
    _, public_key = keypair
    validator = build_validator(public_key)
    token = make_alg_none_token()
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_algorithm_none_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        TokenValidator(
            issuer=ISSUER,
            audience=AUDIENCE,
            key_source=None,  # type: ignore[arg-type]
            algorithms=("none",),
        )


def test_wrong_signature_is_rejected(keypair, other_keypair) -> None:
    _, public_key = keypair
    wrong_private_key, _ = other_keypair
    validator = build_validator(public_key)
    token = make_token(wrong_private_key)
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_wrong_issuer_is_rejected(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(private_key, issuer="https://attacker.invalid/realms/evil")
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_wrong_audience_is_rejected(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(private_key, audience="some-other-api")
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_expired_token_is_rejected(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(private_key, expires_in=-60)
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_future_not_before_is_rejected(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(private_key, not_before=int(time.time()) + 3600)
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_missing_subject_is_rejected(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(private_key, omit_sub=True)
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_missing_exp_is_rejected(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(private_key, omit_exp=True)
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_unresolvable_signing_key_is_rejected(keypair) -> None:
    private_key, _ = keypair
    validator = build_validator(None, key_source=RaisingKeySource())  # type: ignore[arg-type]
    token = make_token(private_key)
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_unconfigured_key_source_fails_closed(keypair) -> None:
    from daher_adapter.exchange.identity_auth import UnconfiguredKeySource

    private_key, _ = keypair
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, key_source=UnconfiguredKeySource())
    token = make_token(private_key)
    with pytest.raises(TokenValidationError):
        validator.validate_header(f"Bearer {token}")


def test_absent_realm_access_grants_no_roles(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(private_key, roles=None)
    result = validator.validate_header(f"Bearer {token}")
    assert result.roles == frozenset()


def test_malformed_realm_access_grants_no_roles(keypair) -> None:
    private_key, public_key = keypair
    validator = build_validator(public_key)
    token = make_token(private_key, extra_claims={"realm_access": "not-a-dict"})
    result = validator.validate_header(f"Bearer {token}")
    assert result.roles == frozenset()


def test_leeway_permits_a_recently_expired_token() -> None:
    private_key, public_key = generate_keypair()
    validator = build_validator(public_key, leeway_seconds=30)
    token = make_token(private_key, expires_in=-10)
    result = validator.validate_header(f"Bearer {token}")
    assert result.subject == "user-1"


def test_static_key_source_never_touches_network(keypair) -> None:
    _, public_key = keypair
    source = StaticKeySource(key=public_key)
    resolved = source.get_signing_key("irrelevant-token-value")
    assert resolved.key is public_key
