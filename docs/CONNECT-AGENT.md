# DHealth Connect Agent — runtime, delivery, and what it does not make true

**DH-CONNECT-AGENT-001.** This document describes the generic DHealth Connect runtime: the durable
delivery engine on the server, the on-premises agent that drains it, and the identity boundary
between them.

Read [`CONNECT-CONTRACT.md`](CONNECT-CONTRACT.md) first — it defines the messages. This document is
about how they travel. Read [`CONNECT-QUALIFICATION.md`](CONNECT-QUALIFICATION.md) for what
"qualified" means; it is a different question from everything below.

---

## The one-paragraph summary

DHealth now has a runtime. An order assigned to a CONNECT laboratory is durably queued, an agent
running on that laboratory's own hardware claims it under a lease, hands it to an adapter, and
acknowledges it; events the laboratory produces travel back the same way. The whole path is
exercised end to end by the qualification suite.

**No real laboratory or clinic is connected through it.** The only adapter that exists is synthetic.
No vendor adapter has been written, no real EMR or LIS has been qualified, and no facility is live.
A CONNECT facility still cannot reach `READY_FOR_ROUTING`, and the refusal now names the real
reason: there is no qualified vendor adapter. That is DH-CONNECT-ADAPTER-001's subject.

---

## The distinctions that must not collapse

The contract's chain gained one link:

```
DECLARED  ≠  EXERCISED  ≠  QUALIFIED  ≠  OPERATIONAL  ≠  PRODUCTION-ROUTABLE
```

* **DECLARED** — the connector says it implements a capability.
* **EXERCISED** — DHealth drove it and observed the required effect.
* **QUALIFIED** — every required capability passed *and* was exercised, under the current suite,
  against the configuration that is installed right now.
* **OPERATIONAL** — an agent is reporting in, its source system answers, and its queue is not aging.
* **PRODUCTION-ROUTABLE** — all of the above, plus a qualified **vendor** adapter. Nothing in this
  build satisfies the last condition.

Before this ticket, `ORDER_RECEIPT` could only ever be `DECLARED`: there was no way to deliver an
order, so nothing could exercise it. That is now the capability the suite proves most thoroughly,
because proving it requires the entire runtime to work.

And in the other direction: `OPERATIONAL` says nothing about `QUALIFIED`. An agent beating perfectly
in front of a connector that has never passed the suite is `HEALTHY` and unroutable. A qualified
connector whose agent stopped an hour ago is `UNAVAILABLE` and still qualified.

---

## Shape

```
  ┌──────────────── DHealth Exchange (central) ─────────────────┐
  │                                                              │
  │  order assigned ──► connector_deliveries (outbound)          │
  │                          ▲            │                      │
  │                          │            │ claim (lease)        │
  │  connect/inbound.py ◄────┤            ▼                      │
  │    applies inbound       │   /exchange/v1/connect/runtime    │
  │    through ConnectBridge │      heartbeat · claim            │
  │                          │      ack · submit                 │
  └──────────────────────────┼───────────────┬──────────────────┘
                             │               │  HTTPS, OIDC service token
                             │               │  (a second audience)
  ┌──────────────────────────┴───────────────▼──────────────────┐
  │  connect_agent (partner's own hardware)                      │
  │    store.py    SQLite inbox / outbox / receipts              │
  │    runner.py   persist ► adapter ► receipt ► acknowledge     │
  │    adapter.py  ConnectorAdapter protocol ── SyntheticLab…    │
  └──────────────────────────────────────────────────────────────┘
```

Everything vendor-specific lives behind `ConnectorAdapter`, which has five methods:
`handle_order`, `handle_result`, `handle_amendment`, `poll_events`, `probe`. The runtime never
branches on `connector_key`; a future FHIR or HL7 adapter implements that protocol and nothing in the
runtime changes.

One protocol covers both sides rather than two. A laboratory adapter refuses `handle_result` and
`handle_amendment`; a provider adapter refuses `handle_order`; each refusal is
`UNSUPPORTED_WORKFLOW`, which dead-letters in front of an operator naming which connector was asked
for what. Splitting the protocol would read better in the type system and would oblige the runtime
to know which kind of connector it is driving in order to dispatch — precisely the knowledge §3 says
it must not have. What a connector may do is decided by its declared capabilities and its
qualification, not by which methods its class happens to have.

`handle_amendment` is separate from `handle_result` for the opposite reason: a receiving system
almost certainly does something different with a correction — supersede a version, re-flag a chart,
re-notify a clinician — and an adapter that handled both identically would file a correction as a
second independent result, leaving a clinician reading both values with nothing to say which
superseded the other.

