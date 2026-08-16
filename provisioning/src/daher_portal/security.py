"""Portal authentication (ADR-020, ADR-023).

**Which libraries, and why** — the build prompt asked for this explicitly.

- **Password hashing: `argon2-cffi`.** Argon2id is the current password-hashing
  recommendation and this is the reference binding. Nothing here implements a KDF; the
  library owns salt generation, parameters, and constant-time verification.
- **Sessions: Starlette's `SessionMiddleware`**, which signs cookies with `itsdangerous`.
  Session handling is the other place hand-rolling goes wrong, so it is delegated too.

⚠️ **What this does NOT give you: MFA.** A single password protects a control plane that
can create and stop clinical deployments. That is the weakest link in the portal and it is
stated here rather than discovered later. The upgrade is an external IdP (Authlib against
Google/GitHub/an OIDC provider), which also removes password storage entirely. Deliberately
not done now: it adds a hard external dependency on a login path that must work when things
are broken, and this is a single-operator pilot.

**The API credential never reaches the browser.** It lives in this server's environment and
is used server-side (ADR-023). Nothing in a template renders it.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

CSRF_SESSION_KEY = "csrf_token"  # noqa: S105 - a session key name, not a secret
USER_SESSION_KEY = "operator"


class AuthError(RuntimeError):
    """Login failed. The message is safe to show; it never says which half was wrong."""


@dataclass(frozen=True)
class Operator:
    """The one account that can drive the fleet."""

    username: str
    password_hash: str


def hash_password(password: str) -> str:
    if len(password) < 12:
        # A control plane that can stop clinical deployments should not be behind a short
        # password. Refuse at creation rather than warn.
        raise AuthError("portal password must be at least 12 characters")
    return _hasher.hash(password)


def verify_password(operator: Operator | None, username: str, password: str) -> Operator:
    """Verify credentials in roughly constant time with respect to account existence.

    An unknown username still performs a hash verification against a dummy, so "no such
    user" and "wrong password" cost the same and cannot be told apart by timing.
    """
    dummy = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHRzb21lc2FsdA$" + "A" * 43
    target = operator.password_hash if operator else dummy

    try:
        _hasher.verify(target, password)
        matched = True
    except VerifyMismatchError:
        matched = False
    except Exception:
        # A malformed stored hash must fail closed, not raise into a 500 that reveals it.
        matched = False

    if operator is None or not matched or not hmac.compare_digest(operator.username, username):
        # One message for every failure. Saying "no such user" tells an attacker which
        # half to keep guessing.
        raise AuthError("invalid username or password")
    return operator


def operator_from_env() -> Operator | None:
    """Load the single operator account from configuration.

    The hash is configured, never the password: a plaintext password in an env file is a
    plaintext password in every backup and process listing of that host.
    """
    username = os.environ.get("PORTAL_USERNAME")
    password_hash = os.environ.get("PORTAL_PASSWORD_HASH")
    if not username or not password_hash:
        return None
    return Operator(username=username, password_hash=password_hash)


def issue_csrf(session: dict) -> str:
    """One token per session, held server-side in the signed session cookie."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def check_csrf(session: dict, submitted: str | None) -> None:
    """Reject a state-changing request whose token does not match the session.

    Without this, any page on the internet could make an authenticated operator's browser
    create or decommission a facility simply by submitting a form at this site.
    """
    expected = session.get(CSRF_SESSION_KEY)
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        raise AuthError("invalid or missing CSRF token")
