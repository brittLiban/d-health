"""Adapter client for OpenELIS's embedded business FHIR API (DH-011D, BLOCKER-6 Option F).

**Never the standalone `openelis-fhir` HAPI store** (Option C's target, a different product at
a different endpoint, HAPI 6.6.0 with mutual TLS). This client speaks only to
`/OpenELIS-Global/fhir/*`, HAPI 7.0.2 embedded in the webapp, authenticated with HTTP Basic
(`SecurityConfig.BasicAuthRequestedMatcher` -- verified live: a wrong password returns 401,
so this boundary is genuinely enforced, not open). DH-011B's whole spike exists because
conflating the two servers is the mistake this file is shaped never to make.

**PatientProvider's false-success defect is why nothing here trusts a status code.**
PROJECT_STATE.md's DH-011B evidence ("known bug 19"): a `POST /Patient` returns HTTP 201 with
no `Location` header whether or not the native `clinlims` persist actually succeeded -- two
identical requests differing only in a name field both returned 201; one wrote a row, one wrote
none. So every create here performs an authoritative identifier read-back after the POST, and
only an exact single match is treated as success:

    2xx alone            -> means nothing
    read-back: 1 match   -> SUCCESS
    read-back: 0 matches -> FAILED_NOT_PERSISTED (the create silently did not happen)
    read-back: 2+ matches -> AMBIGUOUS, fail closed
    read-back unreachable -> AMBIGUOUS, fail closed

**The `ServiceRequest` correlation probe below binds directly to `recovery.py`'s
`TargetProbe`/`TargetWriter` protocol** (DH-025). Its contract is the same shape, applied to the
one resource type whose search behavior could not be proven this pass -- see `service_request_probe`
for exactly what is and is not verified.

**IMPLEMENTED CAPABILITY vs. ACTIVATION PERMISSION (DH-011D3, independent-review finding C) are
deliberately two different things here.** `deliver_service_request` is the real writer protocol --
POST, then always an authoritative native reconciliation read-back, never a second POST, fail
closed on anything short of exactly one match. It runs and is tested with scripted fixtures.
`deliver_service_request_if_activated` is the separate, thin activation gate: it refuses to call
the writer at all unless `Settings.service_request_delivery_ready` is true, i.e. a real numeric
OpenELIS site code is configured (NEEDS LIBAN #7). Nothing in this repository currently calls
either function in production -- DH-011E's poller is not wired to any of this -- so today the gate
is enforced by absence of a caller, not by this file alone; the gate exists so that whenever a
caller is eventually wired, activation and capability cannot be conflated by accident.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

from daher_adapter.config import Settings
from daher_adapter.recovery import (
    AmbiguousTargetState,
    SourceCorrelation,
    TargetPresence,
    TargetProbeResult,
    TargetRecord,
)
from daher_adapter.retry import TransientIntegrationError

logger = logging.getLogger("daher_adapter.openelis")

PATIENT = "Patient"
PRACTITIONER = "Practitioner"
SERVICE_REQUEST = "ServiceRequest"

# clinlims.electronic_order.external_id (existing column, existing index
# e_order_external_id_idx) is the native entity this correlation is designed to land in --
# see openelis-config/patches/order-correlation/PROVENANCE.md for the full source-level
# account of why, and what is not yet wired there.
SERVICE_REQUEST_TARGET_SYSTEM = "urn:daher-health:adapter:source-event"


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    body: Mapping[str, Any] | None


# The transport is injected -- same seam `openmrs.py` uses for `BundleFetcher` -- so every
# code path below is testable with a scripted double, never a real network call.
OpenElisGet = Callable[[str], Awaitable[HttpResult]]
OpenElisPost = Callable[[str, Mapping[str, Any]], Awaitable[HttpResult]]


class OpenElisProtocolError(RuntimeError):
    """The server's response is not something this client can safely interpret."""