### Filing a result back into the provider's system

A `ResultNotification` carries the **provider's own** `external_order_id`, resolved through the
correlation their `ORDER_SUBMISSION` created. It is required, and the hook sends nothing when there
is no correlation — an order placed natively in DHealth by a facility that also runs a connector was
never asked for by that EMR and has no identifier there. A message carrying a DHealth id the
receiving system has never seen is one it cannot file, which is worse than no message: it looks
delivered.

The release itself always commits. A professional released a result, and refusing that clinical act
because a Connect message would not validate inverts the priority.

> **Corrected 2026-09-23 (0031).** The sentence that stood here said a notification that cannot be
> built "is logged and dropped". That was true and it was wrong: a log line is not a delivery state,
> and after a restart DHealth had no queryable answer to *which released results were never queued*.
> The behaviour is described below. The original wording is left recorded rather than edited away.

### Correlation outlives the connector that reported it

A correlation is scoped to **organization + source system + kind + external id**. The installation
that created it is kept as `created_by_connector_installation_id` — provenance, and part of no key.

This is the shape it should always have had. It was first keyed on the *installation*, which made
`ORDER-123` unresolvable the moment a facility's connector was upgraded: an installation is one
DHealth adapter build, while the identifier belongs to the partner's source system, and a connector
upgrade must not erase an external system's identity namespace.

What follows from the correct scoping:

| Situation | What happens |
| --- | --- |
| v1 retired, v2 registered for the **same** source system | v2 delivers the result for `ORDER-123`. It inherits what the identifier *means* — never v1's qualification, which it has to earn. |
| v2 registered for a **different** source system | Nothing is delivered. Epic's `ORDER-123` is not MedSys's `ORDER-123`; the obligation stays owed for a human to settle. No fuzzy handoff. |
| Same identifier in two source systems at one facility | Two distinct bindings. The namespace keeps them apart. |
| Same identifier at two organizations | Two distinct bindings. Tenant scoping is explicit and is never inferred from an external id. |

The hook asks its questions in that order, and the order is the design: released order → the
provider-side ORDER correlation → the organization, source system and their own order id → the
installation currently eligible for *that* namespace → queue. Asking the currently registered
connector who the correspondent is was the original mistake.

### What is owed, when nothing can carry it

Every path through the result hook except one leaves a durable row in
`connector_delivery_obligations`. The exception is an order placed natively in DHealth, where
nothing is owed at all.

    correlated CONNECT provider + professional release
      = a durable delivery, or a durable record that one is owed and why

| State | Meaning |
| --- | --- |
| `QUEUED` | a delivery exists for it |
| `PENDING` | nothing can carry it yet — suspended-then-retired, replaced for another source system, or none registered |
| `FAILED` | the notification could not be built, or contradicted a message already queued |

The row stores **no clinical content**. It names the `LabResultReleaseVersion` whose manifest is
already the clinical truth, and the message is rebuilt from it — which is why a recovery months
later produces the report *as released* rather than as the record looks now. Failure details carry
field names and error kinds only, never the value that failed (§57).

**Suspension is a pause, not an erasure.** The delivery is queued against the suspended
installation and simply waits: a suspended installation cannot authenticate, so nothing can claim
the message, and the moment it is restored the agent drains it. Withholding it instead would mean
the restoration had to be *noticed* by something, and anything that has to be noticed can be missed.

**Health is not a prerequisite.** A registered installation whose agent has never connected still
gets the delivery. Health decides whether *new* work should be routed to a facility; it does not
erase work already owed. The queue is what lets an agent catch up when it arrives.

**Recovery is the agent's own heartbeat.** There is no worker, broker or scheduler. The event that
makes an owed obligation dischargeable is an eligible agent appearing, and DHealth already hears
that — so a successor's first heartbeat queues what its predecessor left owed, bounded and scoped to
that facility. `reconcile_delivery_obligations` is also callable directly by an operator.

---

## Identity: the agent is not a user

A connector agent is its own principal class — `ConnectorServicePrincipal` — and deliberately not
`AuthenticatedPrincipal` with a flag. It is never CLINICIAN, LAB_TECH, LAB_RESULT_RELEASER,
ORG_ADMIN or platform admin.

**The installation comes from the credential and from nothing else:**

```
bearer token ─► signature, issuer, audience validated
             ─► azp  (the OIDC client the token was issued to)
             ─► connector_installations.service_client_id   (globally unique)
             ─► installation ─► organization
```

