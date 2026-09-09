"""DH-EX-001: the Exchange's own audit event shape.

A separate closed schema (not a reuse of ``daher_adapter.audit.AuditEvent``) because Exchange
events have no OpenMRS/OpenELIS ``direction`` and instead need an organization-pair shape; sharing
one ``Literal`` between two unrelated domains would either force fake OpenMRS/OpenELIS direction
values onto Exchange rows or force every future connector pair to be enumerated in a single
ever-growing Literal.

**DH-EX-001R correction:** this class is defined but not yet wired to any writer or repository
call -- no code in this ticket constructs or persists an ``ExchangeAuditEvent``. DH-EX-001
originally widened ``daher_adapter.audit.AuditWriter.append`` from ``AuditEvent`` to ``BaseModel``
so this schema could reuse that writer's fsync/flock/mode-0600 append mechanism; review reverted
that widening because it weakened the writer's existing type guarantee for a caller that does not
exist yet. When a real Exchange audit caller is built (the HTTP API ticket), either give this
schema its own narrowly-typed writer, or widen ``AuditWriter`` again at that point, when there is
an actual second caller to justify it -- not preemptively.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExchangeAuditEvent(BaseModel):
    """Deliberately narrow, matching ``daher_adapter.audit.AuditEvent``'s own discipline: no
    clinical values, no free-text results -- only identifiers and outcomes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: Literal[
        "organization_created",
        "patient_reference_bound",
        "practitioner_reference_bound",
        "lab_order_created",
        "lab_order_transitioned",
        "lab_selected",
        "lab_result_recorded",
        "result_delivered",
        "doctor_reviewed",
    ]
    lab_order_id: str | None = Field(default=None, max_length=64)
    source_organization_id: str | None = Field(default=None, max_length=64)
    target_organization_id: str | None = Field(default=None, max_length=64)
    patient_reference_id: str | None = Field(default=None, max_length=64)
    outcome: Literal["created", "returned_existing", "transitioned", "refused"]
