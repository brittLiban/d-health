"""DH-CONNECT-CONTRACT-001 — the DHealth Connect contract, version 1.1.

Protocol-neutral by construction. Every type here describes *meaning*, never a wire format: a
connector that speaks FHIR, one that speaks HL7 v2, and one that reads a nightly SFTP drop all
produce the same envelopes, because what DHealth needs to know is the same in all three cases.

The distinctions this module exists to make structurally hard to confuse:

    sent                  != received            != accepted
    technical ack         != business acceptance
    source event time     != DHealth receive time
    result entered        != entry complete      != professionally released
    released original     != later amendment
    qualified             != operational
    collection occurred   != collection recorded
    inbound               != outbound          (contract 1.1)

Each of those is a separate type or a separate field, not a boolean somebody remembers to set
correctly. ``TechnicalAck`` and ``BusinessAcceptance`` are two classes rather than one class with
a flag precisely because a connector author who conflates them should have to write code that
looks wrong.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Contract version ─────────────────────────────────────────────────────────────────────────

#: The DHealth Connect contract this build of DHealth implements.
#:
#: Same major version: a connector qualified against it may continue to be trusted across
#: backward-compatible evolution. **New major version: every connector must be requalified** --
#: there is no automatic carry-forward, and nothing in this codebase infers compatibility. See
#: ``docs/CONNECT-CONTRACT.md`` for what a major change is allowed to be.
#:
#: **1.0 -> 1.1 (DH-CONNECT-AGENT-001).** Additive only: one new message type,
#: ``ORDER_NOTIFICATION``, in the DHealth-to-connector direction, which 1.0 had no way to express
#: at all. No existing message type changed a field, a name, a meaning or a validation rule, so a
#: connector written against 1.0 remains correct -- it simply cannot receive orders, which is
#: exactly what was true of it before. The major component is therefore unchanged, and no
#: qualification is invalidated by the version bump itself. (The qualification *suite* version is a
#: separate number and does move; see ``qualification.QUALIFICATION_SUITE_VERSION``.)
CONTRACT_VERSION = "1.1"


def contract_major(version: str) -> int:
    """The major component, for the one comparison that is allowed to be made.

    Deliberately strict: a version string this cannot parse raises rather than defaulting to a
    number that would silently make an unknown contract look compatible with a known one.
    """
    major, _, rest = version.partition(".")
    if not major.isdigit() or not rest:
        raise ValueError(f"not a DHealth Connect contract version: {version!r}")
    return int(major)


# ── Capabilities ─────────────────────────────────────────────────────────────────────────────


class Capability(StrEnum):
    """What a connector can actually do, one fact at a time.

    Not ``connected=true``. A laboratory connector that receives orders and cannot publish a
    released result is not two thirds of a working integration -- it is an integration that takes
    clinical work and never returns it, which is worse than no integration at all, because a
    clinician waits. Naming each ability separately is what lets qualification refuse the bundle
    rather than the individual test.
    """

    # -- laboratory side ---------------------------------------------------------------------
    #: Receive a DHealth order into the laboratory's own system, with correlation preserved.
    ORDER_RECEIPT = "ORDER_RECEIPT"
    #: Answer whether the laboratory will actually do the work. Not a transport acknowledgement.
    BUSINESS_ACCEPTANCE = "BUSINESS_ACCEPTANCE"
    #: Publish a result the source workflow has professionally released, and only such a result.
    RELEASED_RESULT_PUBLICATION = "RELEASED_RESULT_PUBLICATION"
    #: Publish a post-release correction as a later version that preserves the original.
    AMENDMENT_PUBLICATION = "AMENDMENT_PUBLICATION"
    #: Report a verified specimen-collection event. REQUIRED for a laboratory -- see
    #: `required_capabilities` for why that answer changed during implementation.
    COLLECTION_EVENT = "COLLECTION_EVENT"
    #: Report that processing started. Optional.
    PROCESSING_EVENT = "PROCESSING_EVENT"

    # -- provider side -----------------------------------------------------------------------
    #: Submit a diagnostic order from the provider's own system into DHealth.
    ORDER_SUBMISSION = "ORDER_SUBMISSION"
    #: Receive an original professionally released result back into the provider's system.
    RELEASED_RESULT_RECEIPT = "RELEASED_RESULT_RECEIPT"
    #: Receive a later amendment as a distinct version, without overwriting the original.
    AMENDMENT_RECEIPT = "AMENDMENT_RECEIPT"
    #: Report a clinician's review/follow-up decision. Optional.
    REVIEW_REPORTING = "REVIEW_REPORTING"


#: The minimum bundle a connector must pass before its facility may be considered qualified.
#:
#: The required members are the ones whose absence breaks the round trip. A laboratory that
#: receives orders and cannot return released results strands work; a provider that submits orders
#: and cannot receive results never learns the answer. Amendments are required on both sides
#: because a correction that cannot reach the clinician who acted on the original is the one
#: failure mode with direct patient consequences.
#:
#: **``COLLECTION_EVENT`` is required for a laboratory, and this was not the first answer.** The
#: reasoning started at "optional": DHealth's order status machine deliberately has no collection
#: state, so requiring the event looked like demanding a milestone nothing downstream consumes.
#: Building the qualification harness disproved that. ``LabWorkflowRepository`` refuses
#: ``ACCEPTED -> IN_PROGRESS`` unless a collection milestone has actually been recorded (DH-EX-011,
#: an invariant deliberately enforced on both paths that can reach ``IN_PROGRESS``), and result
#: entry refuses anything but ``IN_PROGRESS``. A laboratory connector that cannot report collection
#: therefore cannot get an order into a state where a result can be entered *at all* -- it is not
#: missing a nicety, it is structurally unable to complete the journey.
#:
#: The consequence is deliberate and worth stating plainly: a partner LIS with no reliable
#: specimen-collection signal cannot have a qualified laboratory connector. DHealth will not infer
#: the event from an accession or a timestamp (§23), so the honest outcome is that the integration
#: is not production-safe -- discovered during qualification, which is exactly where a constraint
#: like this should surface rather than in production with a stuck order.
#:
#: ``REVIEW_REPORTING`` stays optional for a provider: DHealth's review model already works with
#: the clinician acting in DHealth, so a provider connector that cannot report review still
#: delivers the whole order-to-result journey. ``PROCESSING_EVENT`` stays optional because nothing
#: in the Exchange gates on it.
_REQUIRED_LAB = (
    Capability.ORDER_RECEIPT,
    Capability.BUSINESS_ACCEPTANCE,
    Capability.COLLECTION_EVENT,
    Capability.RELEASED_RESULT_PUBLICATION,
    Capability.AMENDMENT_PUBLICATION,
)

_REQUIRED_PROVIDER = (
    Capability.ORDER_SUBMISSION,
    Capability.RELEASED_RESULT_RECEIPT,
    Capability.AMENDMENT_RECEIPT,
)

_OPTIONAL_LAB = (Capability.PROCESSING_EVENT,)
_OPTIONAL_PROVIDER = (Capability.REVIEW_REPORTING,)


def required_capabilities(organization_type: str) -> frozenset[Capability]:
    """The production bundle for a facility of this type.

    Raises for a type this contract does not describe, rather than returning an empty set -- an
    empty requirement would make an unknown organization type trivially qualified, which is the
    fail-open direction.
    """
    if organization_type == "LAB":
        return frozenset(_REQUIRED_LAB)
    if organization_type == "PROVIDER":
        return frozenset(_REQUIRED_PROVIDER)
    raise ValueError(
        f"no DHealth Connect capability bundle for organization type {organization_type!r}"
    )


def optional_capabilities(organization_type: str) -> frozenset[Capability]:
    """What a connector of this type may additionally support without being obliged to."""
    if organization_type == "LAB":
        return frozenset(_OPTIONAL_LAB)
    if organization_type == "PROVIDER":
        return frozenset(_OPTIONAL_PROVIDER)
    raise ValueError(
        f"no DHealth Connect capability bundle for organization type {organization_type!r}"
    )


# ── Health, distinct from qualification ──────────────────────────────────────────────────────


class HealthState(StrEnum):
    """Whether a connector runtime is reachable and working *right now*.

    A completely different question from whether it was ever qualified, and kept in a completely
    different place for that reason. The two combine in only one safe direction:

        QUALIFIED + HEALTHY      -> may carry production traffic
        QUALIFIED + UNAVAILABLE  -> must not
        HEALTHY   + not qualified-> must not
        UNKNOWN                  -> must not

    ``UNKNOWN`` is the honest state for every connector in this build, because no runtime exists to
    report anything else. It is the default and it is not a placeholder for optimism: DHealth does
    not have evidence a connector is reachable, so it says so.
    """

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


# ── Failure classification ───────────────────────────────────────────────────────────────────


class FailureCategory(StrEnum):
    """Why a transaction failed, and therefore whether retrying it could ever help.

    The runtime that acts on this is DH-CONNECT-AGENT-001's. What belongs here is the meaning, so
    that two connectors do not invent two different words for "this will never succeed".
    """

    #: Transient. Retry with backoff.
    TEMPORARY = "TEMPORARY"
    #: The remote system is down or unreachable. Retry with backoff; alert if sustained.
    REMOTE_UNAVAILABLE = "REMOTE_UNAVAILABLE"
    #: Credentials rejected or expired. Retrying the same call is pointless; a human must act.
    AUTHENTICATION = "AUTHENTICATION"
    #: A test, patient, practitioner or unit could not be mapped. Never retried into a guess.
    MAPPING = "MAPPING"
    #: The payload is structurally or semantically invalid. Permanent.
    VALIDATION = "VALIDATION"
    #: The same event identity arrived with different content. Permanent; quarantine, never merge.
    CONFLICT = "CONFLICT"
    #: The source system cannot express a workflow step this contract requires. Permanent.
    UNSUPPORTED_WORKFLOW = "UNSUPPORTED_WORKFLOW"


#: The categories a runtime may retry. Everything else is permanent and belongs in a dead letter.
RETRYABLE = frozenset({FailureCategory.TEMPORARY, FailureCategory.REMOTE_UNAVAILABLE})


# ── Fingerprints ─────────────────────────────────────────────────────────────────────────────


def _canonical(value: Any) -> Any:
    """Normalize before hashing, so equal meaning always hashes equally.

    Timestamps go to UTC and then to ISO-8601, because two clients writing the same instant with
    different offsets mean the same thing and must not produce two fingerprints. Decimals go to
    strings, because float repr is not stable across platforms.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime cannot be fingerprinted; the offset is part of the fact"
            )
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def fingerprint(value: Any) -> str:
    """A stable SHA-256 over canonical JSON.

    Never Python's ``hash()``: it is randomized per process for strings, so a fingerprint computed
    in one worker would not match the same content in another, and a qualification pinned to it
    would go stale on restart for no reason.

    Key order and whitespace are not part of the fact, so they are normalized away. Any field whose
    *value* differs produces a different fingerprint -- that is the whole point, and it is what
    makes a changed mapping or a changed configuration invalidate a qualification automatically
    rather than by somebody remembering to.
    """
    encoded = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class TestMapping(BaseModel):
    """One explicit link between an external orderable code and a DHealth catalog entry.

    Explicit, always. No name similarity, no substring match, no automatic LOINC lookup, no
    "close enough". A test DHealth cannot map is a test DHealth refuses to route -- fail closed --
    because the alternative is sending a patient's sample to be analysed as something else.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_system: str = Field(min_length=1, max_length=64)
    source_code: str = Field(min_length=1, max_length=128)
    catalog_entry_id: uuid.UUID
    catalog_code: str = Field(min_length=1, max_length=64)


def mapping_fingerprint(mappings: Sequence[TestMapping]) -> str:
    """The fingerprint of a whole mapping revision.

    Order-independent: two connectors listing the same mappings in different orders have the same
    mapping, and must not be told they need requalification for it. One changed target, one added
    row or one removed row changes the fingerprint, and therefore invalidates the qualification
    that was pinned to it -- which is correct, because a mapping change can silently alter what
    clinical test a patient actually receives.
    """
    rows = sorted(
        (m.source_system, m.source_code, str(m.catalog_entry_id), m.catalog_code) for m in mappings
    )
    return fingerprint({"mapping_revision": rows})


# ── External identity ────────────────────────────────────────────────────────────────────────


class ScopedExternalId(BaseModel):
    """An identifier from somebody else's system, carrying the scope that makes it meaningful.

    ``123`` is not an identifier. ``123, in namespace MRN, asserted by OpenELIS, at Laboratoire
    Central`` is. The Exchange already learned this the expensive way: ``PatientReference`` is
    unique on ``(source_organization_id, source_system, source_identity_system,
    source_identity_value)`` and not on the last two alone, because two facilities both numbering
    their patients from 1 would otherwise be merged into one person. This model carries exactly
    those four so the bridge can hand them to the existing repository unchanged, rather than
    inventing a second patient identity model beside the one the Exchange owns.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_organization_id: uuid.UUID
    source_system: str = Field(min_length=1, max_length=64)
    source_identity_system: str = Field(min_length=1, max_length=256)
    source_identity_value: str = Field(min_length=1, max_length=256)

    def scope_key(self) -> tuple[str, str, str, str]:
        """The tuple two identifiers must share before they can be called the same person."""
        return (
            str(self.source_organization_id),
            self.source_system,
            self.source_identity_system,
            self.source_identity_value,
        )


