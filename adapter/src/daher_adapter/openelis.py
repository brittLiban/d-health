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

**Live ServiceRequest delivery is deliberately not wired here.** Every `ServiceRequest` create
is refused server-side (`Generated lab number is invalid`) until the real numeric OpenELIS site
code is supplied (NEEDS LIBAN #7) -- see `deliver_service_request`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

from daher_adapter.recovery import (
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
        if len(entries) == 0:
            return IdentifierMatch(count=MatchCount.ZERO)
        if len(entries) > 1:
            return IdentifierMatch(count=MatchCount.MANY)

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
        """DH-025's `TargetWriter` for ServiceRequest. Not activated.

        Deliberately refuses to run rather than attempt a create that OpenELIS's own accession
        generator will refuse anyway (`SITEYEARNUM` formats a 13-digit numeric accession; the
        configured prefix is still upstream's placeholder `DEV01`; every ServiceRequest create is
        rejected before subject or requester is even examined -- DH-011B, reconfirmed live this
        pass). Raising here rather than issuing a POST that would only ever fail keeps the ledger
        from recording a real attempt against a target that cannot accept one, and keeps this
        function's eventual real implementation the only place that ever removes the guard.
        """
        raise ServiceRequestDeliveryBlocked(
            "ServiceRequest delivery is gated on the real numeric OpenELIS site code "
            "(NEEDS LIBAN #7) -- see PROJECT_STATE.md BLOCKER-6"
        )


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
