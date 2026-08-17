"""Server-issued FHIR instants, kept byte-exact.

`docs/WATERMARK-DESIGN.md` §7 is the binding constraint here: the adapter **never parses,
normalises, or re-formats** `meta.lastUpdated`. It stores the string the server sent and sends
that same string back. Every window bound therefore comes from a value the server itself
emitted, which is what makes the comparison self-consistent across two machines' clocks.

That leaves one problem this module exists to solve. §3's correction records that OpenMRS
`fhir2` 4.2.0 accepts `_sort` and **silently ignores it**, so the adapter must order the cohort
itself — and ordering means comparing. Comparing without parsing is only sound if the comparison
is exact, so:

* the shape is **validated** rather than assumed, and anything unexpected is refused rather
  than mis-ordered silently;
* the offset is checked against Djibouti's fixed `+03:00`, which is §7's timezone assertion
  expressed where it can actually fail loudly (Djibouti has no daylight saving, so the offset
  is a constant year-round — do not "fix" this by adding DST handling);
* ordering uses a key derived from the string with the fractional part zero-padded **for
  comparison only**. Plain lexicographic comparison would rank `.9` above `.10`, which is
  wrong; padding fixes that without ever changing the bytes we transmit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# Djibouti is UTC+3 with no daylight saving, so this is a constant rather than a lookup.
# Every component in the stack runs TZ=Africa/Djibouti and `config.Settings` refuses to start
# otherwise; this is the same assertion applied to what the server actually sends us.
EXPECTED_UTC_OFFSET: Final = "+03:00"

# 2026-08-11T10:24:58.000+03:00 — the shape observed on this server, with the fraction optional
# because "absent" and "three digits" are both legal FHIR and both must be handled exactly.
_INSTANT = re.compile(
    r"^(?P<seconds>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?"
    r"(?P<offset>[+-]\d{2}:\d{2}|Z)$"
)

_FRACTION_WIDTH: Final = 9


class MalformedInstant(ValueError):
    """A timestamp the adapter refuses to order against.

    Deliberately fatal to the current poll cycle rather than skipped. A timestamp we cannot
    order is a timestamp we cannot prove we processed in sequence, and skipping it is the
    silent-loss direction `WATERMARK-DESIGN.md` refuses.
    """


class TimezoneMismatch(MalformedInstant):
    """The server's offset is not Djibouti's.

    §7: an adapter running on a different offset silently shifts its window by hours, which
    loses lab results without any error. This is the assertion that turns that into a refusal.
    """


@dataclass(frozen=True, slots=True, order=False)
class ServerInstant:
    """A `meta.lastUpdated` value exactly as the server emitted it.

    `raw` is what goes back on the wire, always. `sort_key` exists only for local ordering
    and must never be transmitted or persisted as the watermark.
    """

    raw: str

    def __post_init__(self) -> None:
        # Validation happens at construction so an invalid instant cannot reach a comparison,
        # a watermark, or a query string anywhere in the poller.
        _parse(self.raw)

    @property
    def sort_key(self) -> tuple[str, str]:
        seconds, fraction = _parse(self.raw)
        return (seconds, fraction)

    def __lt__(self, other: ServerInstant) -> bool:
        return self.sort_key < other.sort_key

    def __le__(self, other: ServerInstant) -> bool:
        return self.sort_key <= other.sort_key

    def __gt__(self, other: ServerInstant) -> bool:
        return self.sort_key > other.sort_key

    def __ge__(self, other: ServerInstant) -> bool:
        return self.sort_key >= other.sort_key


def _parse(raw: object) -> tuple[str, str]:
    if not isinstance(raw, str) or not raw:
        raise MalformedInstant(f"instant must be a non-empty string, got {type(raw).__name__}")
    match = _INSTANT.fullmatch(raw)
    if match is None:
        raise MalformedInstant(
            f"instant {raw!r} does not match the shape this server emits. Refusing rather "
            f"than guessing an order for it."
        )
    offset = match.group("offset")
    if offset != EXPECTED_UTC_OFFSET:
        raise TimezoneMismatch(
            f"instant {raw!r} carries offset {offset!r}, not Djibouti's {EXPECTED_UTC_OFFSET!r}. "
            f"Every component must run TZ=Africa/Djibouti; a mismatched window silently drops "
            f"records instead of failing (WATERMARK-DESIGN.md §7)."
        )
    fraction = (match.group("fraction") or "").ljust(_FRACTION_WIDTH, "0")
    return (match.group("seconds"), fraction)


def parse_instant(raw: object) -> ServerInstant:
    """Build a `ServerInstant`, raising `MalformedInstant` with a reason if it is unusable."""
    _parse(raw)
    assert isinstance(raw, str)  # noqa: S101 - narrowed by _parse above
    return ServerInstant(raw)


def highest(left: ServerInstant, right: ServerInstant) -> ServerInstant:
    """The later of two instants, preserving the original string of whichever wins."""
    return right if right > left else left
