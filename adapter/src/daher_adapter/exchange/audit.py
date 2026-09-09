"""DH-EX-001/DH-EX-002: the Exchange's own audit event shape and writer.

A separate closed schema (not a reuse of ``daher_adapter.audit.AuditEvent``) because Exchange
events have no OpenMRS/OpenELIS ``direction`` and instead need an organization/identity shape;
sharing one ``Literal`` between two unrelated domains would either force fake OpenMRS/OpenELIS
direction values onto Exchange rows or force every future connector pair to be enumerated in a
single ever-growing Literal.

**DH-EX-002 is the first real caller.** DH-EX-001 defined this schema unwired; DH-EX-001R reverted
a premature widening of ``daher_adapter.audit.AuditWriter`` to accept it, on the grounds that no
caller existed yet to justify weakening that writer's type guarantee. DH-EX-002 introduces actual
security-sensitive mutations (organization/identity/membership/role) and is that real caller --
``ExchangeAuditWriter`` below is strictly typed to ``ExchangeAuditEvent`` alone (never
``BaseModel``), reusing only the shared low-level file-append primitive
(``daher_adapter.audit.append_jsonl_locked``), never ``AuditWriter`` itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from daher_adapter.audit import append_jsonl_locked, ensure_available


class ExchangeAuditEvent(BaseModel):
    """Deliberately narrow, matching ``daher_adapter.audit.AuditEvent``'s own discipline: no
    clinical values, no free-text results, and -- DH-EX-002's own explicit instruction -- never a
    bearer token, password, or other credential material. Every field is optional except
    ``timestamp``/``event_type``/``outcome`` because the DH-EX-001 order-flow events and the
    DH-EX-002 organization/identity events need different subsets of the same shared shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: Literal[
        # DH-EX-001 order-flow events (defined, still unwired -- no HTTP order API exists yet).
        "organization_created",
        "patient_reference_bound",
        "practitioner_reference_bound",
        "lab_order_created",
        "lab_order_transitioned",
        "lab_selected",
        "lab_result_recorded",
        "result_delivered",
        "doctor_reviewed",
        # DH-EX-002 organization/identity/authorization events. NOTE (DH-EX-002R): as of the
        # transactional `ExchangeSecurityAuditEvent` DB table (`security_audit.py`),
        # "organization_created"/"membership_created"/"role_assigned" are no longer written here
        # for real mutations -- that table is now the sole authoritative record for those three.
        # The values are kept in this Literal for backward compatibility with the DH-EX-001
        # order-flow meaning of "organization_created" above and in case a future caller mirrors
        # events here; `exchange/api.py` itself no longer calls `append` with any of the three.
        "identity_first_seen",
        "membership_created",
        "membership_inactivated",
        "role_assigned",
        "role_removed",
        "authorization_refused",
        # DH-EX-002R: a refusal, not a mutation -- best-effort telemetry only, deliberately never
        # inside the same transaction as the membership lookup that produced it.
        "membership_reactivation_refused",
    ]
    lab_order_id: str | None = Field(default=None, max_length=64)
    source_organization_id: str | None = Field(default=None, max_length=64)
    target_organization_id: str | None = Field(default=None, max_length=64)
    patient_reference_id: str | None = Field(default=None, max_length=64)
    # DH-EX-002 fields. `actor_identity_id` is who performed the mutation (the authenticated
    # caller); `subject_identity_id` is who the event is *about* (may be the same identity, e.g.
    # `identity_first_seen`, or a different one, e.g. an ORG_ADMIN creating someone else's
    # membership). Never a token, password hash, or other credential.
    actor_identity_id: str | None = Field(default=None, max_length=64)
    subject_identity_id: str | None = Field(default=None, max_length=64)
    organization_id: str | None = Field(default=None, max_length=64)
    role: str | None = Field(default=None, max_length=16)
    outcome: Literal["created", "returned_existing", "transitioned", "refused"]


class ExchangeAuditWriter:
    """Strictly typed to `ExchangeAuditEvent` alone -- never widened to accept `AuditEvent` or any
    other `BaseModel`, per DH-EX-002's explicit instruction not to repeat DH-EX-001's reverted
    widening of the connector `AuditWriter`. Shares only the low-level file-append mechanism."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_available(self) -> None:
        ensure_available(self.path)

    def append(self, event: ExchangeAuditEvent) -> None:
        append_jsonl_locked(self.path, event)
