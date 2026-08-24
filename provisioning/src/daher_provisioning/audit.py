"""Tamper-evident audit log for provisioning actions (ADR-021).

Three properties, in the order they matter:

1. **The API records what it did, not what the client claimed.** "The portal said it
   succeeded" is not evidence. Every entry is written by the code that performed the
   action, including failures — an action that errored is exactly the one an attacker
   would prefer went unrecorded.

2. **Tampering is detectable.** Each entry carries the hash of the previous one, so the
   file is a chain. Editing or deleting an entry breaks every hash after it, and
   ``verify_chain`` reports the first broken position.

   ⚠️ Be precise about what this buys: a hash chain makes tampering **detectable**, not
   **impossible**. An attacker with write access can rewrite the whole file and recompute
   every hash. The real requirement — a log that survives this host being rebuilt — needs
   an external append-only destination, which does not exist yet and is marked incomplete
   rather than pretended. What the chain gives today is that *silent selective edits* are
   caught, and it means the eventual external destination is a shipper rather than a
   redesign.

3. **Secrets never enter it.** Entries reference credentials by id. The event model is
   closed, and a test asserts no field is named for a secret.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Marks the start of a chain. A first entry whose prev_hash is anything else means earlier
# entries were removed.
GENESIS_HASH = "0" * 64


class AuditAction(StrEnum):
    FACILITY_CREATE = "facility.create"
    FACILITY_LIST = "facility.list"
    FACILITY_STATUS = "facility.status"
    CREDENTIALS_ROTATE = "facility.credentials.rotate"
    FACILITY_DECOMMISSION = "facility.decommission"
    AUTH_DENIED = "auth.denied"
    RATE_LIMITED = "auth.rate_limited"
    # UI-010/DH-008E. One action per direction; AuditOutcome (SUCCESS/FAILURE/DENIED)
    # carries the REQUESTED/ENABLED-or-FAILED distinction §17 asks for, the same way every
    # other mutating action here already records intent-vs-outcome through `outcome` rather
    # than a separate action per stage.
    BREAK_GLASS_ENABLE = "facility.break_glass.enable"
    BREAK_GLASS_DISABLE = "facility.break_glass.disable"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class AuditEntry(BaseModel):
    """One recorded action.

    Closed on purpose: a secret cannot be smuggled in through an unexpected key. Anything
    genuinely needed here gets a named field and a review of whether it is safe to persist.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=0)
    recorded_at: datetime
    actor: str = Field(
        max_length=128, description="Authenticated caller identity — never a credential value"
    )
    action: AuditAction
    outcome: AuditOutcome
    facility_id: str | None = Field(default=None, max_length=64)
    # Reference credentials by id. Never a value (ADR-021).
    credential_set_id: str | None = Field(default=None, max_length=64)
    message: str = Field(default="", max_length=512)
    source_ip: str | None = Field(default=None, max_length=64)
    prev_hash: str = Field(min_length=64, max_length=64)
    entry_hash: str = Field(min_length=64, max_length=64)


class AuditError(RuntimeError):
    """The audit log cannot be written or is inconsistent."""


class ChainBroken(AuditError):
    """The chain does not verify — entries were altered or removed."""

    def __init__(self, sequence: int, detail: str) -> None:
        super().__init__(f"audit chain broken at sequence {sequence}: {detail}")
        self.sequence = sequence


def _payload(entry: dict[str, Any]) -> dict[str, Any]:
    """The hashed portion: everything except the hash itself."""
    return {key: value for key, value in entry.items() if key != "entry_hash"}


def compute_hash(entry: dict[str, Any]) -> str:
    """Hash an entry deterministically.

    ``sort_keys`` and a fixed separator matter: a hash that depends on dict ordering would
    make the chain fail spuriously across Python versions and make real tampering
    indistinguishable from a serialisation change.
    """
    canonical = json.dumps(
        _payload(entry), sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only JSONL log with a hash chain."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ChainBroken(number, f"line {number} is not valid JSON: {exc}") from exc
        return entries

    def _tail(self) -> tuple[int, str]:
        entries = self._entries()
        if not entries:
            return 0, GENESIS_HASH
        last = entries[-1]
        return int(last["sequence"]) + 1, str(last["entry_hash"])

    def record(
        self,
        *,
        actor: str,
        action: AuditAction,
        outcome: AuditOutcome,
        facility_id: str | None = None,
        credential_set_id: str | None = None,
        message: str = "",
        source_ip: str | None = None,
    ) -> AuditEntry:
        """Append one entry. Called by the code that performed the action, not by a client."""
        sequence, prev_hash = self._tail()
        draft = {
            "sequence": sequence,
            "recorded_at": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": action.value,
            "outcome": outcome.value,
            "facility_id": facility_id,
            "credential_set_id": credential_set_id,
            "message": message,
            "source_ip": source_ip,
            "prev_hash": prev_hash,
        }
        draft["entry_hash"] = compute_hash(draft)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND so concurrent writers cannot interleave a partial line, and fsync so an
        # action is durable before the caller is told it happened.
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(draft, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        return AuditEntry.model_validate(draft)

    def verify_chain(self) -> int:
        """Verify every entry. Returns the number checked; raises ``ChainBroken`` on damage.

        ⚠️ Verifying zero entries is not a pass. An empty or missing log after actions have
        been performed is itself the finding, so callers must check the count rather than
        treat a clean return as proof of anything.
        """
        expected_prev = GENESIS_HASH
        expected_sequence = 0
        for entry in self._entries():
            sequence = int(entry.get("sequence", -1))
            if sequence != expected_sequence:
                raise ChainBroken(
                    sequence,
                    f"expected sequence {expected_sequence} — entries removed or reordered",
                )
            if entry.get("prev_hash") != expected_prev:
                raise ChainBroken(sequence, "prev_hash does not match the previous entry")
            recomputed = compute_hash(entry)
            if recomputed != entry.get("entry_hash"):
                raise ChainBroken(sequence, "entry content was altered after it was written")
            expected_prev = str(entry["entry_hash"])
            expected_sequence += 1
        return expected_sequence

    def entries(self) -> list[AuditEntry]:
        return [AuditEntry.model_validate(entry) for entry in self._entries()]
