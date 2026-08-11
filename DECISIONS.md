# Decisions — Daher Health

Architecture Decision Records. Append-only: supersede an entry with a new one rather than
editing history. If a decision here is relevant to your ticket, **follow it — do not
re-litigate it.** If you believe one is wrong, write a superseding ADR with the reasoning and
flag it; don't quietly diverge.

Status values: `ACCEPTED` · `OPEN` (decision not yet made) · `SUPERSEDED BY ADR-nnn`

---

## ADR-001 — Two database engines, not one PostgreSQL

**Date:** 2026-08-10 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** The build prompt specified one PostgreSQL instance with separate databases for
OpenMRS, OpenELIS, and Keycloak.

**Problem.** OpenMRS does not run reliably on PostgreSQL. Platform-level support exists since
2.4.0, but Liquibase changesets and module SQL (bedmanagement, initializer, FHIR) are written
against MySQL. The OpenMRS community's own integrated distribution runs MySQL, and maintainers
stated in November 2025 that PostgreSQL is not recommended. Separately, OpenELIS ships its
database as a purpose-built image (`postgres:14.4` with schema and reference data baked into
`docker-entrypoint-initdb.d`) — its connection is configurable, its provisioning is not.

**Decision.** MariaDB for OpenMRS. PostgreSQL (OpenELIS's own image) for OpenELIS, Keycloak,
and the adapter as co-tenants. Two engines, two containers.

**Consequences.** One container more than the brief. Backups must cover both engines — see
ADR-003 and the restore-drill requirement. Multi-facility later: each site keeps its own pair.

---

## ADR-002 — RapidPro dropped from v1; adapter calls WhatsApp Cloud API directly

**Date:** 2026-08-10 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** The brief specified RapidPro connected to WhatsApp Business Cloud API.

**Problem.** RapidPro is Django + Celery + Redis + its own PostgreSQL + Elasticsearch +
mailroom + courier — five to seven containers, the heaviest component in the stack, to send one
templated, content-free notification with no branching logic. Disproportionate, and it enlarges
the surface area that must be stable for 1 September.

**Decision.** A notifier module inside the adapter calls the WhatsApp Cloud API (or Twilio)
directly. RapidPro returns in v1.1/v1.2 when two-way messaging, campaigns, or SMS fallback are
actually wanted — those justify a platform; one fire-and-forget message does not.

**Consequences.** VM sizing 4 vCPU / 16 GB rather than 8/32. The notifier is a module inside the
adapter container, **not a separate service** — the architecture diagram was corrected for this.

---

## ADR-003 — WhatsApp is not "done" until real credentials run the flow

**Date:** 2026-08-10 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** No WhatsApp Cloud API account and no BSP account exist as of 2026-08-10.

**Decision.** Build against a local mock/sandbox — fully wired, exercising the real code path and
real templates. Swapping mock → sandbox → production is **`.env`-only, never a code change**. The
WhatsApp line item remains **explicitly `BLOCKED`, not `DONE`**, until real credentials have run
the flow end-to-end.

**This gate is pinned.** A passing mock is not reported as done. If 1 September arrives without
credentials, the status is `BLOCKED`. Deadline pressure does not convert a mock into a
completion.

---

## ADR-004 — Hand-roll the FHIR transport first; `labonfhir` deferred

**Date:** 2026-08-10 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** `openmrs-module-labonfhir` (v1.5.3, used by the official OpenMRS HIS distribution)
already implements the OpenMRS↔OpenELIS FHIR order/result round-trip. Ozone additionally ships
Apache Camel routes for the same integration. This prior art was surfaced before building.

**Decision.** Build the minimal custom round-trip against the native OpenMRS and OpenELIS FHIR
APIs first. Once the real message shapes are visible in practice, decide whether `labonfhir`
earns its place as a dependency.

**Reasoning.** The decision is cheap to defer and expensive to take blind. The audit ledger,
idempotency, retry supervision, and notification trigger are ours under either choice, so nothing
is wasted by starting minimal. What waiting buys is a concrete view of the payloads before
committing to a module whose compatibility becomes a constraint on every future OpenMRS upgrade.

**Revisit trigger.** After the golden flow's order/result legs pass end-to-end: record the
observed `ServiceRequest` and `DiagnosticReport` shapes and make the call.

**Consequence — see ADR-005.** `labonfhir` is not only a mapper; it is also what makes OpenMRS
*emit* the order event. Hand-rolling means we own that trigger.

---

## ADR-005 — OPEN: how the adapter learns an order exists (Subscriptions vs. polling)

**Date raised:** 2026-08-10 · **Status:** OPEN — blocks adapter implementation
**Human Decision Required:** No (technical), but **evidence must be reviewed by Liban personally**

**Question.** Does the OpenMRS `fhir2` module support outbound FHIR Subscriptions in the 3.7.x
line, or is polling the only reliable option? Same question in reverse for OpenELIS: it documents
subscription *listeners* (it consuming Task/ServiceRequest/DiagnosticReport/etc.); the outbound
direction — how it tells us a result is ready — is unconfirmed.

**Why this is not a coin flip.** If polling is the answer, the adapter needs a scheduler, a
durable watermark, and an explicit definition of "new since when" that interlocks with the
idempotency ledger. "No result is lost between the laboratory and a mother's chart" then stops
being a property of the transport and becomes a property of the watermark logic. That is
load-bearing patient-safety code.

**How this gets resolved — binding on whoever picks up the ticket:**

1. Present **primary evidence**: the specific `fhir2` module source files and documentation
   consulted, quoted, with paths. Not a conclusion of the form "I checked and it works."
2. If the answer is polling, **write the watermark design out and have it read before building
   it**: what it is keyed on, how it advances, behaviour on restart mid-window, behaviour when a
   record is back-dated or modified after the watermark passes it, and the observable failure
   mode when the watermark is wrong.

Do not resolve this by defaulting to whichever is easier to implement.

---

## ADR-006 — Partograph ships as data entry only; no decision support

**Date:** 2026-08-10 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** The brief said "enable and configure the Partograph module." No such maintained
OpenMRS module exists — what exists in the ecosystem are external tools (CommCare/mLabor,
iDeliver) that some implementers interface with OpenMRS. The brief was corrected.

**Decision.** A plain OpenMRS custom form (O3 Form Builder / form engine) capturing partograph
data points as structured, time-stamped data entry: cervical dilatation, fetal heart rate,
contractions, descent, maternal observations. Values a midwife enters, stored and displayed back
chronologically.

**Explicitly excluded — by decision, not oversight:**

- ❌ alert lines
- ❌ action lines and action-line flagging
- ❌ automated "prolonged/obstructed labour" determination
- ❌ any threshold, colour-coding, or highlight communicating a clinical judgement

**Reasoning.** These are decision support. They collide with the project's non-negotiable
constraint that this system records and routes but does not interpret. Whether this system should
ever make such determinations is a **clinical-safety call belonging to Liban and the clinical
lead at the birth centre, made explicitly and in advance.**

**Standing instruction.** A near deadline is not implicit permission to add these "helpfully."
An agent noticing that the form "obviously should" flag prolonged labour is experiencing exactly
the failure mode this ADR guards against. If sign-off is ever given it is recorded here as a
superseding ADR with a date and a name, before any such code is written.

---

## ADR-007 — Bed counts are configuration; the seed is additive-only

**Date:** 2026-08-10 · **Status:** ACCEPTED · **Decided by:** Liban

**Decision.** `MATERNITY_DELIVERY_BEDS` and `MATERNITY_POSTPARTUM_BEDS` in `.env` drive an
idempotent seed creating beds up to the configured count on boot. Changing a count is an `.env`
edit plus a restart — no rebuild, no SQL, no admin clicking.

**The seed only adds beds. It never deletes.** Lowering a count is a deliberate manual operation
documented in the runbook, not a silent destructive side effect of an `.env` typo — a bed may
have a patient or clinical history attached to it.

**Pinned.** Do not "simplify" the additive-only behaviour away under time pressure. Changing it
is on the §5 human-decision-required list.

**Placeholder status.** Committed defaults are **placeholders, not the facility's real numbers**,
marked as such in `.env.example`. Real counts replace them before go-live.

---

## ADR-008 — Reverse proxy routes by subdomain, not path prefix

**Date:** 2026-08-10 · **Status:** ACCEPTED

`emr.${BASE_DOMAIN}`, `lab.${BASE_DOMAIN}`, `id.${BASE_DOMAIN}`. Keycloak's OIDC redirect URIs and
O3's importmap plus service-worker scope both assume they own their path root; path-prefix
deployment requires per-app rewriting hacks that fail confusingly during later upgrades. Cost: one
wildcard DNS record. `${BASE_DOMAIN}` is the only thing that changes for the next facility.

---

## ADR-009 — Adapter is written in Python

**Date:** 2026-08-10 · **Status:** ACCEPTED

The brief left the language to the agent, asking which has better-maintained FHIR client
libraries. Python's `fhir.resources` provides Pydantic models generated from the FHIR R4
specification, actively maintained, validating resources at construction — for a clinical data
path that means a malformed `Observation` fails in our process with a clear error rather than
silently landing in a chart. The Node FHIR client ecosystem has no equivalent with comparable
maintenance. Stack: FastAPI + `fhir.resources` + `httpx` + SQLAlchemy + `tenacity`.

---

## ADR-010 — Keep the O3 gateway container; Caddy proxies to it

**Date:** 2026-08-10 · **Status:** ACCEPTED

O3 ships four containers, not three: backend, frontend, and an nginx **gateway** joining the SPA
and API on one origin. Caddy terminates TLS and proxies *to* the gateway rather than replacing
it — the frontend assumes the API lives at `/openmrs` on its own origin, and reimplementing that
in Caddy means maintaining O3's internal routing by hand across every upgrade. One container to
avoid owning someone else's routing rules.

---

## ADR-011 — MariaDB 10.11, following upstream's tested pairing

**Date:** 2026-08-10 · **Status:** ACCEPTED

The architecture draft specified MariaDB 11.4 (current LTS). The OpenMRS reference distribution
tests against the 10.11 line. Corrected to `mariadb:10.11.18`. Following upstream's tested pairing
beats picking the newer LTS on our own judgement, on the axis (OpenMRS + module SQL) most likely
to break subtly.

---

## ADR-012 — "Pilot-ready" and "clinically live" are different states

**Date:** 2026-08-10 · **Status:** ACCEPTED · **Decided by:** Liban

**Pilot-ready** (the 1 September target): full stack deploys with one command, golden flow works
with test patients, backups tested with a performed restore, SSO works.

**Clinically live:** pilot-ready **plus** explicit sign-off from the birth centre's clinical lead
on (a) the prenatal lab order set and (b) the partograph form fields.

**Nobody — human or agent — treats pilot-ready as authorization to use this on real patients.**
If a ticket's completion would result in real patient data flowing through the system, stop and
flag it. That is a human decision, not a technical one. Documentation uses the word
"pilot-ready" specifically, never "launched" or "in production."
