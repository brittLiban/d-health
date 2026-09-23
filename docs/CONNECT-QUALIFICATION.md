# DHealth Connect — qualification

**Added 2026-09-22, DH-CONNECT-CONTRACT-001.** How a connector proves it satisfies
`docs/CONNECT-CONTRACT.md`, what the resulting evidence means, and — at length, because this is the
part that gets misread — what it does **not** mean. Decisions: `DECISIONS.md` ADR-056.

> **No real external system has been qualified.** DH-CONNECT-AGENT-001 built the runtime, and the
> suite now drives `ORDER_RECEIPT` through it end to end — but the far end is a synthetic adapter
> that contacts nothing. No vendor adapter exists, no real EMR or LIS has been qualified, and no
> facility is production-routable through Connect. See `docs/CONNECT-AGENT.md`.

---

## The rule

> **A capability declaration says what a connector claims to implement. Only exercised evidence can
> satisfy a required production capability.**

> **A qualification suite may successfully complete every scenario available to it while production
> qualification remains incomplete, because a required capability could not yet be exercised.**

Every capability row carries an evidence level:

| Level | Means |
|---|---|
| `DECLARED` | the connector says it implements this. Useful, recorded, and not proof. |
| `EXERCISED` | DHealth drove it and observed the required effect. |

Two levels, deliberately. A third — "partially exercised", "exercised in staging" — would be a
judgement call dressed as data, and every such call is somebody's opportunity to round upward.

A required capability counts toward `connector_qualified` only when it is **both** `passed` and
`EXERCISED`. A declared one yields the gap `REQUIRED_CAPABILITY_UNEXERCISED`, which is deliberately
*not* `REQUIRED_CAPABILITY_MISSING`: "you never built it" and "we have no way yet to prove what you
built" are different bug reports for different people, and today the second is DHealth's own gap.

**This was a correction.** The original ticket recorded `ORDER_RECEIPT` as passing with "declaration
only" written in prose, and the verdict counted it. Nothing was routable then, so nothing looked
wrong — and DH-CONNECT-AGENT-001 is exactly the ticket that supplies the missing runtime. That is
recorded rather than quietly fixed; see ADR-056 decision 23.

---

## What a PASS does not prove

Read this section before the others.

**A PASS does not mean the connector is working. It does not mean the connector is running. It
does not mean any external system has ever been contacted. It does not mean a facility may receive
production orders. It does not mean the connector will still satisfy the contract tomorrow, after
its configuration changes, or after the partner upgrades their LIS. It is a statement about one
build, one configuration and one mapping revision at one moment in the past, and nothing else.**

Three specific limits worth naming individually:

1. **`ORDER_RECEIPT` is declared, not exercised — so no laboratory connector in this build reaches
   production qualification.** It is the only capability whose direction is DHealth → connector: it
   asks whether the connector can *receive* an order pushed to it. Pushing requires a transport, and
   there is none. The scenario is named `LQ1-order-receipt-declared`, its row is `DECLARED`, and the
   verdict refuses to count it. The synthetic laboratory suite therefore ends at
   **`connector_qualified = false`, gap `REQUIRED_CAPABILITY_UNEXERCISED`, naming `ORDER_RECEIPT`**
   — which is more truthful than what the first version of this ticket reported.
2. ~~**Provider qualification is declared-only in this build, and therefore never qualifies.**~~
   **Superseded by DH-CONNECT-AGENT-001 (provider side), 2026-09-23.** A provider harness now
   exists (`ProviderQualificationHarness`, driven by `connect/provider_qualification.py`), and
   `ORDER_SUBMISSION`, `RELEASED_RESULT_RECEIPT` and `AMENDMENT_RECEIPT` are exercisable against the
   synthetic provider adapter through the real runtime. A provider connector can reach
   `connector_qualified = true`.

   What stayed true: a provider connector with **no runtime** still records all three as `DECLARED`
   and does not qualify, and a qualified provider facility still cannot reach `READY_FOR_ROUTING`
   (§6/§86 — the far end is synthetic). `REVIEW_REPORTING` remains `DECLARED`: it is optional,
   nothing in DHealth consumes a connector-reported review yet, and inventing a scenario for it
   would manufacture evidence for a capability no facility can use.
