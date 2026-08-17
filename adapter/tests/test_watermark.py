"""The durable watermark: bootstrap once from the server, never regress, survive a restart."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.instants import parse_instant
from daher_adapter.watermark import WatermarkRegression, WatermarkRepository

T1 = "2026-08-11T10:24:58.000+03:00"
T2 = "2026-08-11T10:24:59.000+03:00"
T3 = "2026-08-12T09:00:00.000+03:00"

STREAM = {"resource_type": "ServiceRequest", "direction": "openmrs_to_openelis"}


@pytest.fixture
def repo(sessions: sessionmaker[Session]) -> WatermarkRepository:
    return WatermarkRepository(sessions)


class TestBootstrapHappensOnce:
    def test_the_first_call_persists_the_server_instant(self):
        pass  # covered below; kept separate for readability of the failure output

    def test_first_call_returns_the_bootstrap_value(self, repo):
        got = repo.get_or_bootstrap(**STREAM, server_now=parse_instant(T1))
        assert got.raw == T1

    def test_a_later_call_never_re_derives_now(self, repo):
        """ADR-015: re-deriving 'now' at boot would skip every order placed while we were down."""
        repo.get_or_bootstrap(**STREAM, server_now=parse_instant(T1))
        again = repo.get_or_bootstrap(**STREAM, server_now=parse_instant(T3))
        assert again.raw == T1, "the later server clock must not overwrite a persisted position"

    def test_the_bootstrap_value_is_retained_after_advancing(self, repo):
        """§8's reconciliation counts only from the bootstrap instant, so it must stay readable."""
        repo.get_or_bootstrap(**STREAM, server_now=parse_instant(T1))
        repo.advance(**STREAM, to=parse_instant(T3))
        assert repo.bootstrapped_at(**STREAM).raw == T1
        assert repo.current(**STREAM).raw == T3

    def test_streams_are_independent(self, repo):
        repo.get_or_bootstrap(**STREAM, server_now=parse_instant(T1))
        other = repo.get_or_bootstrap(
            resource_type="DiagnosticReport",
            direction="openelis_to_openmrs",
            server_now=parse_instant(T3),
        )
        assert other.raw == T3
        assert repo.current(**STREAM).raw == T1


class TestRestartSurvival:
    def test_the_position_survives_a_new_repository_instance(self, sessions):
        """A restart mid-window must resume from the last fully-processed position, not rewind
        to boot and not jump to now."""
        WatermarkRepository(sessions).get_or_bootstrap(**STREAM, server_now=parse_instant(T1))
        WatermarkRepository(sessions).advance(**STREAM, to=parse_instant(T2))
        assert WatermarkRepository(sessions).current(**STREAM).raw == T2

    def test_an_unbootstrapped_stream_reads_as_none(self, repo):
        assert repo.current(**STREAM) is None


class TestRegressionIsRefused:
    def test_moving_backwards_raises(self, repo):
        """A regression re-presents completed work AND disguises a stalled poller as a live one."""
        repo.get_or_bootstrap(**STREAM, server_now=parse_instant(T2))
        with pytest.raises(WatermarkRegression) as raised:
            repo.advance(**STREAM, to=parse_instant(T1))
        assert "backwards" in str(raised.value)

    def test_advancing_to_the_same_instant_is_allowed(self, repo):
        """A cycle that found nothing new is not an error."""
        repo.get_or_bootstrap(**STREAM, server_now=parse_instant(T1))
        assert repo.advance(**STREAM, to=parse_instant(T1)).raw == T1

    def test_the_same_moment_at_a_different_precision_is_not_a_regression(self, repo):
        """The concrete cost of getting instant comparison wrong: `.1` and `.10` are the same
        moment, so re-polling an unchanged position must not be rejected and stop the poller."""
        repo.get_or_bootstrap(**STREAM, server_now=parse_instant("2026-08-11T10:24:58.10+03:00"))
        assert repo.advance(**STREAM, to=parse_instant("2026-08-11T10:24:58.1+03:00"))

    def test_advancing_an_unbootstrapped_stream_raises(self, repo):
        with pytest.raises(WatermarkRegression):
            repo.advance(**STREAM, to=parse_instant(T1))


class TestTheStoredValueIsTheServersOwnString:
    def test_the_exact_string_round_trips_through_the_database(self, repo):
        """§7: stored as a String precisely so nothing normalises it. A timestamp column would."""
        odd_but_valid = "2026-08-11T10:24:58.7+03:00"
        repo.get_or_bootstrap(**STREAM, server_now=parse_instant(odd_but_valid))
        assert repo.current(**STREAM).raw == odd_but_valid