class MatchCount(StrEnum):
    ZERO = "zero"
    ONE = "one"
    MANY = "many"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class IdentifierMatch:
    count: MatchCount
    fhir_id: str | None = None

    def __post_init__(self) -> None:
        if self.count is MatchCount.ONE and self.fhir_id is None:
            raise ValueError("a single match requires the matched resource's FHIR id")
        if self.count is not MatchCount.ONE and self.fhir_id is not None:
            raise ValueError("only a single match may carry a FHIR id")


class VerificationOutcome(StrEnum):
    SUCCESS = "success"
    FAILED_NOT_PERSISTED = "failed_not_persisted"
    AMBIGUOUS = "ambiguous"
    LOOKUP_UNAVAILABLE = "lookup_unavailable"


@dataclass(frozen=True, slots=True)
class VerifiedCreateResult:
    outcome: VerificationOutcome
    fhir_id: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is VerificationOutcome.SUCCESS and self.fhir_id is None:
            raise ValueError("a successful verified create requires the resulting FHIR id")
        if self.outcome is not VerificationOutcome.SUCCESS and self.fhir_id is not None:
            raise ValueError("only a successful verified create may carry a FHIR id")


class ServiceRequestDeliveryBlocked(RuntimeError):
    """Live ServiceRequest delivery is gated until the real numeric OpenELIS site code exists."""


class CancellationTargetState(StrEnum):
    """DH-011J §11's four required cancellation-probe outcomes, narrowed to the two this client
    can actually distinguish plus the two shared failure modes every DH-025 probe already uses.
    """

    EXISTS_NOT_CANCELLED = "exists_not_cancelled"
    EXISTS_CANCELLED = "exists_cancelled"
    AMBIGUOUS = "ambiguous"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class CancellationProbeResult:
    state: CancellationTargetState
    record_id: str | None = None


