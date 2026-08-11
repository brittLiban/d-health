# Watermark design — how the adapter learns an order or result exists

**Status: PROPOSED. Nothing here is built.** ADR-005 requires this document to be read by a human
before any of it becomes code. That is not a formality — with polling, *"no result is lost between
the laboratory and a mother's chart"* stops being a property of the transport and becomes a
property of the logic below.

**Written:** 2026-08-11 (DH-009a) · **Evidence:** ADR-005's appended evidence section
**Covers:** the OpenMRS leg only. The OpenELIS leg is DH-009b, after DH-005.

---

## 1. Why polling at all

Both event-driven options were ruled out against the running stack, not from documentation:
FHIR `Subscription` returns HTTP 404 (`HAPI-0302: Unknown resource type`), and the `event` module's
ActiveMQ broker is `vm://` in-JVM only with no TCP connector listening. Full evidence in ADR-005.

Polling is therefore not a preference. It is what remains — and ADR-005 explicitly forbids
resolving this by picking whichever is easier, so the reasoning is recorded rather than assumed.

## 2. What the watermark is keyed on

**`meta.lastUpdated`, as reported by the FHIR API, per resource type — never a local clock.**

The adapter stores one watermark row per `(direction, resource_type)`:

| Column | Meaning |
|---|---|
| `resource_type` | `ServiceRequest` \| `DiagnosticReport` |
| `watermark_ts` | highest `meta.lastUpdated` observed **and fully processed** |
| `updated_at` | when the adapter last advanced it (diagnostics only, never used for querying) |

**Why the server's timestamp and not our own clock:** the adapter's clock and OpenMRS's clock are
different machines. Any window computed from local time can silently exclude records created during
clock skew. Every bound comes from values the server itself emitted.

Observed on a real order (`ORD-96`): `meta.lastUpdated` equals both `date_created` and
`date_activated`. The `orders` table has **no `date_changed` column** — orders are not mutated in
place; revisions and discontinuations are new rows with new timestamps, so they surface as new
records rather than as invisible edits.

## 3. How it advances

```
poll(resource_type):
    since   = watermark_ts            # inclusive lower bound, see §4
    page    = GET /R4/{type}?_lastUpdated=ge{since}&_sort=_lastUpdated&_count=50
    highest = since

    for resource in every page, in _lastUpdated order:
        if ledger.already_processed(resource.id, resource.meta.versionId):
            continue                  # duplicate — expected, not an error
        process(resource)             # idempotent; writes to the ledger on success
        highest = max(highest, resource.meta.lastUpdated)

    # advance ONLY after the whole page set is processed without error
    if no_errors:
        watermark_ts = highest
```

Three rules that matter more than the code:

1. **The watermark advances only after successful processing, never on fetch.** A crash mid-page
   re-fetches that window on restart. Duplicates are absorbed by the ledger; gaps are not
   recoverable, so the design never risks one.
2. **Advance to the highest *processed* timestamp, not to "now".** "Now" would skip anything
   created during the poll cycle.
3. **A failed item does not advance the watermark past itself.** It goes to the dead-letter table
   (DH-010) and the watermark stops at the last contiguous success. Loud, not silent.

## 4. The boundary second — why `ge`, not `gt`

Timestamps are **whole-second** (`.000`), and multiple records routinely share a second — 7 patients
shared `10:24:46` on this stack.

This is the classic watermark boundary bug, and it has only two failure directions:

| Choice | Failure mode |
|---|---|
| `gt watermark` | Any record sharing the boundary second that had not yet been written when we polled is **skipped forever** — silent data loss |
| `ge watermark` | The boundary second is re-fetched every cycle — **duplicates**, absorbed by the ledger |

**We choose `ge` and pay in duplicates.** In a system carrying lab results for pregnant women, a
duplicate that the ledger swallows is an inconvenience; a skipped result is a missed diagnosis.
Never trade toward silent loss.

(In probing, `gt` appeared to behave inclusively at second precision anyway. The design does not
rely on that: `ge` is chosen explicitly so correctness does not depend on an inferred behaviour.)

## 5. Restart mid-window

