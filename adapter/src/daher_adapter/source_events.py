"""The adapter's internal, transport-neutral view of a source order, and how a poll cohort is
assembled from what the server actually returned.

**This module names no target.** Its output is a `SourceOrderEvent`, which is where DH-011E
deliberately stops: BLOCKER-6 has not decided how an order reaches OpenELIS, and a poller that
already knew would have prejudged that decision. Nothing here imports or mentions a target
system.

Three properties are enforced, and each one exists because of something measured on the running
stack rather than anticipated:

1. **The server's order is not trusted.** `fhir2` 4.2.0 accepts `_sort` and ignores it —
   ascending, descending and a nonsense sort key returned byte-identical sequences over 50
   distinct resources (`WATERMARK-DESIGN.md` §3). So the cohort is sorted here, by
   `meta.lastUpdated` with `resource_id` as a deterministic tie-breaker.
2. **The cohort must be provably complete.** With no server-side ordering, offset paging over a
   truncated window can skip a record and leave no trace. So the unique resources received are
   counted against the bundle's own `total`, and a shortfall is a refusal rather than a
   best-effort pass.
3. **Duplicates are absorbed, not feared.** The inclusive `ge` lower bound (§4) re-delivers the
   boundary second every cycle — on this stack 8 ServiceRequests share the single second
   `10:24:58` — so deduplication by stable `(type, id, versionId)` identity is normal
   operation, not error handling.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from daher_adapter.instants import MalformedInstant, ServerInstant, parse_instant
from daher_adapter.ledger import Direction, MessageIdentity

OPENMRS_SYSTEM = "openmrs"


class CohortError(RuntimeError):
    """The cohort cannot be trusted, so the poll cycle must not advance its watermark."""


class CohortIncomplete(CohortError):
    """Fewer unique resources arrived than the server said exist in this window.

    The dangerous case, and the reason `total` is checked at all. An unordered server plus a
    truncated page set can silently omit a record; advancing the watermark over that window
    would lose an order with no error anywhere.
    """


class ResourceRejected(CohortError):
    """A resource lacks the identity or timestamp needed to process it safely."""


@dataclass(frozen=True, slots=True)
class SourceOrderEvent:
    """One order observed at the source, in the adapter's own vocabulary.

    Carries no interpretation: `status` and `replaces` are recorded exactly as the server
    reported them, and nothing here decides what they mean clinically.
    """

    identity: MessageIdentity
    last_updated: ServerInstant
    status: str | None
    # `ServiceRequest.replaces` references, verbatim. A discontinuation arrives as a NEW
    # resource pointing at the original, because stopping an order does not advance the
    # original's `meta.lastUpdated` (§6) — the original will never come back through the
    # watermark, so this back-reference is the only way the cancellation is ever visible.
    replaces: tuple[str, ...] = ()
    # Retained so a terminal failure can be dead-lettered with the payload that caused it.
    payload: Mapping[str, object] = field(default_factory=dict)

    @property
    def resource_id(self) -> str:
        return self.identity.resource_id


@dataclass(frozen=True, slots=True)
class Cohort:
    """A provably complete, deduplicated, ordered window of source events."""

    events: tuple[SourceOrderEvent, ...]
    reported_total: int
    duplicates_absorbed: int

    @property
    def highest(self) -> ServerInstant | None:
        return self.events[-1].last_updated if self.events else None


def event_from_resource(
    resource: Mapping[str, object],
    *,
    resource_type: str,
    direction: Direction = "openmrs_to_openelis",
    source_system: str = OPENMRS_SYSTEM,
) -> SourceOrderEvent:
    """Convert one FHIR resource into a `SourceOrderEvent`, or refuse it with a reason.

    ⚠️ **No status filtering happens here, and none may be added.** A discontinuation arrives
    as `status: unknown` (§6), so a rule as reasonable-looking as "only handle active orders"
    would drop cancellations on the floor. That is the single most likely way to reintroduce
    the silent-loss bug while believing you are being careful.
    """
    if resource.get("resourceType") != resource_type:
        raise ResourceRejected(
            f"expected resourceType {resource_type!r}, got {resource.get('resourceType')!r}"
        )

    resource_id = resource.get("id")
    if not isinstance(resource_id, str) or not resource_id:
        raise ResourceRejected("resource has no usable id, so it cannot be claimed idempotently")

    meta = resource.get("meta")
    if not isinstance(meta, Mapping):
        raise ResourceRejected(f"resource {resource_id!r} has no meta element")

    version = meta.get("versionId")
    if not isinstance(version, str) or not version:
        # The ledger key is (type, id, versionId). Without a version, a corrected resource
        # would be indistinguishable from the original and one of them would be swallowed as
        # a duplicate.
        raise ResourceRejected(
            f"resource {resource_id!r} has no meta.versionId; the ledger identity requires it"
        )

    try:
        last_updated = parse_instant(meta.get("lastUpdated"))
    except MalformedInstant as error:
        raise ResourceRejected(f"resource {resource_id!r}: {error}") from error

    status = resource.get("status")
    replaces = _references(resource.get("replaces"))

    return SourceOrderEvent(
        identity=MessageIdentity(
            source_system=source_system,
            direction=direction,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_version=version,
        ),
        last_updated=last_updated,
        status=status if isinstance(status, str) else None,
        replaces=replaces,
        payload=dict(resource),
    )


def _references(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    found: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            reference = item.get("reference")
            if isinstance(reference, str) and reference:
                found.append(reference)
    return tuple(found)


def assemble_cohort(
    resources: Iterable[Mapping[str, object]],
    *,
    reported_total: int,
    resource_type: str,
    direction: Direction = "openmrs_to_openelis",
    source_system: str = OPENMRS_SYSTEM,
) -> Cohort:
    """Deduplicate, verify completeness, and order a window's resources.

    Order of operations matters: dedupe first, then compare the *unique* count against
    `reported_total`, because the inclusive lower bound legitimately re-delivers records and
    counting raw entries would make a normal cycle look like a surplus.
    """
    if reported_total < 0:
        raise CohortError(f"server reported a negative total ({reported_total})")

    unique: dict[tuple[str, str, str], SourceOrderEvent] = {}
    duplicates = 0
    for resource in resources:
        event = event_from_resource(
            resource,
            resource_type=resource_type,
            direction=direction,
            source_system=source_system,
        )
        key = (
            event.identity.resource_type,
            event.identity.resource_id,
            event.identity.resource_version,
        )
        if key in unique:
            duplicates += 1
            continue
        unique[key] = event

    if len(unique) < reported_total:
        raise CohortIncomplete(
            f"server reported {reported_total} {resource_type} resources in this window but only "
            f"{len(unique)} unique resources arrived. The window is truncated and this server "
            f"does not order results, so advancing the watermark could skip a record silently. "
            f"Refusing to advance."
        )

    # Sorted here, never by the server. The tie-breaker is required, not cosmetic: whole-second
    # timestamps are routine (8 records share one second on this stack), and an unstable order
    # within a second makes "advance to the last contiguous success" non-deterministic.
    ordered = sorted(
        unique.values(), key=lambda event: (event.last_updated.sort_key, event.resource_id)
    )
    return Cohort(
        events=tuple(ordered),
        reported_total=reported_total,
        duplicates_absorbed=duplicates,
    )