class OpenElisClient:
    """Talks to `/OpenELIS-Global/fhir/*` only. Never the standalone `openelis-fhir` store."""

    def __init__(self, base_url: str, get: OpenElisGet, post: OpenElisPost) -> None:
        self._base = base_url.rstrip("/")
        self._get = get
        self._post = post

    async def identifier_search(
        self, resource_type: str, system: str, value: str
    ) -> IdentifierMatch:
        """Exact `system|value` token search. Never a substring or system-agnostic match.

        Control-proven live (DH-011B/PROJECT_STATE.md): a wrong system with the right value
        returns zero, so this is real token matching on this server, not inference from a link
        or a guess about what the server does with the parameter.

        DH-011D3 (Finding B correction, scoped narrowly): counting `Bundle.entry` alone
        overcounts one specific real case -- OpenELIS's native ServiceRequest search
        (`ServiceRequestProvider.searchDaherCorrelationNatively`) returns one entry per
        constituent test on a multi-test order (a prenatal panel, the normal V1 case), since one
        native `Analysis` produces one `ServiceRequest`. Left uncorrected, every multi-test order
        would read back as MANY and could never reconcile. `Bundle.total` is FHIR's field for
        "how many logical results matched" and that provider sets it correctly (0 or 1 -- the
        DB's partial unique index on the correlation value guarantees at most one).

        This method does NOT require `total` to be present, and does not otherwise trust it over
        `entry`: Patient, Practitioner, and every ServiceRequest query that does not carry the
        Daher correlation system all go through the standalone secondary FHIR store via
        `forwardSearchToFhirStore`, and whether that store always populates `total` has not been
        verified. A missing-total-means-UNREACHABLE rule would risk silently breaking every one
        of those identifier searches. The only place `total` overrides the entry count is the
        one case it exists to fix: more than one entry with `total == 1` collapses to ONE, using
        the first entry as a representative id. Every other outcome (0 entries, 1 entry, or more
        than one entry with `total` absent/disagreeing) is exactly what counting entries already
        gave, and is unchanged by this correction.
        """
        token = f"{system}|{value}"
        url = f"{self._base}/{resource_type}?identifier={quote(token, safe='')}"
        try:
            result = await self._get(url)
        except Exception as error:  # noqa: BLE001 - any transport failure means UNREACHABLE
            logger.warning(
                "openelis_identifier_search_unreachable",
                extra={"resource_type": resource_type, "error_type": type(error).__name__},
            )
            return IdentifierMatch(count=MatchCount.UNREACHABLE)

        if result.status != 200 or not isinstance(result.body, Mapping):
            logger.warning(
                "openelis_identifier_search_unreachable",
                extra={"resource_type": resource_type, "status": result.status},
            )
            return IdentifierMatch(count=MatchCount.UNREACHABLE)

        entries = _entries(result.body)
        total = result.body.get("total")
        total_says_one_logical_match = (
            isinstance(total, int) and not isinstance(total, bool) and total == 1
        )

        if len(entries) == 0:
            return IdentifierMatch(count=MatchCount.ZERO)
        if len(entries) > 1 and not total_says_one_logical_match:
            return IdentifierMatch(count=MatchCount.MANY)

        # Either exactly one entry, or more than one entry that Bundle.total says is one logical
        # match (the multi-test-order case) -- either way, the first entry is representative.
        fhir_id = _resource_id(entries[0])
        if fhir_id is None:
            # A matched entry with no readable id cannot be trusted as ONE -- treat it the same
            # as an unreachable lookup rather than fabricate an id.
            return IdentifierMatch(count=MatchCount.UNREACHABLE)
        return IdentifierMatch(count=MatchCount.ONE, fhir_id=fhir_id)

    async def create_patient(
        self, payload: Mapping[str, Any], *, system: str, value: str
    ) -> VerifiedCreateResult:
        return await self._create_and_verify(PATIENT, payload, system=system, value=value)

    async def create_practitioner(
        self, payload: Mapping[str, Any], *, system: str, value: str
    ) -> VerifiedCreateResult:
        return await self._create_and_verify(PRACTITIONER, payload, system=system, value=value)

    async def _create_and_verify(
        self, resource_type: str, payload: Mapping[str, Any], *, system: str, value: str
    ) -> VerifiedCreateResult:
        """POST, then treat the response status as advisory only -- known bug 19.

        Success is decided exclusively by the read-back that follows, never by the POST's own
        2xx/Location. This is the one piece of §8's discipline that must never regress: a
        client that trusted this POST's 201 would mark a delivery complete that never happened.
        """
        try:
            await self._post(f"{self._base}/{resource_type}", payload)
        except Exception as error:  # noqa: BLE001 - a POST that never lands proves nothing either way
            logger.warning(
                "openelis_create_unreachable",
                extra={"resource_type": resource_type, "error_type": type(error).__name__},
            )
            return VerifiedCreateResult(outcome=VerificationOutcome.LOOKUP_UNAVAILABLE)

        match = await self.identifier_search(resource_type, system, value)
        if match.count is MatchCount.ONE and match.fhir_id is not None:
            return VerifiedCreateResult(outcome=VerificationOutcome.SUCCESS, fhir_id=match.fhir_id)
        if match.count is MatchCount.ZERO:
            return VerifiedCreateResult(outcome=VerificationOutcome.FAILED_NOT_PERSISTED)
        if match.count is MatchCount.MANY:
            return VerifiedCreateResult(outcome=VerificationOutcome.AMBIGUOUS)
        return VerifiedCreateResult(outcome=VerificationOutcome.LOOKUP_UNAVAILABLE)

    async def service_request_probe(self, correlation: SourceCorrelation) -> TargetProbeResult:
        """DH-025's `TargetProbe`, bound to the one resource type whose search behavior this
        pass could patch but could not exercise end to end (no ServiceRequest can be created
        without the real site code -- see `deliver_service_request`).

        Requires the DH-011D search-parameter patch (`patches/order-correlation/`) to be live on
        the target OpenELIS image; against the unpatched upstream image every call here would
        receive HTTP 400 and correctly report AMBIGUOUS, never a false ABSENT.
        """
        match = await self.identifier_search(
            SERVICE_REQUEST, SERVICE_REQUEST_TARGET_SYSTEM, correlation.value
        )
        if match.count is MatchCount.ONE and match.fhir_id is not None:
            return TargetProbeResult(
                presence=TargetPresence.FOUND,
                record=TargetRecord(system=SERVICE_REQUEST, record_id=match.fhir_id),
            )
        if match.count is MatchCount.ZERO:
            return TargetProbeResult(presence=TargetPresence.ABSENT)
        # MANY, UNREACHABLE, and the defensive ONE-without-an-id case all fail closed -- DH-025
        # never distinguishes "too many" from "couldn't tell" at the reconciliation boundary;
        # all of them mean "do not write, do not complete, surface to an operator."
        return TargetProbeResult(presence=TargetPresence.AMBIGUOUS)

    async def deliver_service_request(
        self, correlation: SourceCorrelation, payload: Mapping[str, Any]
    ) -> TargetRecord:
        """DH-025's `TargetWriter` for ServiceRequest -- the real protocol (DH-011D3 finding C).

        1. Injects/validates the Daher correlation identifier on the outgoing payload --
           `_with_daher_identifier` raises rather than silently overwriting if the caller
           already attached a conflicting one.
        2. POSTs to the embedded business FHIR endpoint.
        3. Never trusts the POST's status alone (known bug 19's exact hazard). Whether the POST
           returned 2xx, an error status, or raised outright, the very next and only thing this
           method does is one authoritative native reconciliation read via `identifier_search`
           on the Daher system.
        4. Returns a `TargetRecord` only on exactly one authoritative match. Every other outcome
           -- zero matches after a possible write, 2+ matches, the POST itself never landing, or
           the reconciliation read being unreachable/malformed -- raises `AmbiguousTargetState`
           (the same DH-025 vocabulary `RecoveryCoordinator` already uses for a probe that cannot
           prove presence or absence). This method never issues a second POST under any of these
           outcomes -- "no blind retry" applies to its own internals, not only to its caller.

        This is the capability, not the permission to use it in production -- see
        `deliver_service_request_if_activated`.
        """
        outgoing = _with_daher_identifier(payload, correlation)

        try:
            await self._post(f"{self._base}/{SERVICE_REQUEST}", outgoing)
        except Exception as error:  # noqa: BLE001 - a POST that never lands is itself ambiguous
            raise AmbiguousTargetState(
                f"ServiceRequest POST did not complete: {type(error).__name__}"
            ) from error

        match = await self.identifier_search(
            SERVICE_REQUEST, SERVICE_REQUEST_TARGET_SYSTEM, correlation.value
        )
        if match.count is MatchCount.ONE and match.fhir_id is not None:
            return TargetRecord(system=SERVICE_REQUEST, record_id=match.fhir_id)
        if match.count is MatchCount.ZERO:
            raise AmbiguousTargetState(
                "ServiceRequest POST returned but native reconciliation found zero matches for "
                f"correlation {correlation.value} -- cannot prove whether the write happened"
            )
        # MANY or UNREACHABLE
        raise AmbiguousTargetState(
            "ServiceRequest POST reconciliation is ambiguous (multiple native matches or the "
            "target was unreachable) for correlation " + correlation.value
        )

    async def cancellation_probe(self, correlation: SourceCorrelation) -> CancellationProbeResult:
        """DH-011J's cancellation-specific probe -- deliberately not `TargetProbe`/`TargetPresence`
        (DH-025's FOUND/ABSENT model has no vocabulary for "found but not yet cancelled" versus
        "found and cancelled", both of which a cancellation caller must be able to tell apart).

        Reads the *existing* correlation search's own `ServiceRequest.status` field -- no new
        native read endpoint needed. `transformToServiceRequest` already maps a `Canceled`
        Analysis to FHIR status `"revoked"` (verified live, DH-011J, see PROVENANCE.md); every
        other native status maps to something other than `"revoked"`. `correlation` here is
        always the *original* added-test correlation the target was created under (the value
        already in `daher_analysis_correlation`) -- never a value re-derived from the
        cancellation event's own identity.
        """
        token = f"{SERVICE_REQUEST_TARGET_SYSTEM}|{correlation.value}"
        url = f"{self._base}/{SERVICE_REQUEST}?identifier={quote(token, safe='')}"
        try:
            result = await self._get(url)
        except Exception as error:  # noqa: BLE001 - any transport failure means UNREACHABLE
            logger.warning(
                "openelis_cancellation_probe_unreachable",
                extra={"error_type": type(error).__name__},
            )
            return CancellationProbeResult(state=CancellationTargetState.UNREACHABLE)

        if result.status != 200 or not isinstance(result.body, Mapping):
            return CancellationProbeResult(state=CancellationTargetState.UNREACHABLE)

        resources = _entries(result.body)
        total = result.body.get("total")
        total_says_one = isinstance(total, int) and not isinstance(total, bool) and total == 1

        if len(resources) == 0:
            # A cancellation target that does not resolve at all is not a state this probe has
            # vocabulary for (DH-025 requires the target to already exist -- it was created by an
            # earlier, already-completed add-test event) -- fail closed rather than invent one.
            return CancellationProbeResult(state=CancellationTargetState.AMBIGUOUS)
        if len(resources) > 1 and not total_says_one:
            return CancellationProbeResult(state=CancellationTargetState.AMBIGUOUS)

        resource = resources[0]
        fhir_id = _resource_id(resource)
        status = resource.get("status")
        if fhir_id is None or not isinstance(status, str):
            return CancellationProbeResult(state=CancellationTargetState.UNREACHABLE)

        if status == "revoked":
            return CancellationProbeResult(
                state=CancellationTargetState.EXISTS_CANCELLED, record_id=fhir_id
            )
        return CancellationProbeResult(
            state=CancellationTargetState.EXISTS_NOT_CANCELLED, record_id=fhir_id
        )

    async def cancel_service_request(
        self, correlation: SourceCorrelation
    ) -> CancellationProbeResult:
        """DH-011J's `TargetWriter` analog for added-test cancellation.

        Calls the native `$cancel` operation
        (`patches/order-correlation/ServiceRequestProviderCancel.java.patch`), then -- never
        trusting that call's own HTTP outcome, exactly like
        `deliver_service_request` -- always reconciles via `cancellation_probe` and returns
        *that* result, whether the POST returned 200, an error status, or raised outright.

        This discipline is not theoretical here: DH-011J live-proved that a genuine concurrent
        double-cancellation can make the native side raise `StaleObjectStateException` (HTTP 500)
        on the losing caller even though the target was, in fact, correctly cancelled by the
        winner -- the exact same class of race DH-011M already proved and accepted on the create
        path. A caller that trusted this call's own status would misreport that legitimate
        outcome as a failure.

        Returns `EXISTS_CANCELLED` (cancellation confirmed) or `EXISTS_NOT_CANCELLED` (the
        target was reached but is not cancelled -- either the native ADR-018 status gate refused
        it, or the call never reached the server and nothing changed; this method does not and
        cannot distinguish those two from outside, and per DH-011J §14 both must be surfaced to
        an operator rather than silently treated as done). Raises `AmbiguousTargetState` only
        when reconciliation itself cannot prove either state.
        """
        payload = {
            "resourceType": "Parameters",
            "parameter": [{"name": "correlation", "valueString": correlation.value}],
        }
        try:
            await self._post(f"{self._base}/{SERVICE_REQUEST}/$cancel", payload)
        except Exception as error:  # noqa: BLE001 - the POST's own outcome is never trusted anyway
            logger.info(
                "openelis_cancel_post_did_not_complete", extra={"error_type": type(error).__name__}
            )

        result = await self.cancellation_probe(correlation)
        if result.state in (CancellationTargetState.AMBIGUOUS, CancellationTargetState.UNREACHABLE):
            raise AmbiguousTargetState(
                "Cancellation reconciliation could not prove cancelled or not-cancelled for "
                f"correlation {correlation.value}"
            )
        return result