No request body participates. There is no `organization_id` or `connector_installation_id`
parameter on any runtime endpoint — not optional, not validated, *not present*. The envelope carries
an organization, and it is **compared** against what the credential produced; a mismatch is refused
and audited, never reconciled.

**A second audience.** Human tokens and agent tokens share an issuer and signing keys but carry
different `aud` values, validated by two separate `TokenValidator` instances. A clinician's token is
not a valid agent credential and an agent's token is not a valid clinical one. The configuration
refuses to consider the runtime configured if the two audiences are equal.

**No credentials are stored.** `service_client_id` is a public identifier — the same class of fact
as a username. The secret is issued and verified by the identity provider; DHealth never receives
it, cannot log it, and has no column that could hold it. Clearing the binding (`null`) is how an
operator stops an agent without depending on somebody also deleting the client in Keycloak: the
token stays valid and resolves to no installation, which is refused.

---

## Delivery: durable, leased, at-least-once

`connector_deliveries` holds both directions in one table. The idempotency key is
`(installation, direction, replay_scope, source_event_id)` — deliberately not `message_id`, because
an honest retry of the same event carries a new message id, which is exactly the case the key exists
to collapse.

**Claim leases, not locks.** An agent claims a bounded batch and DHealth stamps a deadline. Nothing
has to be released: a claim expires. An agent that dies holding work blocks that work for one lease
and then it is claimable again — no operator gesture, no cleanup job. On PostgreSQL the claim uses
`FOR UPDATE SKIP LOCKED`, which is what makes two concurrent agents safe rather than merely
unlikely.

> SQLite ignores `FOR UPDATE SKIP LOCKED` silently. Every claim test in the fast suite would pass
> with that clause deleted, so the concurrency property is tested against real PostgreSQL only
> (`tests/integration/test_dhconnect002_postgres_runtime.py::test_PG12…`).

**Retry, then stop.** A retryable failure backs off exponentially to a two-minute ceiling; a
terminal one (mapping, validation, conflict, unsupported workflow, authentication) dead-letters
immediately, because retrying it produces the identical failure and spends a budget that exists for
network trouble. Five attempts, then a dead letter.

**A dead letter is not a deletion and not a cancellation.** It is an unresolved row an operator has
to look at. DHealth does not invent a cancellation on a partner's behalf, and an agent going offline
never causes an already-assigned order to be cancelled.

**The payload is stored, not reconstructed.** A fingerprint proves a payload was seen, not what it
said — a queue that can only fingerprint cannot redeliver. The payload is clinical data: it lives in
the Exchange database, inside the same security boundary as the order it references, and is never
copied into audit rows, operational telemetry or structured logs. Retention follows the `LabOrder`
it references.

---

## The agent's own durability

Stdlib `sqlite3`, one file on the partner's hardware. No server, and **no requirement for partner
PostgreSQL**. The schema advances through an ordered, append-only `MIGRATIONS` list guarded by a
`schema_version` row — not a second Alembic environment, because an agent is upgraded by replacing a
binary rather than by someone running a migration tool over SSH. A store written by a *newer* agent
is refused rather than rewritten: those rows are undelivered clinical messages.

The cycle, and why the order is what it is:

```
1. drain owed acknowledgements   work already done that DHealth has not been told about
2. heartbeat                     report facts; learn what DHealth currently thinks
3. claim                         a bounded batch, under a lease
4. per message                   persist ► hand to adapter ► record receipt ► acknowledge
5. poll the adapter              whatever the partner's system produced
6. submit                        each event, persisted before it is sent
```

Every crash window loses nothing:

| crash point | what happens |
|---|---|
| after claim, before persist | DHealth's lease expires and redelivers |
| after persist, before adapter | the row is `RECEIVED`; the next cycle retries the adapter |
| after adapter, before receipt | does not exist — receipt and state change commit together |
| after receipt, before ACK | the row is `HANDLED`; step 1 drains it, and the adapter is **not** called again |

A restarting agent keeps its `runtime_instance_id`, which is minted into its own store rather than
configured — two agents handed the same value in a config file would claim each other's leases.

---

## Health is derived, never declared

Four states, from observations only:

| state | meaning |
|---|---|
| `UNKNOWN` | DHealth has no usable observation — no agent has ever reported in, or the installation is suspended or retired |
| `HEALTHY` | heard from recently, source system reachable, backlog within bounds |
| `DEGRADED` | heard from recently and something is wrong: source system unreachable, work aging, or dead letters present |
| `UNAVAILABLE` | not heard from within the staleness window |

**There is no column holding health, no endpoint that sets one, and no UI control offering one.** A
health flag somebody can set is a health flag somebody *will* set, at 2 a.m., to make an alarm stop —
and the facility it unblocks then takes clinical work through a connector nobody has heard from.

