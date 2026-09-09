"""DH-EX-001: the DHealth Exchange core domain -- organizations, patient/practitioner
references, the lab test catalog, lab orders, lab selection, results, delivery, and doctor
review.

This package intentionally imports nothing from ``daher_adapter.openmrs``,
``daher_adapter.openelis``, ``daher_adapter.order_poller``, ``daher_adapter.watermark``,
``daher_adapter.source_events``, ``daher_adapter.cancellation``, ``daher_adapter.patient_identity``,
or ``daher_adapter.recovery`` -- those remain OpenMRS/OpenELIS connector-specific. This package is
the connector-agnostic Exchange core, per DH-PIVOT-001's architecture audit.

It shares its ORM ``Base`` with the rest of the adapter (see ``models.py``) rather than
duplicating proven infrastructure. It does not share the OpenMRS/OpenELIS message ledger
(``daher_adapter.ledger``) -- lab-order creation is a client-submitted business command, not a
replayed source event, so it defines its own request-level idempotency concept
(``exchange/orders.py``) following the same insert-first-wins pattern rather than importing it.
``exchange/audit.py``'s ``ExchangeAuditEvent`` schema is defined but not yet wired to
``daher_adapter.audit.AuditWriter`` or any caller (DH-EX-001R correction -- see that module's own
docstring); reuse of the writer is deferred until a real caller exists.
"""