3. **Nothing here was run against a real EMR or LIS.** The subject is
   `SyntheticConnectorSubject`, which talks to nothing. The Exchange effects are real — real rows,
   real repositories, real constraints — but the connector side is synthetic.

---

## What a PASS does prove

That a connector, driven through a fixed set of scenarios, produced the required effects in the
real Exchange and refused the things it must refuse. Not asserted — executed. The scenarios write
real `LabOrder`, `LabResult`, `LabResultRelease` and `LabResultAmendment` rows through the same
repositories the Native workflow uses, and read them back.

The value of a *shared* harness is that the next connector cannot invent its own checklist. An
OpenELIS adapter and a vendor REST adapter pass or fail the same scenarios, so "qualified" means
the same thing for both and no partner is qualified on an easier test than a competitor.

---

## The scenarios

Laboratory bundle, in `adapter/src/daher_adapter/connect/harness.py`:

| Scenario | Proves |
|---|---|
| `NDE1-no-double-entry` | the connector does not require a human to enter the same result twice (ADR-043 Law 2, as a qualification requirement rather than a slogan) |
| `LQ1-order-receipt-declared` | *declaration only* — see above |
| `LQ2-technical-receipt-is-not-acceptance` | a `TechnicalAck` is refused as `UNSUPPORTED_WORKFLOW` and the order does **not** become ACCEPTED |
| `LQ3-business-acceptance` | a `BusinessAcceptance` does move the order to ACCEPTED |
| `Q1-exact-replay` | an identical replay has no second effect |
| `Q2-conflicting-replay` | the same `source_event_id` with different content is refused as `CONFLICT` |
| `LQ5a-collection-is-never-inferred` | processing cannot start without a recorded collection |
| `LQ5-collection-event` | a `CollectionEvent` records the milestone |
| `LQ4-unmapped-code-fails-closed` | an unmappable test code is refused as `MAPPING`, never guessed |
| `LQ6-preliminary-is-not-released` | a result the source never professionally released is refused |
| `LQ7-released-result` | a released result becomes a real `LabResultRelease` |
| `LQ8-release-replay` | replaying a release has no second effect |
| `LQ9-conflicting-release` | a different release under the same event id is a `CONFLICT` |
| `LQ10-amendment` | an amendment becomes version 2, bound to an amendment row |
| `LQ10-post-release-correction` | a correction after release goes through the amendment path |
| `LQ11-original-remains-reconstructable` | version 1 still exists and still binds to the original release |

Sixteen scenarios in total for a laboratory connector declaring every capability. The list above is
exhaustive and matches `ScenarioResult(scenario=…)` in `harness.py` name for name; a doc naming a
scenario that does not exist is the same failure as a screenshot showing a state the product cannot
produce.

A capability passes only if **every** scenario exercising it passed. Reporting a capability as
passed because two of three scenarios worked would be exactly the `connected: true` flattening the
capability model exists to avoid.

---

## The evidence

Three tables, added by migration `0027_connect_contract`:

- `connector_installations` — which build is installed where. A partial unique index
  (`WHERE status <> 'RETIRED'`) allows **one live installation per facility** while letting a
  retired one stay in place forever, so a replacement can be registered without deleting history.
- `connector_qualifications` — one row per completed attempt, **append-only**. The contract
  version, connector version, configuration fingerprint and mapping fingerprint are **copied into
  the row when the run completes**, not joined from the installation. That is what makes it
  evidence rather than a cache: it keeps saying what was true then, after the installation moves on.
- `connector_qualification_capabilities` — per capability, passed or not, with detail.

Every foreign key is `ON DELETE RESTRICT`. You cannot tidy away a facility and take its failed
attempts with it.

**A failure is never replaced by a later success.** Both rows stand. "Has this connector ever
failed, and on what" has to remain answerable before anyone trusts a later pass.

### There is no `qualified` column

