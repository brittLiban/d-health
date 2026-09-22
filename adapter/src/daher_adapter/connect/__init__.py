"""DHealth Connect — the contract an external EMR/LIS integration must satisfy.

This package is deliberately **not** part of ``exchange``. The Exchange is DHealth's canonical
domain; Connect is the boundary a partner's software meets it at. Keeping them apart is what stops
FHIR resources, HL7 segments and vendor field names from leaking into the model DHealth actually
owns:

    External EMR/LIS  --vendor-specific-->  DHealth Connect  --canonical-->  DHealth Exchange

Nothing here speaks a wire protocol. There is no FHIR client, no HL7 parser, no MLLP, no SFTP, no
file watcher, no webhook receiver, no polling loop, no credential store and no background worker —
those are DH-CONNECT-AGENT-001's subject and none of them exists yet. What exists here is the
meaning every future connector must implement, plus the qualification model that decides whether a
particular build actually did.

Read ``docs/CONNECT-CONTRACT.md`` before implementing a connector, and
``docs/CONNECT-QUALIFICATION.md`` before trusting a qualification result.
"""

from .contract import (
    CONTRACT_VERSION,
    BusinessAcceptance,
    BusinessRejection,
    Capability,
    CollectionEvent,
    ConnectMessage,
    Envelope,
    FailureCategory,
    HealthState,
    MessageType,
    OrderSubmission,
    ReceiptStore,
    ReleasedAmendment,
    ReleasedResult,
    ResultObservation,
    ScopedExternalId,
    SourcePractitionerRef,
    TechnicalAck,
    TestMapping,
    contract_major,
    fingerprint,
    mapping_fingerprint,
    required_capabilities,
)

__all__ = [
    "CONTRACT_VERSION",
    "BusinessAcceptance",
    "BusinessRejection",
    "Capability",
    "CollectionEvent",
    "ConnectMessage",
    "Envelope",
    "FailureCategory",
    "HealthState",
    "MessageType",
    "OrderSubmission",
    "ReceiptStore",
    "ReleasedAmendment",
    "ReleasedResult",
    "ResultObservation",
    "ScopedExternalId",
    "SourcePractitionerRef",
    "TechnicalAck",
    "TestMapping",
    "contract_major",
    "fingerprint",
    "mapping_fingerprint",
    "required_capabilities",
]
