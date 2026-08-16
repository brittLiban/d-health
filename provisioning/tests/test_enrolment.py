"""Host enrolment (ADR-022).

The manual step exists to buy one property: the provisioning API never holds an agent's
credential. These tests check that property directly, and check the three ways a leaked
enrollment token would otherwise still be useful.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from daher_provisioning.enrolment import (
    EnrolmentError,
    EnrolmentService,
    generate_agent_credential,
)


def enrol(service: EnrolmentService, host_id: str = "host-a") -> tuple[str, str]:
    """Mint a token and generate the agent-side credential, as the real flow would."""
    token, _ = service.mint(host_id)
    _secret, credential_hash = generate_agent_credential(host_id)
    return token, credential_hash


class TestTheApiNeverHoldsAnAgentSecret:
    """The whole reason the manual step is worth it."""

    def test_the_agent_secret_never_reaches_the_service(self):
        service = EnrolmentService()
        token, _ = service.mint("host-a")
        secret, credential_hash = generate_agent_credential("host-a")
        service.redeem(token, credential_hash=credential_hash)

        stored = service.agent("host-a")
        assert stored.credential_hash == credential_hash
        assert secret not in stored.credential_hash
        assert secret.split("_", 2)[2] not in repr(stored)

    def test_a_secret_passed_where_a_hash_belongs_is_refused(self):
        """Guards against a caller wiring this up the dangerous way."""
        service = EnrolmentService()
        token, _ = service.mint("host-a")
        secret, _ = generate_agent_credential("host-a")
        with pytest.raises(EnrolmentError, match="never reach the provisioning host"):
            service.redeem(token, credential_hash=secret)

    def test_generated_credentials_are_unique_per_host(self):
        first, _ = generate_agent_credential("host-a")
        second, _ = generate_agent_credential("host-a")
        assert first != second


class TestSingleUse:
    def test_a_token_works_once(self):
        service = EnrolmentService()
        token, credential_hash = enrol(service)
        assert service.redeem(token, credential_hash=credential_hash).host_id == "host-a"

    def test_a_replayed_token_is_refused(self):
        service = EnrolmentService()
        token, credential_hash = enrol(service)
        service.redeem(token, credential_hash=credential_hash)
        with pytest.raises(EnrolmentError, match="already redeemed"):
            service.redeem(token, credential_hash=credential_hash)

    def test_the_refusal_says_replay_fails_even_inside_the_lifetime(self):
        service = EnrolmentService()
        token, credential_hash = enrol(service)
        service.redeem(token, credential_hash=credential_hash)
        with pytest.raises(EnrolmentError, match="inside its lifetime"):
            service.redeem(token, credential_hash=credential_hash)


class TestShortLived:
    def test_an_expired_token_is_refused(self):
        service = EnrolmentService(lifetime_seconds=1)
        token, credential_hash = enrol(service)
        service._tokens["host-a"].expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(EnrolmentError, match="expired"):
            service.redeem(token, credential_hash=credential_hash)

    def test_the_refusal_says_mint_a_new_one_rather_than_extending(self):
        service = EnrolmentService()
        token, credential_hash = enrol(service)
        service._tokens["host-a"].expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(EnrolmentError, match="rather than extending"):
            service.redeem(token, credential_hash=credential_hash)


class TestBoundToOneHost:
    def test_a_token_for_one_host_cannot_enrol_another(self):
        """A leaked token must not introduce an attacker-controlled host into the fleet."""
        service = EnrolmentService()
        token, credential_hash = enrol(service, "host-a")
        forged = token.replace("dhe_host-a_", "dhe_host-b_", 1)
        with pytest.raises(EnrolmentError, match="invalid enrollment token"):
            service.redeem(forged, credential_hash=credential_hash)

    def test_an_unknown_host_is_refused(self):
        service = EnrolmentService()
        _, credential_hash = enrol(service, "host-a")
        with pytest.raises(EnrolmentError):
            service.redeem("dhe_host-zzz_whatever", credential_hash=credential_hash)


class TestMalformedTokens:
    @pytest.mark.parametrize(
        "bad", ["", "garbage", "dhe_only-two", "wrong_host-a_secret", "dhe__secret"]
    )
    def test_malformed_tokens_are_refused(self, bad):
        service = EnrolmentService()
        _, credential_hash = enrol(service)
        with pytest.raises(EnrolmentError):
            service.redeem(bad, credential_hash=credential_hash)

    def test_a_wrong_secret_is_refused(self):
        service = EnrolmentService()
        _, credential_hash = enrol(service, "host-a")
        with pytest.raises(EnrolmentError, match="invalid enrollment token"):
            service.redeem("dhe_host-a_not-the-secret", credential_hash=credential_hash)

    def test_the_error_never_contains_the_presented_secret(self):
        service = EnrolmentService()
        _, credential_hash = enrol(service, "host-a")
        secret = "leaked-token-value-do-not-log"
        with pytest.raises(EnrolmentError) as caught:
            service.redeem(f"dhe_host-a_{secret}", credential_hash=credential_hash)
        assert secret not in str(caught.value)


class TestReEnrolment:
    def test_minting_for_an_already_enrolled_host_is_refused(self):
        """Re-enrolling would replace a live agent's credential silently."""
        service = EnrolmentService()
        token, credential_hash = enrol(service)
        service.redeem(token, credential_hash=credential_hash)
        with pytest.raises(EnrolmentError, match="revoke it deliberately first"):
            service.mint("host-a")

    def test_is_enrolled_reflects_reality(self):
        service = EnrolmentService()
        assert not service.is_enrolled("host-a")
        token, credential_hash = enrol(service)
        service.redeem(token, credential_hash=credential_hash)
        assert service.is_enrolled("host-a")

    def test_asking_for_an_unenrolled_agent_is_an_error(self):
        with pytest.raises(EnrolmentError, match="has not enrolled"):
            EnrolmentService().agent("host-a")
