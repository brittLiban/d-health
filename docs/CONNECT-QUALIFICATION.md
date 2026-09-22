# DHealth Connect — qualification

**Added 2026-09-22, DH-CONNECT-CONTRACT-001.** How a connector proves it satisfies
`docs/CONNECT-CONTRACT.md`, what the resulting evidence means, and — at length, because this is the
part that gets misread — what it does **not** mean. Decisions: `DECISIONS.md` ADR-056.

> **DHealth Connect transport IS NOT BUILT.** No connector exists, none has been qualified against
> a real external system, and no facility is production-routable through one. The harness described
> here runs against a synthetic connector subject that contacts nothing.

---

## What a PASS does not prove

Read this section before the others.

**A PASS does not mean the connector is working. It does not mean the connector is running. It
does not mean any external system has ever been contacted. It does not mean a facility may receive
production orders. It does not mean the connector will still satisfy the contract tomorrow, after
its configuration changes, or after the partner upgrades their LIS. It is a statement about one
build, one configuration and one mapping revision at one moment in the past, and nothing else.**

Three specific limits worth naming individually:

1. **`ORDER_RECEIPT` is declared, not exercised.** It is the only capability whose direction is
   DHealth → connector: it asks whether the connector can *receive* an order pushed to it. Pushing
   requires a transport, and there is none. So the scenario — named `LQ1-order-receipt-declared`
   rather than `LQ1-order-receipt`, so the limitation is visible in the evidence itself — records
   that the connector *claims* it can receive orders. Nobody watched it receive one. The limitation
   is carried into the stored capability row, not only into a source comment.
2. **Provider qualification is declared-only in this build.** There is a laboratory harness
   (`LabQualificationHarness`). There is no provider harness. `required_capabilities("PROVIDER")`
   can therefore only be satisfied by a directly recorded qualification, which means no provider
   connector has been meaningfully qualified by anything in this repository. Building one is future
   work, and until then a provider connector's PASS carries no more weight than the declaration
   behind it.
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
connector_operational = FALSE      ← no runtime exists, in any release yet shipped
production_routing_allowed = FALSE
```

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
