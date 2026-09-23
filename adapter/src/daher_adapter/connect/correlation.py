"""DH-CONNECT-AGENT-001 §36–§37, §43 — exact correlation, and nothing resembling a guess.

Two systems name the same clinical work differently. DHealth has a ``LabOrder`` id; the laboratory
has an accession number; each report they release has its own identifier. Every message after the
first refers to the partner's names, and DHealth has to turn those back into its own.

**There is no matching here, fuzzy or otherwise.** §43 forbids fuzzy matching, and the way that is
enforced is that nothing in this module compares anything to anything. A partner identifier either
has a row -- recorded the moment the partner first asserted it -- or it does not. If it does not,
the message referencing it is refused. There is no similarity score to tune, no threshold to relax
under pressure, and no demographic field to fall back on.

**Bindings are immutable.** An external id that arrives bound to a different DHealth order is a
contradiction, not an update. Re-pointing it would move a clinical result from one patient's order
to another's, silently, and the only trace would be that the newer message won.

The partner's own identifiers are also **never** how the facility is determined. Every function here
takes the installation from the caller, which derived it from authenticated service credentials
(§8). An accession number is a claim about work, not authority over a tenant.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from daher_adapter.connect.runtime_models import ConnectorCorrelation, CorrelationKind


class CorrelationConflict(ValueError):
    """The binding asked for contradicts one already recorded.

    Three ways to get here, and all three are refusals rather than corrections:

    * this external id already names a different DHealth order;
    * this DHealth order already has a different accession at this laboratory;
    * the same report id is claimed for two different orders.

    Each is a real integration fault worth surfacing to a human. The alternative -- letting the
    newer assertion win -- is how a result reaches the wrong chart.
    """


class CorrelationUnknown(LookupError):
    """An external identifier DHealth has no binding for.

    Deliberately an error rather than ``None`` at the call sites that require a correlation. A
    laboratory publishing a result for an accession DHealth never issued is either talking about
    somebody else's order or has lost track of its own, and the honest response is to refuse the
    message and let an operator look -- not to search for the closest thing.
    """


class CorrelationRepository:
    """The only writer of ``connector_correlations``."""

    def bind_order(
        self,
        session: Session,
        *,
        installation_id: uuid.UUID,
        external_order_id: str,
        lab_order_id: uuid.UUID,
        source_system: str,
    ) -> ConnectorCorrelation:
        """Record that this laboratory calls this DHealth order by this name.

        Idempotent on the exact same binding, because the message that asserts it is delivered
        at-least-once and a laboratory may legitimately repeat its acceptance. Both *directions* of
        contradiction are checked: a reused external id, and an order that already has a different
        accession. Checking only one would leave the other to be caught by a unique index whose
        error nobody could interpret.
        """
        existing = session.scalar(
            select(ConnectorCorrelation).where(
                ConnectorCorrelation.connector_installation_id == installation_id,
                ConnectorCorrelation.correlation_kind == CorrelationKind.ORDER.value,
                ConnectorCorrelation.external_id == external_order_id,
            )
        )
        if existing is not None:
            if existing.lab_order_id != lab_order_id:
                raise CorrelationConflict(
                    f"external order {external_order_id!r} is already bound to a different DHealth "
                    "order; refusing to re-point it"
                )
            return existing

        reverse = session.scalar(
            select(ConnectorCorrelation).where(
                ConnectorCorrelation.connector_installation_id == installation_id,
                ConnectorCorrelation.correlation_kind == CorrelationKind.ORDER.value,
                ConnectorCorrelation.lab_order_id == lab_order_id,
            )
        )
        if reverse is not None:
            raise CorrelationConflict(
                f"this DHealth order already has accession {reverse.external_id!r} at this "
                "laboratory; a second one would mean their system duplicated it"
            )

        return self._insert(
            session,
            installation_id=installation_id,
            kind=CorrelationKind.ORDER,
            external_id=external_order_id,
            lab_order_id=lab_order_id,
            source_system=source_system,
        )

    def bind_report(
        self,
        session: Session,
        *,
        installation_id: uuid.UUID,
        external_report_id: str,
        lab_order_id: uuid.UUID,
        source_system: str,
    ) -> ConnectorCorrelation:
        """Record that this report identifier belongs to this DHealth order.

        Many reports per order, on purpose: the original release and every later amendment are
        separate reports about the same work. Only the reverse uniqueness is dropped -- a report id
        still names exactly one order, so an amendment claiming to correct a report from a different
        order is refused rather than applied across the boundary.
        """
        existing = session.scalar(
            select(ConnectorCorrelation).where(
                ConnectorCorrelation.connector_installation_id == installation_id,
                ConnectorCorrelation.correlation_kind == CorrelationKind.REPORT.value,
                ConnectorCorrelation.external_id == external_report_id,
            )
        )
        if existing is not None:
            if existing.lab_order_id != lab_order_id:
                raise CorrelationConflict(
                    f"report {external_report_id!r} is already bound to a different DHealth order"
                )
            return existing

        return self._insert(
            session,
            installation_id=installation_id,
            kind=CorrelationKind.REPORT,
            external_id=external_report_id,
            lab_order_id=lab_order_id,
            source_system=source_system,
        )

    def resolve_order(
        self, session: Session, *, installation_id: uuid.UUID, external_order_id: str
    ) -> uuid.UUID:
        """The DHealth order this accession names, or ``CorrelationUnknown``."""
        found = session.scalar(
            select(ConnectorCorrelation.lab_order_id).where(
                ConnectorCorrelation.connector_installation_id == installation_id,
                ConnectorCorrelation.correlation_kind == CorrelationKind.ORDER.value,
                ConnectorCorrelation.external_id == external_order_id,
            )
        )
        if found is None:
            raise CorrelationUnknown(
                f"no DHealth order is bound to external order {external_order_id!r}"
            )
        return found

    def resolve_report(
        self, session: Session, *, installation_id: uuid.UUID, external_report_id: str
    ) -> uuid.UUID:
        """The DHealth order this report names, or ``CorrelationUnknown``."""
        found = session.scalar(
            select(ConnectorCorrelation.lab_order_id).where(
                ConnectorCorrelation.connector_installation_id == installation_id,
                ConnectorCorrelation.correlation_kind == CorrelationKind.REPORT.value,
                ConnectorCorrelation.external_id == external_report_id,
            )
        )
        if found is None:
            raise CorrelationUnknown(f"no DHealth order is bound to report {external_report_id!r}")
        return found

    def external_order_id_for(
        self, session: Session, *, installation_id: uuid.UUID, lab_order_id: uuid.UUID
    ) -> str | None:
        """The accession this laboratory assigned, if it has told DHealth one yet.

        ``None`` rather than an error: DHealth queues an order notification *before* the laboratory
        has any accession for it, so "not yet" is an ordinary state rather than a fault.
        """
        return session.scalar(
            select(ConnectorCorrelation.external_id).where(
                ConnectorCorrelation.connector_installation_id == installation_id,
                ConnectorCorrelation.correlation_kind == CorrelationKind.ORDER.value,
                ConnectorCorrelation.lab_order_id == lab_order_id,
            )
        )

    def _insert(
        self,
        session: Session,
        *,
        installation_id: uuid.UUID,
        kind: CorrelationKind,
        external_id: str,
        lab_order_id: uuid.UUID,
        source_system: str,
    ) -> ConnectorCorrelation:
        row = ConnectorCorrelation(
            connector_installation_id=installation_id,
            correlation_kind=kind.value,
            external_id=external_id,
            lab_order_id=lab_order_id,
            source_system=source_system,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as error:
            # The read-then-write above is not atomic under concurrency; the unique constraints are.
            # Translating the database's refusal into the same exception the explicit checks raise
            # means a caller handles one failure mode rather than two that mean the same thing.
            raise CorrelationConflict(
                f"{kind.value.lower()} {external_id!r} was bound concurrently to a different order"
            ) from error
        return row
