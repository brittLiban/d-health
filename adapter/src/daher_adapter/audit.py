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


class AuditWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_available(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def append(self, event: AuditEvent) -> None:
        # DH-EX-001R: DH-EX-001 briefly widened this parameter to `BaseModel` so
        # `exchange/audit.py`'s `ExchangeAuditEvent` could reuse this writer. Reverted on review:
        # `ExchangeAuditEvent` was never actually wired to any repository call in DH-EX-001 (no
        # caller exists yet), so the widening weakened this writer's type-level guarantee (any
        # `BaseModel`, not just the closed `AuditEvent` shape) for zero current benefit. Restore
        # the strict type now; revisit reuse (or a narrower shared protocol) only when a real
        # Exchange audit caller exists to justify it.
        self.ensure_available()
        encoded = (
            json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        descriptor = os.open(self.path, os.O_APPEND | os.O_WRONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise OSError("short audit-log write")
            os.fsync(descriptor)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