Anywhere. `qualified` is derived on every read from the live rows, in
`qualification_verdict_in_session`. There is no stored boolean, no cached verdict, and — see below
— no endpoint that sets one.

---

## Why a qualification stops applying

A stored qualification applies only while the installation still matches it on **all four** of:
contract version, connector version, configuration fingerprint, mapping fingerprint. Change any
one and the verdict becomes `QUALIFICATION_STALE` while the evidence row stays exactly as it was —
still `PASSED`, still truthful, now about a configuration that is no longer installed.

That separation is the design: **evidence is a historical fact; qualification is a present-tense
question.**

The full gap vocabulary, which the API and the French UI both speak:

| Gap | Meaning |
|---|---|
| `NO_INSTALLATION` | nothing is registered here |
| `NEVER_QUALIFIED` | registered, never run |
| `LAST_ATTEMPT_FAILED` | the most recent applicable attempt failed |
| `QUALIFICATION_STALE` | the installation changed since the passing run |
| `CONTRACT_MAJOR_MISMATCH` | built against a contract DHealth does not speak |
| `REQUIRED_CAPABILITY_MISSING` | a passing run that did not cover the required bundle |
| `SUSPENDED` | the installation is suspended |
| `RETIRED` | the installation is retired |

---

## Who may qualify a connector

Nobody, by hand. There is no endpoint that records a qualification, no `[Mark connector qualified]`
control in the platform UI, and no request body anywhere that accepts `{"qualified": true}`. Two
tests assert the absence rather than trusting code review (`CX1`, `CX2` in
`adapter/tests/test_connect_api.py`), because this is precisely the sort of convenience that gets
added later by someone who needs a demo to look finished.

A qualification row is written in exactly one place: `ConnectorInstallationRepository
.record_qualification`, which copies the pinned fields under a row lock and has no update path.

What a platform administrator *can* do: register an installation, record that its configuration
changed, suspend, restore and retire it. All platform-admin only — including against the facility's
own administrator, because which build DHealth trusts at a partner site is DHealth's record, and a
facility writing it would be a facility grading its own integration.

---

## Qualified ≠ routable

The state this whole ticket is built around, and the one an operator will most want to misread:

```
connector_qualified   = TRUE
connector_operational = FALSE      ← no agent is reporting in for this facility
production_routing_allowed = FALSE
```

DH-CONNECT-AGENT-001 made the first line reachable: the synthetic laboratory suite now exercises
`ORDER_RECEIPT` through the real delivery path and reaches `connector_qualified = true` — the first
time anything in this codebase has. It also made the second line reachable, by running an agent.

**And a facility with all three of those satisfied is still not production-routable**, because
routing additionally requires a qualified *vendor* adapter and none exists. That refusal lives in
`exchange/facility_enrollment.py` and is pinned by
`test_RQ8_qualified_is_still_not_production_routable`.

Before that ticket, nothing reached even the first line. After the evidence-level correction and
before the runtime, the best a real CONNECT laboratory could reach was:

```
suite de qualification     RÉUSSIE     ← every scenario the suite could run behaved correctly
connector_qualified        FALSE       ← ORDER_RECEIPT is DECLARED, not EXERCISED
connector_operational      FALSE       ← no runtime reports in
production_routing_allowed FALSE
```

Three separate falsehoods would have to be introduced to route a connector today, not one.

Both findings are emitted for every CONNECT facility, and both are blocking. The screen states them
as two separate lines rather than one badge, because they are two separate facts and the second
cannot be satisfied by anything an operator does today.

---

## Negative controls

Per §71, the harness was broken on purpose and the suite re-run, to check the scenarios catch what
they claim to. Results are recorded in `PROJECT_STATE.md` under DH-CONNECT-CONTRACT-001. One of the
five found a genuine defect — `LQ2` was asserting the order's status *before* sending anything, so
it passed regardless of how the bridge handled a `TechnicalAck`. It now sends one. That is the
argument for running negative controls rather than reading green ticks.

---

*Next: DH-CONNECT-AGENT-001 — Connector Runtime + Durable Correlation / Delivery Engine.*
