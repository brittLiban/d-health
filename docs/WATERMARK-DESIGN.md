# Watermark design — how the adapter learns an order or result exists

**Status: ACCEPTED 2026-08-12 by Liban. DH-010's ledger/dead-letter foundation is built; the
watermark and poller are not.** ADR-005 records the approval and the 15-second configurable
interval. With polling, *"no result is lost between the laboratory and a mother's chart"* stops
being a property of the transport and becomes a property of the logic below.

**Written:** 2026-08-11 (DH-009a) · **Evidence:** ADR-005's appended evidence section
**Covers:** the OpenMRS leg only. The OpenELIS leg is DH-009b, after DH-005.

---

## 1. Why polling at all

Both event-driven options were ruled out against the running stack, not from documentation:
FHIR `Subscription` returns HTTP 404 (`HAPI-0302: Unknown resource type`), and the `event` module's
ActiveMQ broker is `vm://` in-JVM only with no TCP connector listening. Full evidence in ADR-005.

Polling is therefore not a preference. It is what remains — and ADR-005 explicitly forbade
resolving this by picking whichever was easier, so the reasoning was recorded before approval
rather than assumed.

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
`date_activated`. The `orders` table has **no `date_changed` column**.

⚠️ **Do not read that as "orders are immutable"** — an earlier draft of this document did, and it
was wrong. There is no `date_changed`, but there *is* `date_stopped`, and discontinuing an order
writes it and flips the FHIR status from `active` to `completed` **without advancing
`meta.lastUpdated`**. The absence of a `date_changed` column is not the same as the absence of
mutation; it just means the mutation is invisible to a timestamp watermark. See §6.

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

The hazard ADR-005 names: a record whose state changes without its timestamp advancing, so that a
time-ordered watermark cannot see the change. This section was rewritten on 2026-08-13 after the
question was tested properly — the original text claimed orders were structurally safe from this,
which turned out to be wrong.

### RESOLVED 2026-08-13 — tested properly, and the answer is a hard design constraint

The earlier version of this section left cancellation "unresolved" on the strength of a
`DELETE /ws/rest/v1/order/{uuid}` returning HTTP 500. **That was testing the wrong verb** — OpenMRS
wants a discontinue *order*, not a delete — so it was evidence of nothing. Retested by issuing a
real discontinuation (`POST /order` with `action: DISCONTINUE` and `previousOrder`), and the result
splits into one bad half and one good half:

**❌ The original order IS mutated in place, and its `meta.lastUpdated` does NOT advance.**

| | before | after discontinuation |
|---|---|---|
| `orders.date_stopped` (DB) | `NULL` | `2026-08-13 04:55:23` |
| FHIR `status` | `active` | `completed` |
| **FHIR `meta.lastUpdated`** | `2026-08-11T14:33:33+03:00` | **`2026-08-11T14:33:33+03:00` — unchanged** |

So §2's "orders are never mutated in place" was **too strong**. They are: stopping one writes
`date_stopped` and flips the FHIR status, invisibly to a timestamp watermark. **Re-polling the
original order would never reveal that it was cancelled.** This is exactly the silent-loss shape
this document exists to prevent, and it is now confirmed rather than hypothetical.

**✅ But the discontinuation itself arrives as a new, visible resource that points at its target.**

The `DISCONTINUE` action creates a new row (`ORD-97`) with a fresh `meta.lastUpdated`, and its FHIR
`ServiceRequest` carries an explicit back-reference:

```json
"status": "unknown",
"replaces": [{"reference": "ServiceRequest/23ff7a38-…", "type": "ServiceRequest"}]
```

**Confirmed reachable by the poller**: a bare `_lastUpdated=ge…` search returns it. There is **no
implicit status filter** on this server — `?status=active` is rejected with **HTTP 400** because
`status` is not a supported search parameter for `ServiceRequest` in `fhir2` at all, so there is no
hidden default quietly excluding cancelled resources before the timestamp comparison. (That was a
real risk worth eliminating: it would have produced the same silent-loss symptom from a completely
different cause.)

### What this obliges the adapter to do

1. **Never treat an order's state as fixed once synced**, and never rely on re-seeing the original
   resource to learn it changed. The original will never come back through the watermark.
2. **Process `replaces`.** A ServiceRequest carrying `replaces` is a cancellation of the referenced
   order and must be forwarded to the laboratory as such.
3. **Do not filter on `status` when deciding what to process.** The cancellation arrives as
   `status: unknown`, which a naive "only handle active orders" rule would drop on the floor — the
   single most likely way to reintroduce this bug while believing you are being careful.

**Net effect on the design: the watermark survives, and no reconciliation sweep is needed for
cancellation specifically.** §8's nightly reconciliation stays, because it still covers the general
skipping case — but it is no longer compensating for a known hole.

### ⚠️ Still genuinely open, and higher-stakes: corrected RESULTS

Everything above characterises **orders**. It says nothing about results, and results are the more
dangerous direction: if a lab technician enters a value and then corrects it — wrong figure
transcribed, wrong patient selected — does OpenELIS represent that as a new
`DiagnosticReport`/`Observation` version with a fresh `lastUpdated`, or as an in-place amendment
that slips past the watermark exactly as a stopped order does?

**A corrected result that fails to propagate leaves a stale, wrong lab value in a mother's chart.**
That is worse than a stale order, because the order at least fails visibly at the laboratory.

**This is the first thing DH-009b must test**, with the same discipline: enter a result, amend it,
and watch what the FHIR API actually reports. Do not assume the order-leg answer generalises — it
already failed to generalise once, within the order leg itself.

## 7. Timezone — a stated constraint, with a test

Filtering behaviour is **consistent with** the server comparing naive local timestamps. A client
that round-trips `meta.lastUpdated` unchanged stays self-consistent, so the design does exactly
that: **it never parses, normalises, or re-formats the timestamp — it stores the string the server
sent and sends it back.**

The constraint this creates: every component must run `TZ=Africa/Djibouti`. An adapter container
left on UTC would shift its window by three hours.

One edge case this design does **not** have to handle: Djibouti does not observe daylight saving,
so the offset is a fixed +03:00 year-round. The startup assertion compares against a constant and
never has to reason about an offset that changes twice a year — worth stating explicitly so nobody
later "fixes" it by adding DST handling that introduces the bug it was meant to prevent. That is one `.env` variable away from being
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

## 9. Decision and remaining result-path gates

1. **Poll interval — resolved by Liban:** **15 seconds**, configurable by environment variable.
2. **Whether `labonfhir` reopens — resolved for v1:** no. Spiked 2026-08-13 — see ADR-005 Q6. It solves
   the *order* trigger (in-process event listeners, which a separate container cannot use) but
   **still polls for results** (`api/scheduler/FetchTaskUpdates.java`). So adopting it removes half
   this document, not all of it, and relocates the other half into a dependency built against
   `fhir2` **2.2.0** while we run **4.2.0**. Revisit only after the golden flow, as ADR-004 says.
3. **Liban's ADR-005 review — completed 2026-08-12.** This design is authorised for implementation.
   Discontinuation visibility is a tested implementation constraint in §6.
4. **Before the result path:** prove OpenELIS outbound delivery and characterise corrected-result
   version/timestamp behavior. A correction that does not propagate would leave a stale value in
   the OpenMRS chart.
