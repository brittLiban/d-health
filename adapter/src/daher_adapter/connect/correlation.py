"""DH-CONNECT-AGENT-001 §36–§37, §43 — exact correlation, and nothing resembling a guess.

Two systems name the same clinical work differently. DHealth has a ``LabOrder`` id; the laboratory
has an accession number; the provider's EMR has the order id it submitted under. Every message after
the first refers to the partner's names, and DHealth has to turn those back into its own.

**There is no matching here, fuzzy or otherwise.** §43 forbids fuzzy matching, and the way that is
enforced is that nothing in this module compares anything to anything. A partner identifier either
has a row -- recorded the moment the partner first asserted it -- or it does not. If it does not,
the message referencing it is refused. There is no similarity score to tune, no threshold to relax
under pressure, and no demographic field to fall back on.

**Bindings are immutable.** An external id that arrives bound to a different DHealth order is a
contradiction, not an update. Re-pointing it would move a clinical result from one patient's order
to another's, silently, and the only trace would be that the newer message won.

**The scope is the namespace, not the installation** (0031, corrected). Every lookup here is keyed
on ``(organization_id, source_system, correlation_kind, external_id)``. The first version keyed on
the connector installation, which conflated two lifetimes that are not the same:

    a connector installation   one DHealth adapter build and configuration, retired and replaced
                               when a version or a fingerprint moves

    an external identifier     a name in the partner's *system*, which outlives any number of
                               adapter upgrades on DHealth's side

A clinic's ``ORDER-123`` did not stop meaning the same thing because DHealth replaced the connector
that first told us about it. Under the old key it became unresolvable -- so a result released after
a routine connector upgrade could not be filed, through no act of the partner's.

The installation is still recorded, as ``created_by_connector_installation_id``: provenance worth
keeping, and part of no key. What carries across a replacement is the *meaning of the identifier*.
What does not carry is the new installation's **qualification**, which it earns for itself. Those
are separate facts and this module deliberately knows about only the first.

The organization is **never** derived from the identifier. Every function takes it from the caller,
which got it from the installation the credential resolved to (§8). An external order number is a
claim about work, not authority over a tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from daher_adapter.connect.runtime_models import ConnectorCorrelation, CorrelationKind


class CorrelationConflict(ValueError):
    """The binding asked for contradicts one already recorded.

    Three ways to get here, and all three are refusals rather than corrections:

    * this external id already names a different DHealth order;
    * this DHealth order already has a different external id in this namespace;
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


@dataclass(frozen=True, slots=True)
class CorrelationNamespace:
    """Where an external identifier lives: one facility's one source system.

    A value rather than two loose arguments, because these two always travel together and a call
    site that passed the organization of one facility with the source system of another would be
    doing something meaningless that nothing else would catch.
    """

    organization_id: uuid.UUID
    source_system: str


@dataclass(frozen=True, slots=True)
class OrderCorrelation:
    """A resolved ORDER binding, with the namespace it was found in.

    The namespace comes back with it because the caller's next question is almost always "so which
    connector can carry this?" -- and that is answered from the source system, never from whichever
    installation happens to be registered now.
    """

    lab_order_id: uuid.UUID
    external_order_id: str
    namespace: CorrelationNamespace
    created_by_connector_installation_id: uuid.UUID


