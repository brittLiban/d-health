# DHealth Connect — the integration contract

**Added 2026-09-22, DH-CONNECT-CONTRACT-001.** What any connector between an external EMR or LIS
and DHealth Exchange must obey. Decisions and their reasoning: `DECISIONS.md` ADR-056. How a
connector proves it obeys this: `docs/CONNECT-QUALIFICATION.md`. Architecture: `docs/ARCHITECTURE.md`
§22.

> **DHealth Connect has a runtime; it has no qualified vendor adapter.**
> DH-CONNECT-AGENT-001 built the generic runtime: a durable delivery engine, a service-identity
> boundary, an on-premises agent, retries, dead letters and health derived from behaviour. There is
> still no FHIR client, no HL7 parser, no MLLP listener, no SFTP runtime, no file watcher, no webhook
> receiver and **no vendor adapter of any kind** — the only adapter that exists is synthetic and
> contacts nothing. Nothing in this repository has ever contacted a real external EMR or LIS, no real
> system has been qualified, and no facility is production-routable through Connect. See
> `docs/CONNECT-AGENT.md`.

---

## What this document is for

A contract written after the first connector is a description of that connector. Written before,
it is a constraint on every one that follows — which is the point. The next three integrations
will be built by different people, possibly outside this repository, against OpenELIS, OpenMRS and
some vendor's REST API. They must mean the same things by the same words, or "DHealth-compatible"
means nothing and a clinician's confidence in a result depends on which partner happened to build
the adapter.

**Exchange is canonical.** FHIR, HL7 v2, CSV and vendor JSON are connector-side formats. None of
them is DHealth's internal model, and no connector's shape leaks into the Exchange schema. A
connector translates into the canonical messages below and is refused if it cannot.

---

## The chains that must not collapse

Each `≠` below is a distinction some integration somewhere has merged, usually by accident and
usually because merging them made a screen simpler. Every one of them is load-bearing here.

```
enrolled  ≠  configured  ≠  operationally ready  ≠  production-routable
REPORTED  ≠  DOCUMENTED  ≠  VERIFIED             ≠  qualified
qualified ≠  healthy now
sent      ≠  received    ≠  accepted
result entered ≠ entry complete ≠ professionally released ≠ delivered ≠ reviewed
original release ≠ amendment
```

The two that cause the most damage when merged:

- **A technical acknowledgement is not a business acceptance.** In HTTP both are a 200. "We stored
  your bytes" and "this laboratory will run this test" are different facts with different authors
  and different times. `TechnicalAck` and `BusinessAcceptance` are separate message types for this
  reason: a connector author who wants to say the laboratory accepted must construct a different
  object, and cannot arrive there by setting a flag.
- **Qualified is not operational.** A qualification proves a *build* satisfies this contract.
  Only a runtime reporting in proves one *is working now*. See "Operational" below.

---

## Messages

All defined in `adapter/src/daher_adapter/connect/contract.py`. Every one is `extra="forbid"` and
frozen. An unknown field is refused rather than ignored: silently dropping a field lets a partner
believe they communicated something that arrived nowhere, and nobody finds out until a result is
missing.

| Message | Direction | Means |
|---|---|---|
| `OrderSubmission` | connector → DHealth | this facility is placing an order |
| `TechnicalAck` | either | bytes received. Nothing else |
| `BusinessAcceptance` | connector → DHealth | this laboratory will do this work |
| `BusinessRejection` | connector → DHealth | this laboratory will not, and why |
| `CollectionEvent` | connector → DHealth | a specimen was collected |
| `ReleasedResult` | connector → DHealth | a professionally released result |
| `ReleasedAmendment` | connector → DHealth | a correction to an already-released result |

### The envelope

Every message travels inside an `Envelope` carrying, among others:

- `contract_version` — refused at the boundary if its **major** differs from DHealth's. There is no
  automatic carry-forward: a connector built against contract 2 is not parsed hopefully by a
  DHealth speaking contract 1.
- `source_event_id` — the connector's own id for the event, unique within
  `(organization_id, source_system)`. This is the idempotency key. The millionth connector
  numbering its events from 1 must not collide with the first, which is why the scope includes both.
- `source_occurred_at` **and** `sent_at` — when it happened, and when it was transmitted. A
  connector draining a week's backlog after an outage sends today what happened last Tuesday. One
  field cannot carry both, and collapsing them would silently rewrite a week of clinical chronology
  as having happened at reconnection.
- `lab_order_id` — DHealth's own order id. Required for every message that is about an order, and
  **never inferred from an external identifier**.

Both timestamps must be offset-aware. An instant with no offset is not a fact about when; Djibouti
is UTC+3.

---

## Identity

External identities are scoped, always, by all four of:

```
(source_organization_id, source_system, source_identity_system, source_identity_value)
```

`123` is not an identifier. `123, at this facility, from this system, in this numbering scheme` is.
Two facilities each numbering their patients from 1 are two sets of people, and the Exchange's
`PatientReference` is unique on exactly these four for the same reason.

**There is no fuzzy patient matching, and the model cannot express one.** `ScopedExternalId` has no
name, no date of birth, no phone number. Adding a fuzzy match would require adding the fields
first, which is a visible change rather than a quiet one. DHealth never matches patients by name
similarity, spelling distance, date-of-birth proximity, or any combination of demographics.

---

## Failure categories

