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
from typing import Any
from urllib.parse import quote

from daher_adapter.instants import MalformedInstant, ServerInstant, parse_instant
from daher_adapter.retry import TransientIntegrationError

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
