"""Server instants: ordering must be exact, and anything unorderable must be refused.

These tests exist because `_sort` does not work on this server (WATERMARK-DESIGN.md §3), so the
adapter's own comparison is now load-bearing patient-safety logic rather than a convenience.
"""

from __future__ import annotations

import pytest

from daher_adapter.instants import (
    MalformedInstant,
    ServerInstant,
    TimezoneMismatch,
    highest,
    parse_instant,
)

OK = "2026-08-11T10:24:58.000+03:00"


class TestTheStringIsNeverAltered:
    def test_raw_is_returned_byte_identical(self):
        """§7: we hand the server back its own bytes. Anything else risks a shifted window."""
        assert parse_instant(OK).raw == OK

    def test_a_fraction_free_instant_keeps_its_exact_form(self):
        raw = "2026-08-11T10:24:58+03:00"
        assert parse_instant(raw).raw == raw


class TestOrdering:
    def test_plain_second_ordering(self):
        assert parse_instant("2026-08-11T10:24:58.000+03:00") < parse_instant(
            "2026-08-11T10:24:59.000+03:00"
        )

    def test_identical_timestamps_are_neither_greater_nor_less(self):
        """8 records share one second on this stack, so this is the common case, not the edge."""
        a, b = parse_instant(OK), parse_instant(OK)
        assert not a < b and not a > b
        assert a <= b and a >= b

    def test_the_same_moment_written_with_different_precision_compares_equal(self):
        """`.1` and `.10` are the same instant, and naive string comparison says otherwise.

        This is the case zero-padding exists for, and it is not academic. `advance()` refuses a
        watermark that moves backwards; under naive comparison `.1` reads as *earlier* than a
        persisted `.10`, so a legitimate re-poll of an unchanged position would be rejected as a
        regression and stop the poller.

        (For decimal fractions, leading digits dominate, so plain lexicographic comparison is
        otherwise correct — an earlier version of this test compared `.10` with `.9`, where naive
        and padded comparison happen to agree, and it therefore proved nothing. A control run
        that deliberately removed the padding still passed, which is how that was caught.)
        """
        short = parse_instant("2026-08-11T10:24:58.1+03:00")
        long = parse_instant("2026-08-11T10:24:58.10+03:00")
        assert not short < long
        assert not short > long
        assert short <= long and short >= long

    def test_a_shorter_fraction_is_not_treated_as_earlier(self):
        """`.2` (200ms) is later than `.15` (150ms), whichever way the digits are written."""
        assert parse_instant("2026-08-11T10:24:58.15+03:00") < parse_instant(
            "2026-08-11T10:24:58.2+03:00"
        )

    def test_highest_preserves_the_winning_original_string(self):
        a = parse_instant("2026-08-11T10:24:58.000+03:00")
        b = parse_instant("2026-08-11T10:24:59.500+03:00")
        assert highest(a, b).raw == b.raw
        assert highest(b, a).raw == b.raw


class TestRefusals:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "not-a-timestamp",
            "2026-08-11",
            "2026-08-11T10:24:58",  # no offset at all
            "11/08/2026 10:24:58+03:00",
        ],
    )
    def test_malformed_instants_are_refused_not_guessed(self, bad):
        with pytest.raises(MalformedInstant):
            parse_instant(bad)

    @pytest.mark.parametrize("missing", [None, 12345, [], {}])
    def test_a_missing_or_non_string_instant_is_refused(self, missing):
        with pytest.raises(MalformedInstant):
            parse_instant(missing)

    def test_a_utc_offset_is_refused_because_it_shifts_the_window(self):
        """§7's assertion. A UTC-running adapter silently shifts its window by three hours."""
        with pytest.raises(TimezoneMismatch) as raised:
            parse_instant("2026-08-11T07:24:58.000+00:00")
        assert "Africa/Djibouti" in str(raised.value)

    def test_zulu_is_refused_too(self):
        with pytest.raises(TimezoneMismatch):
            parse_instant("2026-08-11T07:24:58.000Z")

    def test_construction_validates_so_a_bad_instant_cannot_reach_a_comparison(self):
        with pytest.raises(MalformedInstant):
            ServerInstant("nonsense")
