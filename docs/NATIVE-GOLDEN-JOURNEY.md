# The native golden journey — how to run it, and what it proves

**DHEALTH-NATIVE-MVP-1.0, 2026-09-25.** A real browser, a real Keycloak login, the real DHealth UI
behind the real Caddy edge, the real adapter, and real PostgreSQL, carrying **one synthetic
diagnostic order** from a clinic through a laboratory, through release, back to a clinician's
review — with the authorization boundaries that surround it exercised negatively.

This is **runtime qualification, not usability qualification.** It says the integrated system works
through its real boundaries. It says nothing whatever about whether a clinician finds it usable;
that is DH-USABILITY-001's gate, it is still `INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED`, and
nothing here moves it. No F1/C1/L1 session has happened.

---

## Run it

```bash
scripts/demo/up.sh                  # start the native runtime and seed the baseline
scripts/demo/qualify-journey.sh     # the browser qualification
scripts/demo/reset.sh               # restore the baseline before running it again
scripts/demo/down.sh                # stop (add -v to delete its volumes)
```

`up.sh` waits on real readiness (`docker compose --wait` against each service's own healthcheck),
not a sleep. `qualify-journey.sh` refuses to start if `https://app.<BASE_DOMAIN>` is not answering,
because a Playwright timeout thirty seconds later is a worse way to learn the same thing.

The journey leaves workflow state behind on purpose — an order, a result, a release, a review. Run
`reset.sh` between runs.

**Your machine needs `app.localhost`, `id.localhost` and `exchange.localhost` to resolve to
127.0.0.1.** Browsers special-case `*.localhost`; the operating system often does not, and neither
does the Alpine container that runs the healthcheck. On Linux add one line to `/etc/hosts`.

## What it runs

`ui/e2e/native/01-golden-journey.spec.ts`, five phases in series, each beginning with its own real
Keycloak sign-in:

| Phase | Identity | What the browser does |
| --- | --- | --- |
| A | `demo-clinician` | opens the synthetic patient, prescribes the demo analysis, routes the order to the demo laboratory, and reads the order reference off the orders table |
| B | `demo-lab-tech` | finds **that same reference** in the laboratory queue, accepts, records collection, starts analysis, fills every required template field, saves, completes entry |
| B2 | `demo-lab-tech` | has **no** `Résultats` section at all — entry and release are separate authorities (ADR-044) |
| B3 | `demo-clinician` | sees **nothing**: the values are entered and not released, so there is no result and no review obligation. `list_queue_for_provider`'s `LabOrder.status == COMPLETED` gate is what enforces that, and removing it is negative control NC-NATIVE-4 |
| C | `demo-releaser` | reviews the completed result and releases it explicitly |
| D | `demo-clinician` | sees the released version, confirms the tab says which version is current, records a review disposition against it |

then asserts the whole journey produced **no browser console errors and no failed requests**.

`ui/e2e/native/02-authorization.spec.ts` asks the server the hostile questions with **real tokens
taken from real browser logins**: an unauthenticated browser gets Keycloak rather than a workspace;
an unauthenticated API call gets 401; a clinician cannot read the laboratory's queue or its orders;
a technician cannot act as the provider; an invented organization id is refused rather than
answered with an empty list.

## Whether the suite can actually see these boundaries break

Four mutations have been applied to real product code and reverted, each to check that the test
claiming a boundary would notice its absence (`PROJECT_STATE.md` carries the diffs and the observed
failures):

| Control | Broken | Red |
| --- | --- | --- |
| NC-NATIVE-1 | the adapter expects a different OIDC audience | the journey, before any workflow proceeds |
| NC-NATIVE-2 | every `LAB_RESULT_RELEASER` assignment deleted | `PHASE C`, while entry still passes |
| NC-NATIVE-3 | `require_clinician` accepts a membership held in *another* organization | `NEG-3` — the cross-tenant read answered **200** |
| NC-NATIVE-4 | the provider review queue's `LabOrder.status == COMPLETED` gate removed | `PHASE B3` — an entered, unreleased result appeared in the clinician's list |

NC-3 is worth one line of warning: the first attempt mutated `require_membership` and **nothing went
red**, because `require_clinician` performs its own membership lookup rather than delegating.
Mutating the wrong function is how a control quietly proves nothing.

## What it does not do

* **No patient-surface phase.** The assisted-digital patient path is not exercised here. It exists
  (`ui/src/features/patient/`) and DH-PATIENT-001 qualified it under jsdom; carrying an
  order-scoped grant through a real browser is the obvious next addition and is not claimed.
* **No amendment scenario.** The journey stops at the first released version and its review.
  Version identity is asserted (the tab that says which result is current); the
  release → amendment → v2 → fresh review obligation cycle is not.
* **Nothing about a public domain or a public certificate.** `BASE_DOMAIN=localhost` means Caddy's
  internal CA. The browser is told to accept it.
* **Nothing about performance, scale, or concurrency.** One order, one user at a time.

## Rules the qualification keeps

Every clinical transition is caused by clicking the control a staff user clicks. The database is
read to assert persistence and to inspect invariants, and is **never** written to move the workflow
forward. The API is called only to ask the authorization questions the UI deliberately offers no
control for. No token is fabricated, no session injected, no auth mode bypassed, and there is no
test-only code path inside the application.

All data is synthetic: `Clinique Démo DHealth`, `Laboratoire Démo DHealth`, `Patient Démo Alpha`,
`Analyse de démonstration A`, and three `@demo.invalid` accounts whose passwords are committed in
`demo/native/demo.env` precisely because they must never resemble real credentials.