class SourcePractitionerRef(ScopedExternalId):
    """A practitioner from the source system, under the identical no-guessing rule.

    "Dr. Ahmed" is not a practitioner identity. A source practitioner DHealth cannot map is an
    error the connector must surface, never a name DHealth matches on similarity.
    """


# ── Canonical envelope ───────────────────────────────────────────────────────────────────────


class MessageType(StrEnum):
    ORDER_SUBMISSION = "ORDER_SUBMISSION"
    TECHNICAL_ACK = "TECHNICAL_ACK"
    BUSINESS_ACCEPTANCE = "BUSINESS_ACCEPTANCE"
    BUSINESS_REJECTION = "BUSINESS_REJECTION"
    COLLECTION_EVENT = "COLLECTION_EVENT"
    RELEASED_RESULT = "RELEASED_RESULT"
    RELEASED_AMENDMENT = "RELEASED_AMENDMENT"
    #: Added in contract 1.1 (DH-CONNECT-AGENT-001). The first message that travels *outward*.
    ORDER_NOTIFICATION = "ORDER_NOTIFICATION"


class Direction(StrEnum):
    """Which way a message travels. Added in contract 1.1.

    Contract 1.0 described seven message types and every one of them ran connector -> DHealth. That
    was not an oversight about direction, it was the shape of a build with no runtime: DHealth could
    receive what a connector chose to send and could send nothing at all. It also meant
    ``ORDER_RECEIPT`` -- a *required* laboratory capability -- was unexercisable by construction,
    which is the hole DH-CONNECT-CONTRACT-001's correction had to record as ``DECLARED``.

    Direction is an explicit enum rather than a naming convention because the runtime enforces it at
    two boundaries in opposite senses: a connector may only *submit* ``CONNECTOR_TO_DHEALTH``
    messages, and DHealth may only *queue* ``DHEALTH_TO_CONNECTOR`` ones. A convention cannot be
    checked; this can.
    """

    CONNECTOR_TO_DHEALTH = "CONNECTOR_TO_DHEALTH"
    DHEALTH_TO_CONNECTOR = "DHEALTH_TO_CONNECTOR"


