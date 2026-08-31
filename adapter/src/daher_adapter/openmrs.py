"""Reading orders out of OpenMRS FHIR R4 (DH-011E).

Read-only, by design and by capability: `ServiceRequest` on this server is search/read only, so
the adapter cannot write an order back into OpenMRS even if a future change tried to
(ADR-005 Q5's evidence).

**The window is bounded at both ends, and that was verified before being relied on**, because
the alternative is the bug this file is shaped to avoid. Probed on the running stack:

| Query | `total` |
|---|---|
| no bounds | 192 |
| `_lastUpdated=le2000-01-01` | 0 |
| `ge2026-08-11` & `le2026-08-12` | 96 — narrower than either bound alone |
| `ge2026-08-11` & `le2000-01-01` | **0** — an impossible range |

That last row is the one that matters: an impossible range returning 0 proves *both* bounds are
applied, rather than one being silently dropped. A server that ignored `le` would have returned
the `ge` result and looked fine.

**Why an upper bound at all**, when the accepted design only specified a lower one: without it,
records written *during* the poll land inside the window after `total` was read, so the
completeness check in `source_events.assemble_cohort` would fire on healthy traffic and the
poller would refuse to advance forever. Freezing the top of the window at a server-issued
instant makes the cohort a closed set. Anything newer is simply next cycle's work — no record is
skipped, because the next window's inclusive lower bound starts at what this one finished.

The HTTP call itself is injected. That keeps the ordering, paging and completeness logic testable
without a network, and it is the seam a different client can be dropped into later.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

from daher_adapter.instants import MalformedInstant, ServerInstant, parse_instant
from daher_adapter.ledger import MessageIdentity
from daher_adapter.retry import TransientIntegrationError
from daher_adapter.source_events import ResourceRejected, event_from_resource

logger = logging.getLogger("daher_adapter.openmrs")

SERVICE_REQUEST = "ServiceRequest"

# The server pages; 50 is what the probes used and what upstream's own paging emitted.
DEFAULT_PAGE_SIZE = 50

# A cohort larger than this in one 15-second window means something unexpected is happening
# (a bulk import, a misconfigured window). Refusing beats quietly pulling an unbounded set into
# memory and beats a poll cycle that never finishes.
DEFAULT_MAX_PAGES = 200

# Fetch one URL and return the decoded FHIR Bundle. Raising `TransientIntegrationError` is how
# an unreachable server is signalled; `retry.py` retries only that type, never a 4xx.
BundleFetcher = Callable[[str], Mapping[str, Any]]


class OpenMrsProtocolError(RuntimeError):
    """The server's response is not a Bundle the poller can act on."""


class TooManyPages(RuntimeError):
    """The window is larger than the configured page budget."""


class OpenMrsResourceNotFound(RuntimeError):
    """A single-resource read (not a search) confirmed the resource does not exist -- a real
    HTTP 404, not a transport failure. `_fetch`'s real implementation must raise this, and only
    this, for a confirmed 404 on `read_resource`; anything else (connection refused, timeout,
    5xx) must still surface as a plain exception so `check_source_supersession` treats it as
    UNREACHABLE, never as AMBIGUOUS-because-missing."""


class SourcePresence(StrEnum):
    """DH-011E4's dead-letter retry preflight result (PROVENANCE.md's DH-011E4 §7). Only
    `CURRENT` may authorize a retry."""

    CURRENT = "current"
    SUPERSEDED = "superseded"
    AMBIGUOUS = "ambiguous"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class SourcePreflightResult:
    presence: SourcePresence
    # A short, non-PHI explanation -- safe for operator CLI output (§11/§17). Never includes the
    # resource payload itself.
    detail: str


@dataclass(frozen=True, slots=True)
class FetchedWindow:
    """Everything one bounded window produced, before dedupe/ordering."""

    resources: tuple[Mapping[str, Any], ...]
    reported_total: int
    upper_bound: ServerInstant
    pages: int