Falls out of §3 rather than needing its own mechanism: the watermark is durable (adapter Postgres),
advances only after success, and the lower bound is inclusive. A restart re-polls from the last
fully-processed timestamp and re-presents some records the ledger has already seen.

**Required property:** the ledger must be keyed on something stable across a restart —
`(resource_type, resource_id, meta.versionId)` — and the insert must be the thing that claims the
work, with a unique constraint, so two adapter instances or a restart mid-write cannot double-apply.

## 6. Back-dated and modified records — the honest gap

The hazard ADR-005 names: a record whose timestamp is older than the watermark by the time we see
it. A time-ordered watermark cannot see it, by construction.

What we know:

- **Orders reduce this risk structurally** — no `date_changed` column, so no in-place edits. A
  revision or discontinuation is a new row with a current timestamp, which the poller picks up
  normally.
- **Results are not yet characterised.** The `DiagnosticReport`/`Observation` leg comes from
  OpenELIS (DH-009b) and has not been observed at all.

**Unresolved, deliberately not guessed:** whether **voiding** an order advances its
`meta.lastUpdated`. If it does not, an order already synced to the laboratory could be voided in
OpenMRS with the poller never learning — the LIS would keep working an order the EMR considers
cancelled. Attempting `DELETE /ws/rest/v1/order/{uuid}` returned **HTTP 500** (OpenMRS wants a
discontinue order, not a delete), so this was left untested rather than half-tested.

**This is the sharpest open question in this document and it is clinical, not technical.** It needs
answering in DH-010 before the order leg is trusted, and it may need a periodic reconciliation
sweep rather than a pure watermark.

## 7. Timezone — a stated constraint, with a test

Filtering behaviour is **consistent with** the server comparing naive local timestamps. A client
that round-trips `meta.lastUpdated` unchanged stays self-consistent, so the design does exactly
that: **it never parses, normalises, or re-formats the timestamp — it stores the string the server
sent and sends it back.**

The constraint this creates: every component must run `TZ=Africa/Djibouti`. An adapter container
left on UTC would shift its window by three hours. That is one `.env` variable away from being
wrong, so it is asserted rather than trusted:

> **Startup assertion.** On boot the adapter compares its own timezone against the offset in a
> freshly-fetched `meta.lastUpdated`, and **refuses to start** on a mismatch. A three-hour window of
> silently missed lab results is not something to discover from a patient complaint.

The mechanism above is black-box inference from probe results, not a claim about `fhir2` internals.
The design is built so that it does not matter whether the inference is right — round-tripping the
server's own string is correct under either explanation.

## 8. Observable failure mode when the watermark is wrong

The point of this section: a wrong watermark must be **loud and visible**, never silent.

| Symptom | Meaning | Where it shows |
|---|---|---|
| Ledger duplicate-hit rate ≈ 100% for a cycle | Watermark stuck / not advancing | Adapter metric + log |
| Watermark older than `now - 15min` while polls succeed | Advancement broken | Health endpoint, alertable |
| Dead-letter table non-empty | Items failing; watermark deliberately held back | DH-010 dead-letter |
| Reconciliation count mismatch (below) | **Records were skipped** — the serious one | Nightly job |

**Nightly reconciliation, and why it is not optional.** Every mechanism above detects *stalling*.
None of them detects *skipping* — a skipped record leaves no trace anywhere. So once per night the
adapter counts OpenMRS `ServiceRequest`s created in the last 48 hours and compares that against
ledger entries for the same window. A mismatch is the only signal that would ever reveal silent
loss, and it is the check that lets anyone claim results are not being lost.

## 9. What must be settled before this is built

1. **Poll interval.** Not chosen here — it is a clinical-workflow question (how long may a
   prenatal order sit before the laboratory sees it?), not a technical one.
2. **Void/discontinue visibility** (§6) — the sharpest open item.
3. **Whether `labonfhir` reopens.** ADR-004 deferred it on the reasoning that the trigger was ours
   to own either way. Now that owning the trigger is demonstrably *polling*, that trade has changed
   — `labonfhir` runs inside OpenMRS and does not need one. This does not reverse ADR-004, but it
   is new information bearing directly on it, and it should be weighed before adapter work starts
   rather than after.
