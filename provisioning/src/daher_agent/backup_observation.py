"""Parse the nightly scheduler's own evidence file (DH-027H).

`backup/backup.sh` writes `${BACKUP_OUTPUT_DIR:-/backup}/last-run.status` as exactly one line:
``<STATE> <TIMESTAMP>``, e.g. ``SUCCESS 20260821T020000Z``. This is the *only* channel through
which the control plane learns whether last night's autonomous, in-container backup actually
succeeded — DH-027G wired on-demand `BACKUP_NOW`, but the nightly `scheduler.sh` loop runs
independent of the job queue entirely, so its result was previously invisible to the fleet.

Pure function on purpose, exactly like `infrastructure.py`: the rules are decided here, from
text, so they can be tested exhaustively without a Docker daemon or a real backup container.
The agent supplies the text; this module never runs a command or opens a file.

**The rule this module exists to enforce:** a file existing, or a command that read it exiting
0, is not evidence of anything. Only a well-formed `SUCCESS`/`FAILED` line is. Anything else —
absent, empty, malformed, an unrecognised state word, a garbled or missing timestamp, a
timestamp claiming to be from the future — must never be read as `SUCCESS`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from daher_provisioning.models import BackupReport, BackupState

# How the timestamp in `last-run.status` is written by `backup.sh`'s `TS="$(date -u
# +%Y%m%dT%H%M%SZ)"`, captured once at the start of that script — so this is the backup RUN's
# start time, not whenever an agent happens to read the file. `BackupReport.checked_at` is
# populated from this, not from `datetime.now()`, deliberately: the operator needs to know
# when the backup actually ran, not when it was last observed.
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

_STATE_WORDS = {"SUCCESS": BackupState.SUCCESS, "FAILED": BackupState.FAILED}

# Same tolerance `JobValidator` already uses for a job claiming to be issued in the future
# (validation.py) — reused rather than inventing a second clock-skew constant.
_FUTURE_TOLERANCE = timedelta(seconds=60)


class BackupStatusError(ValueError):
    """`last-run.status` could not be trusted. Always becomes a fail-closed report, never
    `SUCCESS` — see `parse_last_run_status`."""


def parse_last_run_status(text: str, *, observed_at: datetime | None = None) -> BackupReport:
    """Parse one `last-run.status` read. Raises `BackupStatusError` on anything untrusted.

    Strict on purpose: this file is a tiny, schema-like marker, not a message channel. Extra
    lines, extra fields, an unrecognised state word, or a timestamp that fails to parse are
    all refused rather than best-effort accepted — accepting "close enough" here is exactly
    how a malformed marker would end up read as a working backup.
    """
    now = observed_at or datetime.now(UTC)

    stripped = text.strip("\n")
    if not stripped:
        raise BackupStatusError("last-run.status is empty")

    lines = stripped.split("\n")
    if len(lines) != 1:
        raise BackupStatusError("last-run.status must contain exactly one line")

    fields = lines[0].split(" ")
    if len(fields) != 2:
        raise BackupStatusError("last-run.status is not in the expected '<STATE> <TIMESTAMP>' form")

    state_word, timestamp_text = fields
    if state_word not in _STATE_WORDS:
        raise BackupStatusError(f"last-run.status has an unrecognised state {state_word!r}")

    try:
        run_at = datetime.strptime(timestamp_text, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError as error:
        raise BackupStatusError("last-run.status has an unparseable timestamp") from error

    if run_at - now > _FUTURE_TOLERANCE:
        raise BackupStatusError("last-run.status timestamp is in the future beyond clock tolerance")

    return BackupReport(state=_STATE_WORDS[state_word], checked_at=run_at)
