"""DH-EX-002R: the authoritative, transactional audit record for Exchange security mutations.

`ExchangeSecurityAuditRepository.record` never opens or commits its own transaction -- it only
`session.add()`s a row onto a `Session` the caller already owns and is going to commit. This is
deliberate: the whole point of this table is that its row and the mutation it describes commit or
roll back together, in one database transaction, never as two separate writes with a gap between
them. `exchange/security_audit.py`'s only caller is `exchange/api.py`'s mutation handlers, which
own that transaction boundary explicitly (see `run_in_transaction` there).

This is a different mechanism from `exchange/audit.py`'s `ExchangeAuditWriter`, on purpose: that
file-based writer remains for authorization *refusals*, which are not state mutations and must
never make access denial depend on a database write succeeding. This table is for mutations only.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy.orm import Session

from daher_adapter.exchange.models import ExchangeSecurityAuditEvent

SecurityEventType = Literal["organization_created", "membership_created", "role_assigned"]
SecurityOutcome = Literal["created", "returned_existing"]


class ExchangeSecurityAuditRepository:
    def record(
        self,
        session: Session,
        *,
        event_type: SecurityEventType,
        actor_identity_id: uuid.UUID,
        organization_id: uuid.UUID,
        outcome: SecurityOutcome,
        subject_identity_id: uuid.UUID | None = None,
        membership_id: uuid.UUID | None = None,
        role: str | None = None,
    ) -> None:
        session.add(
            ExchangeSecurityAuditEvent(
                event_type=event_type,
                actor_identity_id=actor_identity_id,
                organization_id=organization_id,
                subject_identity_id=subject_identity_id,
                membership_id=membership_id,
                role=role,
                outcome=outcome,
            )
        )