class CorrelationRepository:
    """The only writer of ``connector_correlations``."""

    def bind_order(
        self,
        session: Session,
        *,
        namespace: CorrelationNamespace,
        external_order_id: str,
        lab_order_id: uuid.UUID,
        created_by_installation_id: uuid.UUID,
    ) -> ConnectorCorrelation:
        """Record that this source system calls this DHealth order by this name.

        Idempotent on the exact same binding, because the message that asserts it is delivered
        at-least-once and a partner may legitimately repeat it. Both *directions* of contradiction
        are checked: a reused external id, and an order that already has a different external id in
        this namespace. Checking only one would leave the other to be caught by a unique index whose
        error nobody could interpret.

        The reverse check is scoped to the namespace and not to the order alone, which matters: one
        DHealth order genuinely has **two** ORDER correlations when both ends are CONNECT -- the
        provider's EMR knows it by the id it submitted, the laboratory's LIS by the accession it
        assigned. Those are different namespaces, and a check that looked only at ``lab_order_id``
        would call the second one a duplicate of the first.
        """
        existing = self._find(
            session, namespace, CorrelationKind.ORDER, external_id=external_order_id
        )
        if existing is not None:
            if existing.lab_order_id != lab_order_id:
                raise CorrelationConflict(
                    f"external order {external_order_id!r} is already bound to a different DHealth "
                    "order in this source system; refusing to re-point it"
                )
            return existing

        reverse = session.scalar(
            select(ConnectorCorrelation).where(
                ConnectorCorrelation.organization_id == namespace.organization_id,
                ConnectorCorrelation.source_system == namespace.source_system,
                ConnectorCorrelation.correlation_kind == CorrelationKind.ORDER.value,
                ConnectorCorrelation.lab_order_id == lab_order_id,
            )
        )
        if reverse is not None:
            raise CorrelationConflict(
                f"this DHealth order is already known as {reverse.external_id!r} in this source "
                "system; a second one would mean their system duplicated it"
            )

        return self._insert(
            session,
            namespace=namespace,
            kind=CorrelationKind.ORDER,
            external_id=external_order_id,
            lab_order_id=lab_order_id,
            created_by_installation_id=created_by_installation_id,
        )

    def bind_report(
        self,
        session: Session,
        *,
        namespace: CorrelationNamespace,
        external_report_id: str,
        lab_order_id: uuid.UUID,
        created_by_installation_id: uuid.UUID,
    ) -> ConnectorCorrelation:
        """Record that this report identifier belongs to this DHealth order.

        Many reports per order, on purpose: the original release and every later amendment are
        separate reports about the same work. Only the reverse uniqueness is dropped -- a report id
        still names exactly one order, so an amendment claiming to correct a report from a different
        order is refused rather than applied across the boundary.
        """
        existing = self._find(
            session, namespace, CorrelationKind.REPORT, external_id=external_report_id
        )
        if existing is not None:
            if existing.lab_order_id != lab_order_id:
                raise CorrelationConflict(
                    f"report {external_report_id!r} is already bound to a different DHealth order"
                )
            return existing

        return self._insert(
            session,
            namespace=namespace,
            kind=CorrelationKind.REPORT,
            external_id=external_report_id,
            lab_order_id=lab_order_id,
            created_by_installation_id=created_by_installation_id,
        )

    def resolve_order(
        self, session: Session, *, namespace: CorrelationNamespace, external_order_id: str
    ) -> uuid.UUID:
        """The DHealth order this external id names, or ``CorrelationUnknown``."""
        found = self._find(session, namespace, CorrelationKind.ORDER, external_id=external_order_id)
        if found is None:
            raise CorrelationUnknown(
                f"no DHealth order is bound to external order {external_order_id!r} in this "
                "source system"
            )
        return found.lab_order_id

    def resolve_report(
        self, session: Session, *, namespace: CorrelationNamespace, external_report_id: str
    ) -> uuid.UUID:
        """The DHealth order this report names, or ``CorrelationUnknown``."""
        found = self._find(
            session, namespace, CorrelationKind.REPORT, external_id=external_report_id
        )
        if found is None:
            raise CorrelationUnknown(f"no DHealth order is bound to report {external_report_id!r}")
        return found.lab_order_id

    def order_correlation_for(
        self, session: Session, *, organization_id: uuid.UUID, lab_order_id: uuid.UUID
    ) -> OrderCorrelation | None:
        """This organization's own name for this DHealth order, and the namespace it lives in.

        The reverse lookup the result hook needs, and it is keyed on the **organization** rather
        than on any installation: the connector that first bound the identifier may be retired, and
        that must not make the provider's own order id unresolvable.

        Returns ``None`` when this organization never named this order, which is the ordinary state
        for an order placed natively inside DHealth. More than one row is a contradiction rather
        than a choice -- one facility should not have two source systems both claiming to have
        ordered the same work -- so it raises instead of picking, and the caller records an
        unresolved obligation rather than guessing which system to notify.
        """
        rows = session.scalars(
            select(ConnectorCorrelation).where(
                ConnectorCorrelation.organization_id == organization_id,
                ConnectorCorrelation.correlation_kind == CorrelationKind.ORDER.value,
                ConnectorCorrelation.lab_order_id == lab_order_id,
            )
        ).all()
        if not rows:
            return None
        if len(rows) > 1:
            raise CorrelationConflict(
                f"DHealth order {lab_order_id} is claimed by {len(rows)} source systems at one "
                "organization; refusing to choose which one asked for it"
            )
        row = rows[0]
        return OrderCorrelation(
            lab_order_id=row.lab_order_id,
            external_order_id=row.external_id,
            namespace=CorrelationNamespace(
                organization_id=row.organization_id, source_system=row.source_system
            ),
            created_by_connector_installation_id=row.created_by_connector_installation_id,
        )

    def _find(
        self,
        session: Session,
        namespace: CorrelationNamespace,
        kind: CorrelationKind,
        *,
        external_id: str,
    ) -> ConnectorCorrelation | None:
        return session.scalar(
            select(ConnectorCorrelation).where(
                ConnectorCorrelation.organization_id == namespace.organization_id,
                ConnectorCorrelation.source_system == namespace.source_system,
                ConnectorCorrelation.correlation_kind == kind.value,
                ConnectorCorrelation.external_id == external_id,
            )
        )

    def _insert(
        self,
        session: Session,
        *,
        namespace: CorrelationNamespace,
        kind: CorrelationKind,
        external_id: str,
        lab_order_id: uuid.UUID,
        created_by_installation_id: uuid.UUID,
    ) -> ConnectorCorrelation:
        row = ConnectorCorrelation(
            organization_id=namespace.organization_id,
            source_system=namespace.source_system,
            created_by_connector_installation_id=created_by_installation_id,
            correlation_kind=kind.value,
            external_id=external_id,
            lab_order_id=lab_order_id,
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
