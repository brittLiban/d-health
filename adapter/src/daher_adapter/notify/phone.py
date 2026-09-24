"""DH-NOTIFY-001 §6 — destination normalization. The server owns what a phone number is.

``phonenumbers`` (Google's libphonenumber port) rather than string manipulation, because the rules
this has to get right are not rules anybody derives correctly from examples: which prefixes are
assignable in which country, how many digits a Djiboutian mobile has, that a leading national trunk
zero is dropped rather than kept, that ``+253 77 12 34 56`` and ``+25377123456`` are one number.
A hand-written normalizer passes its author's test cases and then routes a real patient's result
notification to a number that does not exist.

**No silent country guessing** (§6). The default region is ``None`` unless a deployment explicitly
configures one, so a number without a country code is *refused* rather than assumed to be local.
Guessing from the server's timezone -- the obvious shortcut, and the one §6 names -- would mean the
same typed digits resolve to different real people depending on where the container runs.

**The configuration seam exists and is empty.** ``default_region`` is a parameter threaded from
``DHEALTH_NOTIFICATION_DEFAULT_REGION``, unset by default. Djibouti is not hard-coded here: this
module is the Exchange's, and the Exchange is not a Djiboutian product by construction even though
its first pilot is. A facility-level default, if one is ever wanted, replaces the value passed in
rather than editing this file.

**Failure is before the provider, always** (§6). Every write path validates here, so an invalid
destination never reaches an attempt row, never consumes a retry budget, and never produces a
provider error whose body would have to be sanitized.
"""

from __future__ import annotations

import phonenumbers


class InvalidDestination(ValueError):
    """The destination is not a number this deployment can send to.

    One exception for every rejection reason -- unparseable, no country code, not a valid number,
    not a line type that can receive messages -- and the message never echoes more of the input
    than the caller already typed. The API layer turns this into a 422 whose detail is the reason
    class, not the number.
    """


def normalize(raw: str, *, default_region: str | None = None) -> str:
    """Return the E.164 form of ``raw``, or raise ``InvalidDestination``.

    ``default_region`` is an ISO 3166-1 alpha-2 code, or ``None`` to require that the caller's input
    already carries a country code. ``None`` is the default and the configured default in this
    repository: see the module docstring on why guessing is refused.
    """
    candidate = (raw or "").strip()
    if not candidate:
        raise InvalidDestination("a destination phone number is required")
    # A bound before the parser, not after. libphonenumber is happy to chew on a megabyte of
    # attacker-supplied text; a real E.164 number is at most 16 characters, and a generously
    # formatted human entry ("+253 77 12 34 56") is well under this.
    if len(candidate) > 32:
        raise InvalidDestination("destination phone number is too long")

    if default_region is None and not candidate.startswith("+"):
        raise InvalidDestination(
            "destination phone number must include a country code (for example +253...)"
        )

    try:
        parsed = phonenumbers.parse(candidate, default_region)
    except phonenumbers.NumberParseException as error:
        raise InvalidDestination("destination phone number could not be read") from error

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidDestination("destination phone number is not a valid number")

    normalized = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    # The database CHECK on this column is the portable shape guard; this is the same assertion
    # made where the good error message is. They agree by construction: libphonenumber's E164
    # formatter cannot emit anything outside this shape, so reaching here means something upstream
    # changed and it is better to refuse than to write a row the database will reject anyway.
    if not _looks_like_e164(normalized):  # pragma: no cover - unreachable via libphonenumber
        raise InvalidDestination("destination phone number is not a valid number")
    return normalized


def _looks_like_e164(value: str) -> bool:
    return (
        value.startswith("+")
        and 8 <= len(value) <= 16
        and value[1] in "123456789"
        and value[1:].isdigit()
    )
