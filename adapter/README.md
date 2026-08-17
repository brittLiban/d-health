# Adapter safety skeleton (DH-010)

This directory currently contains the transport-neutral safety layer only:

- a PostgreSQL processed-message ledger with a unique stable source-event key;
- explicit `processing` → `completed` or `dead_letter` transitions;
- a dead-letter table that retains the source payload inside the stack-owned database;
- append-only JSONL audit records on the `adapter-audit` named volume;
- retry with exponential random backoff for explicitly transient failures only; and
- liveness/readiness endpoints that check both PostgreSQL and the audit volume.

**DH-011E added the OpenMRS-side poller (2026-08-17), and it is deliberately not activated.**
`instants.py`, `source_events.py`, `watermark.py`, `openmrs.py` and `order_poller.py` implement the
accepted watermark design: a durable position bootstrapped once from the server's own clock
(ADR-015), a window bounded at both ends, deduplication by `(type, id, versionId)`, a completeness
check against the bundle's `total`, and **client-side ordering** — because `fhir2` 4.2.0 accepts
`_sort` and silently ignores it. Its output is a `SourceOrderEvent`, which names no target.

Nothing starts that loop. No compose service and no `app.py` path constructs an `OrderPoller`, and
there is no delivery implementation, because **BLOCKER-6 has not decided how an order reaches
OpenELIS**. So the adapter still does not map a `ServiceRequest`, call OpenELIS, return a result, or
notify WhatsApp — DH-011's target leg through DH-013. A healthy adapter proves its safety foundation
and dependencies, not the clinical round trip.

⚠️ The ledger is marked `completed` **only** after a delivery returns a target record, never on mere
observation. Marking it on observation would make every order polled before the target leg existed
look already-done, and none of them would ever be delivered.

DH-025's transport-neutral recovery core is now implemented. Every source event gets a stable,
opaque SHA-256 correlation under `urn:daher-health:adapter:source-event`; the adapter holds a
PostgreSQL advisory fence for its full process lifetime; and recovery distinguishes exactly one
target match, proven absence, and ambiguity. It completes an existing write, retries only after
proven absence, and otherwise stops without advancing state. `processing` and `dead_letter` are
never silently treated as completed duplicates.

The remaining boundary belongs to the concrete DH-011 transport: its target writer must attach the
exact correlation, and its target probe must demonstrate a search returning exactly one match or
zero matches against the chosen OpenELIS resource. No order transport exists yet, so that binding
has not been claimed or activated.

The audit model deliberately rejects unknown fields. It records identifiers and outcomes required
for incident reconstruction, but never a FHIR payload, patient name, or clinical result value.
Payloads that need operator recovery live in the access-controlled `dead_letters` database table,
not stdout or the JSONL audit file.

## Verification

The Dockerfile has a test target. DH-011E increased the suite to **94 tests** (DH-025 had 27),
covering duplicate timestamps, unordered and paged responses, restart mid-window, malformed and
missing timestamps, and the watermark refusing to advance past anything unresolved — plus DH-025's
concurrent claiming and crash points before/after the target write:

```bash
docker build --target test --tag daher-health/adapter-test:0.1.0 adapter
docker run --rm daher-health/adapter-test:0.1.0
```

Runtime verification is stronger: `docker compose up -d --build adapter` applies the Alembic
migration to the existing `adapter` database and waits for `/health/ready` to confirm both the
database and named audit volume are usable.