_DIRECTIONS: Mapping[MessageType, Direction] = {
    MessageType.ORDER_SUBMISSION: Direction.CONNECTOR_TO_DHEALTH,
    MessageType.TECHNICAL_ACK: Direction.CONNECTOR_TO_DHEALTH,
    MessageType.BUSINESS_ACCEPTANCE: Direction.CONNECTOR_TO_DHEALTH,
    MessageType.BUSINESS_REJECTION: Direction.CONNECTOR_TO_DHEALTH,
    MessageType.COLLECTION_EVENT: Direction.CONNECTOR_TO_DHEALTH,
    MessageType.RELEASED_RESULT: Direction.CONNECTOR_TO_DHEALTH,
    MessageType.RELEASED_AMENDMENT: Direction.CONNECTOR_TO_DHEALTH,
    MessageType.ORDER_NOTIFICATION: Direction.DHEALTH_TO_CONNECTOR,
}


def message_direction(message_type: MessageType) -> Direction:
    """The direction this message type travels.

    Raises for a type absent from the table rather than guessing. A new message type whose direction
    nobody stated is a message the runtime's two boundary checks would both let through, which is
    the fail-open direction; ``test_every_message_type_declares_a_direction`` makes forgetting it a
    test failure rather than a production surprise.
    """
    try:
        return _DIRECTIONS[message_type]
    except KeyError:  # pragma: no cover - the test above makes this unreachable
        raise ValueError(f"no direction declared for message type {message_type!r}") from None