`DEGRADED` blocks routing exactly as `UNAVAILABLE` does. A connector whose LIS is unreachable is not
"mostly fine": an order routed to it is an order that sits.

**The agent's clock decides nothing.** Staleness is measured against `last_seen_at`, which DHealth
stamps on receipt. The agent's own `reported_at` is recorded beside it so an operator can see skew,
and is never consulted for liveness.

Every threshold lives in one `RuntimeHealthPolicy`: a 30-second heartbeat interval, a 90-second
staleness window (three intervals, so a single missed beat does not flip a working facility out of
routing), a five-minute backlog age, and one dead letter.

**Heartbeats are not audited.** §64: a heartbeat is operational telemetry, not a security mutation,
and one audit row per poll would bury the handful of rows that matter. The security table records
three runtime events and no others: a service identity being set, a runtime instance connecting for
the first time, and a message refused at the trust boundary.

---

## Running an agent

```
dhealth-connect-agent run      # the delivery loop, until interrupted
dhealth-connect-agent run --once
dhealth-connect-agent status   # local queue state; contacts nothing
```

**There is no destructive subcommand, and none may be added without a Human Decision**
(`AGENTS.md`). No `purge`, no `drop`, no `reset`, no `skip`, no `force-deliver`. The reason is
specific: this store holds undelivered clinical messages that exist nowhere else on the partner's
premises, and a `purge` on a stuck queue is a command that deletes an order somebody is waiting for —
reached for at exactly the moment a stuck queue makes anyone want to try anything.
`tests/test_connect_agent.py` inspects the registered subcommands rather than trusting this
paragraph.

Configuration, all from the environment, all required except where noted:

| variable | meaning |
|---|---|
| `DHEALTH_CONNECT_SERVER_URL` | the Exchange base URL |
| `DHEALTH_CONNECT_CLIENT_ID` | the OIDC client this agent authenticates as |
| `DHEALTH_CONNECT_TOKEN` | the bearer token; held in memory only, never written or logged |
| `DHEALTH_CONNECT_ORGANIZATION_ID` | the facility — a claim the server ignores in favour of the credential |
| `DHEALTH_CONNECT_INSTALLATION_ID` | likewise |
| `DHEALTH_CONNECT_SOURCE_SYSTEM` | the partner system's name, for the replay scope |
| `DHEALTH_CONNECT_STORE_PATH` | optional; defaults to `/var/lib/dhealth-connect-agent/agent.sqlite` |
| `DHEALTH_CONNECT_POLL_SECONDS` | optional; defaults to 30 |

A missing value is a startup failure naming the variable, not a half-configured process that looks
deployed and does nothing. There is no `verify_ssl=false` escape hatch and no way to disable the
credential.

Server side, `EXCHANGE_CONNECT_RUNTIME_AUDIENCE` must be set and must differ from
`EXCHANGE_OIDC_AUDIENCE`. Unset, every runtime endpoint fails closed with a 401 — correct rather
than limiting: a deployment that has provisioned no agent credentials has no agents.

---

## Upgrades and downgrades

**Queued work is never deleted.** Migration `0029_connect_runtime`'s downgrade drops the runtime
tables, which is lossless only in the sense that they are new in it — an operator downgrading a
deployment with messages in flight loses them. Drain the queue before downgrading.

Three version numbers move independently, and conflating them would force a requalification for an
operational change:

* **contract version** (`1.1`) — what a message *means*. A major change requires requalification.
* **runtime protocol version** (`1`) — the shape of the four endpoints.
* **agent version** (`0.1.0`) — the binary running at a facility.

A contract *major* mismatch fails closed at the boundary: the message is refused before any clinical
processing, with a body saying requalification is required. It is never processed with a warning.

---

## What this does not make true

Stated plainly because every sentence above is about machinery that works:

* **No vendor adapter exists.** The only implementation of `ConnectorAdapter` is
  `SyntheticLabAdapter`, which contacts nothing.
* **No real EMR or LIS has been qualified.** The qualification suite proves the runtime against the
  synthetic adapter.
* **No facility is live through Connect.** `READY_FOR_ROUTING` remains unreachable for a CONNECT
  facility, and `tests/test_connect_runtime_qualification.py::test_RQ8…` pins that a fully qualified
  connector with a healthy agent is still refused.
* **The synthetic adapter is never seeded into real facility onboarding**, and the platform UI does
  not present it as anybody's production connector.

The next ticket is **DH-CONNECT-ADAPTER-001 — First Real Partner / Vendor Adapter**.
