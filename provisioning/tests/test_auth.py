"""Scoped credential authentication and rate limiting (ADR-021)."""

from __future__ import annotations

import pytest

from daher_provisioning.auth import (
    PORTAL_SCOPES,
    AuthenticationError,
    AuthorizationError,
    KeyStore,
    Scope,
    hash_key,
    issue_key,
    require,
)
from daher_provisioning.ratelimit import RateLimitExceeded, TokenBucketLimiter


def a_store() -> tuple[KeyStore, str]:
    presentable, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
    return KeyStore([record]), presentable


class TestIssuing:
    def test_presentable_key_has_the_expected_shape(self):
        presentable, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        assert presentable.startswith("dhp_portal_")
        assert record.key_id == "portal"

    def test_the_secret_is_never_stored(self):
        """A compromised API host must not yield a usable credential."""
        presentable, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        secret = presentable.split("_", 2)[2]
        assert secret not in record.key_hash
        assert record.key_hash == hash_key(secret)

    def test_two_issues_never_collide(self):
        first, _ = issue_key("a", actor="a", scopes=PORTAL_SCOPES)
        second, _ = issue_key("a", actor="a", scopes=PORTAL_SCOPES)
        assert first != second

    def test_key_id_with_underscore_is_refused(self):
        """The underscore is the field separator; allowing it would make parsing ambiguous."""
        with pytest.raises(AuthenticationError):
            issue_key("bad_id", actor="x", scopes=PORTAL_SCOPES)


class TestAuthentication:
    def test_valid_credential_authenticates(self):
        store, presentable = a_store()
        principal = store.authenticate(presentable)
        assert principal.key_id == "portal"
        assert principal.scopes == PORTAL_SCOPES

    def test_missing_credential_is_refused(self):
        store, _ = a_store()
        with pytest.raises(AuthenticationError, match="no credential presented"):
            store.authenticate(None)

    @pytest.mark.parametrize(
        "bad", ["", "garbage", "dhp_only-two", "wrongprefix_portal_secret", "dhp__secret"]
    )
    def test_malformed_credentials_are_refused(self, bad):
        store, _ = a_store()
        with pytest.raises(AuthenticationError):
            store.authenticate(bad)

    def test_wrong_secret_is_refused(self):
        store, _ = a_store()
        with pytest.raises(AuthenticationError, match="invalid credential"):
            store.authenticate("dhp_portal_not-the-right-secret")

    def test_unknown_key_id_is_refused(self):
        store, _ = a_store()
        with pytest.raises(AuthenticationError):
            store.authenticate("dhp_nosuchkey_whatever")

    def test_error_never_contains_the_presented_secret(self):
        """Errors reach logs; the id is safe to record, the secret is not."""
        store, _ = a_store()
        secret = "super-secret-value-do-not-log"
        with pytest.raises(AuthenticationError) as caught:
            store.authenticate(f"dhp_portal_{secret}")
        assert secret not in str(caught.value)

    def test_revoked_credential_is_refused(self):
        store, presentable = a_store()
        store.revoke("portal")
        with pytest.raises(AuthenticationError, match="revoked"):
            store.authenticate(presentable)

    def test_revoking_an_unknown_key_is_harmless(self):
        store, _ = a_store()
        store.revoke("nosuchkey")


class TestScopes:
    def test_granted_scope_is_allowed(self):
        store, presentable = a_store()
        require(store.authenticate(presentable), Scope.FACILITY_CREATE)

    def test_missing_scope_is_refused(self):
        store, presentable = a_store()
        with pytest.raises(AuthorizationError, match="lacks scope"):
            require(store.authenticate(presentable), Scope.FACILITY_DECOMMISSION)

    def test_portal_does_not_hold_decommission_by_default(self):
        """Stopping a facility's stack is granted explicitly, not by default membership."""
        assert Scope.FACILITY_DECOMMISSION not in PORTAL_SCOPES

    def test_no_scope_implies_host_access(self):
        """Nothing in the scope vocabulary grants shell, docker, or database access."""
        for scope in Scope:
            lowered = scope.value.lower()
            for forbidden in ("shell", "exec", "docker", "database", "sql", "root", "ssh"):
                assert forbidden not in lowered

    def test_authorization_error_explains_widening_is_a_security_decision(self):
        store, presentable = a_store()
        with pytest.raises(AuthorizationError, match="security decision"):
            require(store.authenticate(presentable), Scope.FACILITY_DECOMMISSION)


class TestRateLimiting:
    def test_allows_up_to_the_burst(self):
        limiter = TokenBucketLimiter(rate_per_second=1, burst=5)
        for _ in range(5):
            limiter.check("portal")

    def test_refuses_past_the_burst(self):
        limiter = TokenBucketLimiter(rate_per_second=1, burst=3)
        for _ in range(3):
            limiter.check("portal")
        with pytest.raises(RateLimitExceeded):
            limiter.check("portal")

    def test_reports_how_long_to_wait(self):
        limiter = TokenBucketLimiter(rate_per_second=2, burst=1)
        limiter.check("portal")
        with pytest.raises(RateLimitExceeded) as caught:
            limiter.check("portal")
        assert 0 < caught.value.retry_after_seconds <= 0.5

    def test_budgets_are_per_caller(self):
        limiter = TokenBucketLimiter(rate_per_second=1, burst=2)
        limiter.check("portal")
        limiter.check("portal")
        limiter.check("other-caller")  # unaffected

    def test_refills_over_time(self):
        limiter = TokenBucketLimiter(rate_per_second=1000, burst=1)
        limiter.check("portal")
        # A short real sleep is enough at this refill rate and keeps the test honest
        # rather than mocking the clock the limiter depends on.
        import time

        time.sleep(0.01)
        limiter.check("portal")

    def test_never_exceeds_the_burst_cap(self):
        limiter = TokenBucketLimiter(rate_per_second=10_000, burst=3)
        import time

        time.sleep(0.02)
        assert limiter.remaining("portal") <= 3

    def test_costly_calls_consume_more(self):
        limiter = TokenBucketLimiter(rate_per_second=1, burst=5)
        limiter.check("portal", cost=5)
        with pytest.raises(RateLimitExceeded):
            limiter.check("portal")

    def test_invalid_configuration_is_refused(self):
        with pytest.raises(ValueError):
            TokenBucketLimiter(rate_per_second=0, burst=5)
        with pytest.raises(ValueError):
            TokenBucketLimiter(rate_per_second=1, burst=0)

    def test_reset_clears_a_caller(self):
        limiter = TokenBucketLimiter(rate_per_second=1, burst=1)
        limiter.check("portal")
        limiter.reset("portal")
        limiter.check("portal")