def require_aware(value: datetime) -> datetime:
    """Refuse a naive datetime. Public because more than one module needs exactly this rule.

    DH-CONNECT-AGENT-001's heartbeat carries the agent's own clock reading, and §59 forbids naive
    timestamps everywhere rather than only in the envelope. Two copies of this check would be two
    places for it to be relaxed.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("timestamp must be offset-aware; a naive instant is not a fact about when")
    return value


class OrderSubmission(BaseModel):
    """A provider's own system asking DHealth for diagnostic work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: Literal[MessageType.ORDER_SUBMISSION] = MessageType.ORDER_SUBMISSION
    patient: ScopedExternalId
    practitioner: SourcePractitionerRef
    external_order_id: str = Field(min_length=1, max_length=128)
    #: Source orderable codes, not DHealth catalog ids. Mapping happens at the boundary, under an
    #: explicit mapping revision, and an unmapped code refuses the whole submission.
    source_test_codes: tuple[str, ...] = Field(min_length=1)


class TechnicalAck(BaseModel):
    """ "I received your bytes."" Nothing else. Not a promise that anybody will do the work.

    A separate class from ``BusinessAcceptance`` on purpose. A connector author who wants to say
    "the laboratory accepted this order" has to construct a different object with a different
    author and a different timestamp; they cannot get there by setting a flag on this one. HTTP 200
    is this message, never the other one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: Literal[MessageType.TECHNICAL_ACK] = MessageType.TECHNICAL_ACK
    acknowledged_message_id: uuid.UUID
    #: The receiving system's own id for what it stored, when it has one. Never a DHealth id.
    external_receipt_id: str | None = Field(default=None, max_length=128)


class BusinessAcceptance(BaseModel):
    """The laboratory says it will actually perform this work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: Literal[MessageType.BUSINESS_ACCEPTANCE] = MessageType.BUSINESS_ACCEPTANCE
    external_order_id: str = Field(min_length=1, max_length=128)
    #: The laboratory's own accession/order number, if it issues one.
    external_accession_id: str | None = Field(default=None, max_length=128)