class OpenMrsOrderSource:
    def __init__(
        self,
        base_url: str,
        fetch: BundleFetcher,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._fetch = fetch
        self._page_size = page_size
        self._max_pages = max_pages

    def server_instant(self, resource_type: str = SERVICE_REQUEST) -> ServerInstant:
        """The server's own current instant, taken from a Bundle it just produced.

        This is how ADR-015's bootstrap and every window's upper bound stay server-issued
        without needing an endpoint that reports a clock: every search Bundle carries
        `meta.lastUpdated`, generated at response time. A `_summary=count` search is the
        cheapest way to ask.
        """
        bundle = self._get(f"{self._base}/{resource_type}?_summary=count")
        meta = bundle.get("meta")
        if not isinstance(meta, Mapping):
            raise OpenMrsProtocolError("count Bundle carried no meta element to read a clock from")
        try:
            return parse_instant(meta.get("lastUpdated"))
        except MalformedInstant as error:
            raise OpenMrsProtocolError(f"server clock unusable: {error}") from error

    def fetch_window(
        self,
        *,
        since: ServerInstant,
        upper: ServerInstant,
        resource_type: str = SERVICE_REQUEST,
    ) -> FetchedWindow:
        """Fetch every page of `[since, upper]`, inclusive at both ends.

        Inclusive lower bound is `ge`, not `gt` — §4. At whole-second precision a `gt` would
        skip any record sharing the boundary second that had not been written when we last
        polled, and that record is gone forever with no trace. `ge` re-delivers the boundary
        second instead and the ledger absorbs the duplicates. Duplicates are an inconvenience;
        a skipped lab result is a missed diagnosis.
        """
        if upper < since:
            raise ValueError(
                f"upper bound {upper.raw!r} precedes the watermark {since.raw!r}; the server "
                f"clock appears to have moved backwards"
            )

        url = (
            f"{self._base}/{resource_type}"
            f"?_lastUpdated=ge{quote(since.raw, safe='')}"
            f"&_lastUpdated=le{quote(upper.raw, safe='')}"
            f"&_count={self._page_size}"
        )

        resources: list[Mapping[str, Any]] = []
        reported_total: int | None = None
        pages = 0

        while url is not None:
            pages += 1
            if pages > self._max_pages:
                raise TooManyPages(
                    f"window {since.raw}..{upper.raw} exceeded {self._max_pages} pages; refusing "
                    f"to keep pulling rather than silently truncating the cohort"
                )
            bundle = self._get(url)
            if reported_total is None:
                reported_total = _total(bundle)
            resources.extend(_entries(bundle))
            url = _next_link(bundle)

        if reported_total is None:  # pragma: no cover - the loop always runs at least once
            raise OpenMrsProtocolError("no Bundle was returned for the window")

        logger.info(
            "order_window_fetched",
            extra={
                "resource_type": resource_type,
                "pages": pages,
                "received": len(resources),
                "reported_total": reported_total,
            },
        )
        return FetchedWindow(
            resources=tuple(resources),
            reported_total=reported_total,
            upper_bound=upper,
            pages=pages,
        )

    def _get(self, url: str) -> Mapping[str, Any]:
        bundle = self._fetch(url)
        if not isinstance(bundle, Mapping):
            raise OpenMrsProtocolError(f"expected a JSON object from {url!r}")
        if bundle.get("resourceType") != "Bundle":
            # An OperationOutcome here is a real answer we must not treat as an empty window:
            # "no results" and "the query was rejected" are opposite facts.
            raise OpenMrsProtocolError(
                f"expected a Bundle, got {bundle.get('resourceType')!r} — a rejected query must "
                f"never be read as an empty window"
            )
        return bundle

    def read_resource(self, resource_type: str, resource_id: str) -> Mapping[str, Any] | None:
        """A direct FHIR `read` (`GET [base]/{type}/{id}`), not a `search` -- DH-011E4's
        dead-letter retry preflight needs this, because `?status=` and `?identifier=` are both
        confirmed-unsupported search parameters on this server (WATERMARK-DESIGN.md §6,
        DECISIONS.md's DH-011C/D correction), so re-fetching the exact resource by id is the only
        tested way to observe whether it has changed since it was dead-lettered.

        Returns `None` only for a confirmed 404 (`OpenMrsResourceNotFound`, raised by `_fetch`'s
        real implementation) -- a genuinely missing resource, distinct from an unreachable server,
        which still propagates as a plain exception.
        """
        url = f"{self._base}/{resource_type}/{quote(resource_id, safe='')}"
        try:
            resource = self._fetch(url)
        except OpenMrsResourceNotFound:
            return None
        if not isinstance(resource, Mapping):
            raise OpenMrsProtocolError(f"expected a JSON object from {url!r}")
        if resource.get("resourceType") != resource_type:
            raise OpenMrsProtocolError(
                f"expected a {resource_type!r} resource from {url!r}, got "
                f"{resource.get('resourceType')!r}"
            )
        return resource


def _total(bundle: Mapping[str, Any]) -> int:
    total = bundle.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise OpenMrsProtocolError(
            f"Bundle.total is {total!r}; without it the cohort cannot be proven complete"
        )
    return total


def _entries(bundle: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    entries = bundle.get("entry")
    if entries is None:
        return
    if not isinstance(entries, Sequence) or isinstance(entries, str | bytes):
        raise OpenMrsProtocolError("Bundle.entry is not a list")
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise OpenMrsProtocolError("Bundle.entry contained a non-object element")
        resource = entry.get("resource")
        if not isinstance(resource, Mapping):
            raise OpenMrsProtocolError("Bundle entry carried no resource object")
        yield resource


def _next_link(bundle: Mapping[str, Any]) -> str | None:
    links = bundle.get("link")
    if not isinstance(links, Sequence) or isinstance(links, str | bytes):
        return None
    for link in links:
        if isinstance(link, Mapping) and link.get("relation") == "next":
            url = link.get("url")
            if isinstance(url, str) and url:
                return url
    return None


def unreachable(error: Exception) -> TransientIntegrationError:
    """Wrap a transport failure so `retry.py` treats it as retryable and nothing else does."""
    return TransientIntegrationError(f"OpenMRS unreachable: {type(error).__name__}")


def check_source_supersession(
    identity: MessageIdentity,
    stored_payload: Mapping[str, object],
    *,
    source: OpenMrsOrderSource,
) -> SourcePreflightResult:
    """DH-011E4/DH-011E4B's mandatory dead-letter retry preflight (PROVENANCE.md's DH-011E4B §3).

    **Proven complete for openmrs-core 2.8.7 / fhir2 4.2.0 (the exact pinned versions), not just
    observed once.** `OrderServiceImpl.saveOrderInternal` routes both `Action.REVISE` and
    `Action.DISCONTINUE` -- the only two order actions that can supersede an existing order --
    through the identical `stopOrder` method on the previous order, which sets `dateStopped` and
    refuses (`CannotStopInactiveOrderException`) unless that order is currently active; there is no
    code path where either action succeeds without this mutation. fhir2's
    `ServiceRequestTranslatorImpl.determineServiceRequestStatus` derives the original's FHIR
    `status` purely from `dateStopped`/`autoExpireDate`, never from `Action` -- so this status
    check is action-agnostic by construction, not by coincidence. `Action.RENEW` is not a
    counter-example: it is never referenced in `OrderServiceImpl`'s save/validation logic at all
    (zero uses outside the enum declaration, zero test coverage in openmrs-core's own suite), so it
    cannot supersede an order in this version regardless of what the preflight does or does not
    check. Full source citations and the complete action matrix:
    `tests/test_openmrs.py`'s `test_supersession_superseded_on_status_change_with_version_unchanged`
    docstring; live confirmation for both REVISE and DISCONTINUE: PROVENANCE.md's DH-011E4B §3.

    `meta.lastUpdated` does **not** advance when this mutation happens (WATERMARK-DESIGN.md §6,
    RESOLVED 2026-08-13; reconfirmed live for both actions 2026-08-31), which is exactly why the
    watermark alone can never see it -- and `meta.versionId` was live-confirmed not to advance
    either, for both actions. This function does not depend on either timestamp: it re-reads the
    original resource by id and treats *either* the identity (built the same way
    `event_from_resource` builds it for a fresh poll) or the status differing from what was
    captured at dead-letter time as proof the source has moved. `?status=` and `?identifier=` are
    both confirmed-unsupported search parameters on this server (`?replaces=` too, live-confirmed
    HTTP 400 during this same investigation), which is why this is a direct `read`, never a
    `search`.

    Only `SourcePresence.CURRENT` means "safe to retry." Every other outcome -- including a
    resource that no longer exists at all -- refuses, because none of them proves the dead-lettered
    event is still exactly what the lab would receive.
    """
    try:
        current_resource = source.read_resource(identity.resource_type, identity.resource_id)
    except Exception as error:
        return SourcePreflightResult(
            presence=SourcePresence.UNREACHABLE,
            detail=f"OpenMRS unreachable while checking the source: {type(error).__name__}",
        )

    if current_resource is None:
        return SourcePreflightResult(
            presence=SourcePresence.AMBIGUOUS,
            detail="the original resource no longer exists at the source",
        )

    try:
        current_event = event_from_resource(
            current_resource,
            resource_type=identity.resource_type,
            direction=identity.direction,
            source_system=identity.source_system,
        )
    except ResourceRejected as error:
        return SourcePreflightResult(
            presence=SourcePresence.AMBIGUOUS,
            detail=f"the current source resource could not be parsed: {error}",
        )

    if current_event.identity != identity:
        return SourcePreflightResult(
            presence=SourcePresence.SUPERSEDED,
            detail=(
                f"source now reports version {current_event.identity.resource_version!r}, not "
                f"the dead-lettered {identity.resource_version!r}"
            ),
        )

    stored_status = stored_payload.get("status")
    if current_event.status != stored_status:
        return SourcePreflightResult(
            presence=SourcePresence.SUPERSEDED,
            detail=(
                f"source status changed from {stored_status!r} to {current_event.status!r} "
                f"since the dead letter was recorded"
            ),
        )

    return SourcePreflightResult(
        presence=SourcePresence.CURRENT, detail="source identity and status unchanged"
    )