```
TEMPORARY  REMOTE_UNAVAILABLE          → retryable
AUTHENTICATION  MAPPING  VALIDATION  CONFLICT  UNSUPPORTED_WORKFLOW  → not retryable
```

The split is the whole value. Retrying an unmappable test code forever fixes nothing and hides the
mapping problem from the person who could fix it; retrying a transient network failure is exactly
right. A connector that reports everything as `TEMPORARY` has a retry loop instead of an
integration.

---

## Fail closed on mapping

If DHealth cannot map an incoming test code to an item on the order, the message is **refused** as
`MAPPING`. It is not filed under the nearest match, not attached to the first item on the order,
and not stored unmapped for somebody to sort out later.

This is the failure most likely to happen in the field and least likely to happen in a demo,
because mappings drift *after* setup: the partner adds a test, renames a code, upgrades their LIS.
The dangerous outcome is not a refusal — it is a guess, which produces a result for a test nobody
ordered, attached to a patient who did have something ordered. The qualification harness exercises
this (scenario `LQ4-unmapped-code-fails-closed`) rather than asserting it.

---

## Capabilities

A connector declares what it can do, per capability, and is qualified per capability. There is no
`connected: true`.

> **A capability declaration says what a connector claims to implement. Only exercised evidence can
> satisfy a required production capability.**
>
> Added 2026-09-22 as a correction. The first version of this contract recorded a declaration as a
> passing required capability, which would have let a connector become production-routable on its
> own say-so the moment a runtime existed. Every capability row now carries an evidence level —
> `DECLARED` or `EXERCISED` — and the production verdict reads it. See
> `docs/CONNECT-QUALIFICATION.md` and ADR-056 decision 23.

**Required for a laboratory connector:**

| Capability | Why required |
|---|---|
| `ORDER_RECEIPT` | a laboratory that cannot receive an order cannot do the work |
| `BUSINESS_ACCEPTANCE` | without it nobody ever learns whether the laboratory took the order |
| `COLLECTION_EVENT` | **see below** |
| `RELEASED_RESULT_PUBLICATION` | the point of the integration |
| `AMENDMENT_PUBLICATION` | a laboratory that cannot correct a released result is unsafe |

Optional for a laboratory: `PROCESSING_EVENT`.

**Required for a provider connector:** `ORDER_SUBMISSION`, `RELEASED_RESULT_RECEIPT`,
`AMENDMENT_RECEIPT`. Optional: `REVIEW_REPORTING`.

### Why `COLLECTION_EVENT` is required, which was not the first answer

It was designed as optional and moved to required when the Exchange refused to cooperate — a real
product finding, not a test artifact:

`LabWorkflowRepository` refuses the `ACCEPTED → IN_PROGRESS` transition unless a
`LabSampleCollection` milestone has been recorded (DH-EX-011), and result entry refuses any order
whose status is not `IN_PROGRESS`. So a laboratory connector that cannot report collection cannot
enter a result **at all** — not "with reduced fidelity", not at all.

The consequence is stated rather than hidden: **a partner LIS with no reliable collection signal
cannot have a qualified laboratory connector under contract 1.0.** That is a real constraint on
which partners are integrable, and discovering it at qualification time is far better than
discovering it after a facility has been told it is going live.

---

## Operational

`connector_operational` is derived from a health state that is itself derived from observations —
when DHealth last heard from an agent, whether that agent could reach its source system, and how much
work is aging in its queue. **There is no column holding it, no endpoint that sets one, and no UI
control offering one.**

Before DH-CONNECT-AGENT-001 it was unconditionally false, because nothing reported in. It is now
reachable, and reaching it requires an agent to actually be running at the facility and working.

This is not a placeholder to be flipped when convenient. `production_routing_allowed` for a CONNECT
facility depends on it, so a CONNECT facility stays unroutable no matter how much qualification
evidence accumulates. It becomes reachable only when DH-CONNECT-AGENT-001 supplies an actual live
runtime and health mechanism. Faking a heartbeat to make a screen look finished would mean telling
an operator a laboratory is receiving orders when nothing is carrying them.

---

## Credentials

**DHealth Exchange stores no connector credential of any kind.** No API key, password, client
secret, private key, SFTP password or database password is accepted by any endpoint, stored in any
column, written to any log or audit event, or carried in any message defined here.

What DHealth stores instead is a `configuration_fingerprint`: a SHA-256 digest computed by the
connector's own operator over their configuration. That is enough to *detect* that a configuration
changed — which is what invalidates a qualification — without DHealth ever being a place worth
attacking for a partner's LIS password. If a non-secret handle to an externally-held credential is
ever needed, it is a handle, never the secret.

Fingerprints are stable canonical digests over sorted JSON. They are never Python's `hash()`, which
is randomized per process and would make a stored fingerprint meaningless on the next restart.

---

## What this contract does not cover

Deliberately, and each is somebody else's ticket:

- **Transport.** How a message physically travels. DH-CONNECT-AGENT-001.
- **Delivery.** Queues, retries against a live external system, ordering, backpressure, dead
  letters. DH-CONNECT-AGENT-001.
- **A durable message log.** This build records qualification evidence, not connector traffic.
- **Any real connector.** No OpenELIS, OpenMRS, FHIR or HL7 implementation exists here.

---

*Next: DH-CONNECT-AGENT-001 — Connector Runtime + Durable Correlation / Delivery Engine.*