class BusinessRejection(BaseModel):
    """The laboratory says it will not perform this work, and why in its own words.

    ``reason`` is free text on purpose: an enum here would force every partner's real reason into
    DHealth's vocabulary, and "the analyser is down until Thursday" does not have a code. It is
    shown to a human, never parsed into a decision.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: Literal[MessageType.BUSINESS_REJECTION] = MessageType.BUSINESS_REJECTION
    external_order_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=512)


class CollectionEvent(BaseModel):
    """A specimen was collected, as an event the source system actually observed.

    Never inferred from order creation, row creation, accession creation or a processing timestamp
    -- DHealth's own rule since DH-011J2, and the reason ``LabOrderStatus`` has no collection
    state. A connector with no reliable source signal for this omits the message; it does not
    approximate one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: Literal[MessageType.COLLECTION_EVENT] = MessageType.COLLECTION_EVENT
    external_order_id: str = Field(min_length=1, max_length=128)


class ResultObservation(BaseModel):
    """One measured value, as the source reported it.

    Source-flexible on purpose: a CONNECT laboratory produces results in its own system with its
    own codes, units and ranges, and DHealth records what it was told. DHealth does not invent a
    unit, normalize a reference range, infer normal/abnormal, or map an analyte -- every one of
    those would be DHealth making a clinical claim it has no basis for. Native result templates
    are a Native concern and no CONNECT laboratory needs one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_code: str = Field(min_length=1, max_length=64)
    display_label: str = Field(min_length=1, max_length=256)
    result_type: Literal["NUMERIC", "TEXT"]
    numeric_value: Decimal | None = None
    numeric_unit: str | None = Field(default=None, max_length=32)
    reference_range_low: Decimal | None = None
    reference_range_high: Decimal | None = None
    text_value: str | None = Field(default=None, max_length=4096)


class ReleasedResult(BaseModel):
    """A result the source workflow has **professionally released**, and only such a result.

    ``source_release_confirmed`` is not a formality. A row existing in a LIS is not a release; a
    value being typed is not a release; an analyser emitting a number is not a release. If the
    source system has no reliable professional-release signal, the honest conclusion is that the
    integration is not safely production-ready -- which onboarding already records as a blocker.
    The connector does not invent the signal, and this flag is how it states that it has one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: Literal[MessageType.RELEASED_RESULT] = MessageType.RELEASED_RESULT
    external_order_id: str = Field(min_length=1, max_length=128)
    #: The source system's own identifier for this report/release. The idempotency key for LQ8/LQ9.
    external_report_id: str = Field(min_length=1, max_length=128)
    source_release_confirmed: bool
    observations: tuple[ResultObservation, ...] = Field(min_length=1)


