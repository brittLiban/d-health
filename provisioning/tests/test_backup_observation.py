"""Parsing `last-run.status` (DH-027H).

Pure function tests, no Docker, no agent — exactly the same reasoning
`test_infrastructure_status.py` uses for `infrastructure.py`: the rules are decided here, from
text, so they can be tested exhaustively. The rule under test throughout: only a well-formed
`SUCCESS`/`FAILED` line is evidence of anything. Everything else must never parse into `SUCCESS`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from daher_agent.backup_observation import BackupStatusError, parse_last_run_status
from daher_provisioning.models import BackupState

NOW = datetime(2026, 8, 21, 9, 41, 0, tzinfo=UTC)


class TestWellFormedInput:
    def test_success_parses_with_the_run_timestamp_not_the_observation_time(self):
        report = parse_last_run_status("SUCCESS 20260821T020000Z\n", observed_at=NOW)
        assert report.state is BackupState.SUCCESS
        assert report.checked_at == datetime(2026, 8, 21, 2, 0, 0, tzinfo=UTC)
        assert report.checked_at != NOW, (
            "the backup ran at 02:00 and was merely observed at 09:41 — these must not collapse"
        )

    def test_failed_parses(self):
        report = parse_last_run_status("FAILED 20260820T020000Z\n", observed_at=NOW)
        assert report.state is BackupState.FAILED

    def test_a_trailing_newline_is_tolerated(self):
        assert parse_last_run_status("SUCCESS 20260821T020000Z", observed_at=NOW).state is (
            BackupState.SUCCESS
        )


class TestMalformedInputNeverParsesToSuccess:
    """Every case here must raise, not silently degrade to a plausible-looking report — a
    parse that quietly accepted 'close enough' is exactly the false green this exists to stop.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "\n",
            "SUCCESS",
            "SUCCESS ",
            "SUCCESS 20260821T020000Z extra",
            "UNKNOWN 20260821T020000Z",
            "success 20260821T020000Z",
            "SUCCESS not-a-timestamp",
            "SUCCESS 2026-08-21T02:00:00Z",
            "SUCCESS 20260821T020000Z\nFAILED 20260820T010000Z",
            "garbage line entirely",
        ],
        ids=[
            "empty",
            "newline-only",
            "state-word-only",
            "state-word-and-trailing-space-only",
            "extra-field",
            "unrecognised-state",
            "lowercase-state",
            "unparseable-timestamp",
            "wrong-timestamp-format",
            "two-lines",
            "no-structure-at-all",
        ],
    )
    def test_never_parses(self, text):
        with pytest.raises(BackupStatusError):
            parse_last_run_status(text, observed_at=NOW)

    def test_a_future_timestamp_beyond_tolerance_is_refused(self):
        """Reuses the same 60s clock-skew tolerance `JobValidator` already applies to job
        issuance, rather than inventing a second constant — a marker claiming to be from
        five minutes in the future is not evidence, it is a clock problem or a forgery."""
        future = NOW + timedelta(minutes=5)
        text = f"SUCCESS {future.strftime('%Y%m%dT%H%M%SZ')}"
        with pytest.raises(BackupStatusError):
            parse_last_run_status(text, observed_at=NOW)

    def test_a_few_seconds_in_the_future_is_within_tolerance(self):
        """Clock skew between the backup container and the observing agent is real and
        small; only refuse when it exceeds the documented tolerance."""
        near_future = NOW + timedelta(seconds=30)
        text = f"SUCCESS {near_future.strftime('%Y%m%dT%H%M%SZ')}"
        report = parse_last_run_status(text, observed_at=NOW)
        assert report.state is BackupState.SUCCESS


class TestControlTheMarkerStateIsWhatMatters:
    """The false-green control this ticket names explicitly: a command around this file can
    exit 0 while the file itself says FAILED. The parser must report what the file says,
    never let anything downstream substitute a command's exit code for it."""

    def test_a_failed_marker_parses_as_failed_regardless_of_anything_else(self):
        report = parse_last_run_status("FAILED 20260821T020000Z", observed_at=NOW)
        assert report.state is BackupState.FAILED
        assert report.state is not BackupState.SUCCESS
