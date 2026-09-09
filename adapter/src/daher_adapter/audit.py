from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AuditEvent(BaseModel):
    """A deliberately narrow audit shape; FHIR payloads and clinical values do not belong here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    direction: Literal["openmrs_to_openelis", "openelis_to_openmrs"]
    event_type: str = Field(min_length=1, max_length=64)
    source_system: str = Field(min_length=1, max_length=32)
    source_record_id: str = Field(min_length=1, max_length=256)
    target_system: str | None = Field(default=None, max_length=32)
    target_record_id: str | None = Field(default=None, max_length=256)
    patient_reference: str | None = Field(default=None, max_length=256)
    outcome: Literal["processed", "duplicate", "dead_letter"]
    attempts: int = Field(default=1, ge=1)


def ensure_available(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def append_jsonl_locked(path: Path, event: BaseModel) -> None:
    """The shared low-level append primitive: append-only, mode 0600, `flock`-serialized, one
    `fsync`-durable line per call.

    DH-EX-001R found no real caller for a `BaseModel`-typed writer; DH-EX-002 is that real
    caller (`exchange/audit.py`'s `ExchangeAuditWriter`), but reused *this* narrow function
    rather than widening `AuditWriter.append`'s own parameter type again -- `AuditWriter` and
    `ExchangeAuditWriter` stay independently, strictly typed to their own closed schema
    (`AuditEvent`/`ExchangeAuditEvent`); only this file-writing mechanism is shared.
    """
    ensure_available(path)
    encoded = (
        json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_WRONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise OSError("short audit-log write")
        os.fsync(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class AuditWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_available(self) -> None:
        ensure_available(self.path)

    def append(self, event: AuditEvent) -> None:
        append_jsonl_locked(self.path, event)