class ReleasedAmendment(BaseModel):
    """A correction made *after* the source professionally released, as a new version.

    Not a pre-release edit. A LIS may change a value any number of times before release, and none
    of those is a DHealth amendment -- nothing was ever released, so nothing is being corrected
    from the clinician's point of view. ``corrects_external_report_id`` names the predecessor, so
    the amendment can be refused rather than guessed at when the original has not arrived.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: Literal[MessageType.RELEASED_AMENDMENT] = MessageType.RELEASED_AMENDMENT
    external_order_id: str = Field(min_length=1, max_length=128)
    external_report_id: str = Field(min_length=1, max_length=128)
    corrects_external_report_id: str = Field(min_length=1, max_length=128)
    source_release_confirmed: bool
    observations: tuple[ResultObservation, ...] = Field(min_length=1)


class OrderNotification(BaseModel):
    """DHealth telling a laboratory's connector that it has work. Contract 1.1.

    The mirror image of ``OrderSubmission``, and deliberately not the same class. A submission asks
    DHealth to *create* an order and therefore carries no DHealth order id; a notification is about
    an order that already exists, and the id lives on the envelope where the correlation rules can
    see it. Reusing one class for both would have meant a field that is required in one direction
    and forbidden in the other -- the kind of conditional shape that reads fine and is impossible to
    validate.

    **What it does not carry is the point.** No date of birth, no sex, no address, no phone number,
    no diagnosis, no clinical narrative, no free-text note. A LIS that needs demographics to
    register a patient is describing a real requirement, and the honest place to raise it is
    DH-CONNECT-ADAPTER-001 with a named partner and a stated purpose -- not here, pre-emptively, on
    behalf of a hypothetical system, which is how minimization turns into a JSON blob. ADR-056
    decision 5 stands.

    ``catalog_codes`` are **DHealth catalog codes**, not the laboratory's own orderables. The
    connector maps them through the same ``TestMapping`` revision its qualification pinned, and an
    unmapped code fails the notification closed rather than delivering a partial order -- a
    laboratory that runs four of five requested tests and reports four results has produced a
    clinically misleading report, and nothing downstream would know a test was dropped.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: Literal[MessageType.ORDER_NOTIFICATION] = MessageType.ORDER_NOTIFICATION
    #: The patient as the *ordering provider's* system identifies them. DHealth holds no identity
    #: the laboratory asserted -- if it did, this would still be the provider's, because that is the
    #: identity the order was placed under.
    patient: ScopedExternalId
    practitioner: SourcePractitionerRef
    catalog_codes: tuple[str, ...] = Field(min_length=1)


ConnectMessage = Annotated[
    OrderSubmission
    | OrderNotification
    | TechnicalAck
    | BusinessAcceptance
    | BusinessRejection
    | CollectionEvent
    | ReleasedResult
    | ReleasedAmendment,
    Field(discriminator="message_type"),
]