def _with_daher_identifier(
    payload: Mapping[str, Any], correlation: SourceCorrelation
) -> Mapping[str, Any]:
    """Returns a copy of `payload` guaranteed to carry exactly one Daher-system identifier
    matching `correlation` -- injects it if absent, raises if the caller already attached a
    conflicting value under the same system rather than silently overwriting it, and
    normalizes to canonical form if it was already present and correct. Never mutates the
    caller's `payload`.
    """
    existing = payload.get("identifier")
    others: list[Any] = []
    if isinstance(existing, Sequence) and not isinstance(existing, str | bytes):
        for entry in existing:
            if isinstance(entry, Mapping) and entry.get("system") == SERVICE_REQUEST_TARGET_SYSTEM:
                if entry.get("value") != correlation.value:
                    raise ValueError(
                        "payload already carries a conflicting Daher correlation identifier: "
                        f"{entry.get('value')!r} != {correlation.value!r}"
                    )
                continue  # drop; re-added below in canonical form, never duplicated
            others.append(entry)
    daher_identifier = {"system": SERVICE_REQUEST_TARGET_SYSTEM, "value": correlation.value}
    return {**payload, "identifier": [*others, daher_identifier]}


async def deliver_service_request_if_activated(
    client: OpenElisClient,
    settings: Settings,
    correlation: SourceCorrelation,
    payload: Mapping[str, Any],
) -> TargetRecord:
    """The activation gate (DH-011D3 finding C, §11) -- deliberately separate from the writer
    capability above. Refuses even to attempt delivery unless `settings
    .service_request_delivery_ready` is true (a real, non-placeholder, numeric OpenELIS site
    code is configured). This is the one function production code should call to deliver a
    ServiceRequest; `OpenElisClient.deliver_service_request` itself has no opinion about
    activation, which is exactly what lets tests exercise its real protocol with scripted
    fixtures while production stays gated. Today nothing calls this in production either --
    DH-011E's poller is not wired to any of this -- so the gate is currently enforced by the
    absence of a caller too; this function exists so that whenever one is wired, activation and
    capability cannot be conflated by accident.
    """
    if not settings.service_request_delivery_ready:
        raise ServiceRequestDeliveryBlocked(
            "ServiceRequest delivery is gated on the real numeric OpenELIS site code "
            "(NEEDS LIBAN #7) -- see PROJECT_STATE.md BLOCKER-6"
        )
    return await client.deliver_service_request(correlation, payload)


def _entries(bundle: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    if bundle.get("resourceType") != "Bundle":
        raise OpenElisProtocolError(f"expected a Bundle, got {bundle.get('resourceType')!r}")
    entries = bundle.get("entry")
    if entries is None:
        return ()
    if not isinstance(entries, Sequence) or isinstance(entries, str | bytes):
        raise OpenElisProtocolError("Bundle.entry is not a list")
    resources = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise OpenElisProtocolError("Bundle entry contained a non-object element")
        resource = entry.get("resource")
        if isinstance(resource, Mapping):
            resources.append(resource)
    return tuple(resources)


def _resource_id(resource: Mapping[str, Any]) -> str | None:
    value = resource.get("id")
    return value if isinstance(value, str) and value else None


def unreachable(error: Exception) -> TransientIntegrationError:
    """Wrap a transport failure so `retry.py` treats it as retryable and nothing else does."""
    return TransientIntegrationError(f"OpenELIS unreachable: {type(error).__name__}")