class Envelope(BaseModel):
    """What travels, whatever the transport underneath happens to be.

    ``extra="forbid"``: this envelope gates production clinical routing, and a field DHealth does
    not understand is a field DHealth cannot honour. Silently ignoring it would let a partner
    believe they had communicated something that never arrived anywhere.

    **Two timestamps, never one.** ``source_occurred_at`` is when the thing happened in the
    partner's system; ``sent_at`` is when their connector transmitted it. DHealth records its own
    receive time separately again. After an outage a connector drains a backlog, and every message
    in it has a ``sent_at`` of today and a ``source_occurred_at`` of whenever the work really
    happened -- treating arrival as the event time would silently rewrite a week of clinical
    chronology.

    ``organization_id`` is the facility this message belongs to and it is **explicit**. It is never
    inferred from a patient identifier, a hostname, an email address, a display name or an external
    facility name. A connector installation is bound to exactly one organization and may not act
    for another.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = Field(min_length=1, max_length=16)
    message_id: uuid.UUID
    connector_installation_id: uuid.UUID
    organization_id: uuid.UUID
    source_system: str = Field(min_length=1, max_length=64)
    #: The source system's own id for the event. With ``organization_id`` and ``source_system``
    #: this is the replay key: the same triple twice is a duplicate, whatever ``message_id`` says.
    source_event_id: str = Field(min_length=1, max_length=128)
    source_occurred_at: datetime
    sent_at: datetime
    #: DHealth's own order id when the message is about an order DHealth already knows.
    lab_order_id: uuid.UUID | None = None
    payload: ConnectMessage

    @field_validator("source_occurred_at", "sent_at")
    @classmethod
    def _offset_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def _order_correlation_matches_message(self) -> Envelope:
        """A message about a DHealth order must name it; a submission must not.

        ``BusinessAcceptance`` without ``lab_order_id`` is a laboratory accepting *something* --
        the correlation is the entire content of the message, and accepting it without one would
        leave the runtime to guess which order from the external id, which is the guessing this
        contract exists to forbid. ``OrderSubmission`` runs the other way: it is asking DHealth to
        create an order, so an order id in it would be either a fabrication or a replay of somebody
        else's, and forbidding it is cheaper than deciding which.
        """
        needs_order = {
            MessageType.ORDER_NOTIFICATION,
            MessageType.TECHNICAL_ACK,
            MessageType.BUSINESS_ACCEPTANCE,
            MessageType.BUSINESS_REJECTION,
            MessageType.COLLECTION_EVENT,
            MessageType.RELEASED_RESULT,
            MessageType.RELEASED_AMENDMENT,
        }
        kind = self.payload.message_type
        if kind in needs_order and self.lab_order_id is None:
            raise ValueError(
                f"{kind.value} is about a DHealth order and must carry lab_order_id; "
                "the order is never inferred from an external identifier"
            )
        if kind is MessageType.ORDER_SUBMISSION and self.lab_order_id is not None:
            raise ValueError(
                "ORDER_SUBMISSION creates a DHealth order and must not claim one already exists"
            )
        return self

    @field_validator("contract_version")
    @classmethod
    def _known_contract(cls, value: str) -> str:
        # A message declaring a major version this build does not implement is refused at the
        # boundary rather than parsed hopefully. Requalification is the answer, not best effort.
        if contract_major(value) != contract_major(CONTRACT_VERSION):
            raise ValueError(
                f"contract version {value!r} is not compatible with {CONTRACT_VERSION!r}; "
                "a new major version requires requalification"
            )
        return value

    def replay_scope(self) -> str:
        """The scope a ``source_event_id`` is unique within.

        Organization plus source system: the same local event id from two facilities, or from two
        systems at one facility, is two different events. Without this scoping the millionth
        connector to number its events from 1 would collide with the first.
        """
        return f"{self.organization_id}:{self.source_system}"

    def content_fingerprint(self) -> str:
        """What "the same content" means for conflicting-replay detection.

        The payload and the source event time -- deliberately **not** ``message_id`` or
        ``sent_at``, which differ on every honest retry of the identical event and would make every
        retry look like a conflict.
        """
        return fingerprint(
            {
                "payload": self.payload.model_dump(mode="json"),
                "source_occurred_at": self.source_occurred_at,
                "lab_order_id": self.lab_order_id,
            }
        )


# ── Replay ───────────────────────────────────────────────────────────────────────────────────


class ReceiptStore(Protocol):
    """Where a runtime remembers what it has already applied.

    The contract requires the *behaviour*; DH-CONNECT-AGENT-001 supplies the durable
    implementation. Transport is assumed to be at-least-once -- exactly-once delivery does not
    exist, and a design that depends on it is a design that fails the first time a network times
    out after the server committed. Correctness comes from durable correlation plus idempotency
    plus replay-safe mutation, not from hoping nobody retries.

    The rule, in full:

        same scope + same event id + same content      -> one effect, replay is safe
        same scope + same event id + different content -> conflict, quarantine, never overwrite
    """

    def seen(self, scope: str, source_event_id: str) -> str | None:
        """The content fingerprint already recorded for this event, or ``None``."""
        ...

    def record(self, scope: str, source_event_id: str, content_fingerprint: str) -> None:
        """Remember that this exact event with this exact content has been applied."""
        ...


class InMemoryReceiptStore:
    """A ``ReceiptStore`` for the qualification harness and for tests.

    Explicitly **not** production-durable, and named so nobody mistakes it for the real thing. A
    process restart forgets everything, which is exactly the property DH-CONNECT-AGENT-001 has to
    fix with a table; qualification can prove the semantics without it.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], str] = {}

    def seen(self, scope: str, source_event_id: str) -> str | None:
        return self._seen.get((scope, source_event_id))

    def record(self, scope: str, source_event_id: str, content_fingerprint: str) -> None:
        self._seen[(scope, source_event_id)] = content_fingerprint
