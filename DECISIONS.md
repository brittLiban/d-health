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

### ADR-005 — evidence appended 2026-08-11 (DH-009a). **Status remains OPEN.**

Status stays `OPEN` deliberately: this ADR's own acceptance is *"reviewed by Liban personally"*, and
nothing below has been reviewed yet. What follows is evidence and a **proposed** resolution.

Scope note: every statement is about **the O3 3.7.x distribution as we deploy it** — `fhir2-4.2.0`,
`event-4.0.0`, HAPI FHIR Server 5.7.9 — not about OpenMRS in general. Gathered against the running
stack from DH-002, not from documentation.

#### Q1 — Does `fhir2` support outbound FHIR Subscriptions? **No.**

1. The server's own CapabilityStatement (`GET /ws/fhir2/R4/metadata`) advertises **24 resource
   types**, and `Subscription` is not among them:
   `AllergyIntolerance, CarePlan, Condition, DiagnosticReport, Encounter, EpisodeOfCare, Flag,
   Group, Immunization, Invoice, Location, Medication, MedicationDispense, MedicationRequest,
   Observation, OperationDefinition, Patient, Person, PlanDefinition, Practitioner, RelatedPerson,
   ServiceRequest, Task, ValueSet`
2. `GET /ws/fhir2/R4/Subscription` → **HTTP 404**, body:
   `HAPI-0302: Unknown resource type 'Subscription' - Server knows how to handle: [...]`
3. `POST /ws/fhir2/R4/Subscription` with a valid `rest-hook` subscription → same error.
4. Control on the same server, same session: `GET /ws/fhir2/R4/ServiceRequest` → **HTTP 200**.

#### Q2 — Is the `event` module a usable trigger for an out-of-process adapter? **Not as shipped.**

`event-4.0.0.omod` *is* installed, and an ActiveMQ broker really is running (visible as
`ActiveMQ Broker[localhost]`, `KahaDB Scheduler` threads in a JVM thread dump). But:

1. The only broker URL in the module's own jar (`lib/event-api-4.0.0.jar`, package
   `org/openmrs/event/`) is
   `vm://localhost?broker.persistent=true&broker.useJmx=false&broker.dataDirectory=` —
   the **in-JVM** transport. The observed connection is `vm://localhost#1-2`.
2. The module ships **no broker configuration**: `moduleApplicationContext.xml` contains an empty
   `<beans>` element, and there is no properties file or bean exposing a broker URL.
3. No TCP connector is listening. Listening ports inside the backend container are **8080, 8005**
   and one ephemeral port; **61616 (ActiveMQ's default) is refused**.

**Honest limit of this claim:** a bare `tcp://` string also appears in `event-api-4.0.0.jar`. No
property, bean, or documented setting was found that exposes the broker over TCP — but "none found"
is not "none exists". What is established is that **as configured and as shipped, a separate
adapter container cannot subscribe to it.** Using JMS would require a custom in-process Java module
that forwards events outward, which is a substantially larger commitment than this project's
adapter design assumes — and close to re-implementing what `labonfhir` already does (ADR-004).

#### Q3 — Is polling viable, and what does it key on?

Yes, and the necessary search support exists. `_lastUpdated` is advertised **and applied** on all
four relevant resources — `ServiceRequest`, `DiagnosticReport`, `Observation`, `Task`. Verified
applied, not merely accepted: `_lastUpdated=gt2030-01-01` → `total: 0` against a baseline of `52`.

Observed on a real order placed for this purpose (`ORD-96`, Haemoglobin, read back as
`ServiceRequest/23ff7a38-…`):

- `meta.lastUpdated` = `2026-08-11T14:33:33+03:00`, equal to both `date_created` and
  `date_activated` in the `orders` table.
- **The `orders` table has no `date_changed` column** (columns: `date_activated`, `date_stopped`,
  `date_created`, `date_voided`). OpenMRS orders are not updated in place — revisions and
  discontinuations are *new rows*. This materially reduces the "record modified after the watermark
  passed it" hazard for the order leg.
- Timestamps are **whole-second** (`.000`); multiple records routinely share one second (7 patients
  shared `10:24:46`).

**Correction appended 2026-08-13:** the sentence above saying orders are not updated in place was
too strong. A proper `DISCONTINUE` writes `date_stopped` and changes the original FHIR status without
advancing its `meta.lastUpdated`. It also creates a new timestamped ServiceRequest whose `replaces`
points to the original, which is how the poller can see the cancellation. The implementation must
process `replaces` and must not filter on status. Full evidence and consequences are in
`docs/WATERMARK-DESIGN.md` §6; the original statement remains here so the correction is auditable.

#### Q4 — OpenELIS outbound direction: **evidence gathered 2026-08-12 (DH-009b), after DH-005**

Superseded within this same appendix: OpenELIS was deployed at DH-005, so this half became
testable. It was originally recorded here as "not answerable yet".

**The two legs are not symmetrical, and that is the headline.** OpenELIS's FHIR endpoint is a full
**HAPI FHIR JPA server 6.6.0** (OpenMRS's `fhir2` is a module on HAPI 5.7.9). Its
CapabilityStatement advertises **146 resource types**, and unlike OpenMRS it **does** include
`Subscription`, with `create`, `update`, `delete`, `read`, `search-type`, `history` and `patch`.
`DiagnosticReport`, `Observation`, `ServiceRequest` and `Task` carry the same full interaction set.

Separately, OpenELIS ships its **own** outbound push mechanism, configured in `common.properties`
(now committed at `openelis-config/properties/common.properties`):

```
org.openelisglobal.fhir.subscriber=
org.openelisglobal.fhir.subscriber.resources=Task,Patient,ServiceRequest,DiagnosticReport,Observation,Specimen,Practitioner,Encounter
org.openelisglobal.fhir.subscriber.backup.interval=5
```

So the result leg may not need polling at all: OpenELIS can push. **That is a materially different
design from the order leg**, and it is the opposite of what the polling answer to Q1–Q3 might have
led someone to assume for both directions.

**⚠️ Limit of this evidence, stated plainly.** What is established is what the server *advertises*
and what upstream *configures*. **No subscription has been created, and no delivery has been
observed.** Proving delivery needs a receiving endpoint, which is adapter work (DH-010+), and
ADR-005 forbids building that before this review. "Advertised" is not "works" — the gap between
those two is exactly what this ADR was written to stop being papered over.

#### Q5 — How the adapter must authenticate to OpenELIS (new, not in the original ADR)

Found while verifying DH-005 and load-bearing for DH-011/DH-012: **the OpenELIS FHIR endpoint
requires mutual TLS.** Port 8443 answers, but the server sends `Request CERT` during the handshake
(`clientAuth="true"` in its `server.xml`), and the plain HTTP connector on 8080 is **commented out**
in the shipped configuration. A request without a client certificate fails at the TLS layer, which
looks like an unreachable host rather than an authentication failure — worth knowing before someone
spends an afternoon on it.

Verified working: extracting the client certificate and key from the shared keystore
(`openssl pkcs12 -legacy`, which OpenSSL 3 needs for this file) and calling
`GET https://fhir.openelis.org:8443/fhir/metadata` returns **HTTP 200** and a `CapabilityStatement`.

Consequence for the adapter: it needs a client certificate and its own trust of that keystore, and
that is a deployment concern (certificate distribution, rotation) not just a code concern. It also
interacts with DH-021 — the shipped keystore's private key is public.

#### Proposed resolution — for Liban's review, not yet adopted

**Polling, on the OpenMRS side, with the idempotency ledger as a load-bearing safety component
rather than a defensive nicety.** Two findings drive that second clause:

- `gt` at second precision behaves **inclusively** of the boundary second in testing, so the
  boundary record is **re-delivered rather than skipped**. That is the safe failure direction —
  duplicates, not silent loss — but only if something absorbs duplicates. That something is
  DH-010's ledger. "No result is lost between the laboratory and a mother's chart" therefore
  depends on the ledger, and the ledger becomes patient-safety code, not plumbing.
- Correctness is coupled to **every component running `TZ=Africa/Djibouti`**. Filtering behaviour is
  consistent with the server comparing naive local timestamps; a client that round-trips
  `meta.lastUpdated` unchanged stays self-consistent, but an adapter container running UTC would
  silently shift its window by three hours. This is one `.env` variable away from being wrong, and
  the design asserts it with a test.

  *This mechanism is black-box inference* consistent with eight probe results across two timestamp
  encodings — not a claim about `fhir2` internals.

The write direction is available: `DiagnosticReport`, `Observation` and `Task` all advertise
`create`, so the result leg is feasible over FHIR. `ServiceRequest` is **read/search only** — the
adapter can read orders from OpenMRS but cannot write them there, which is all this design needs.

Full watermark design — keying, advancement, restart mid-window, back-dating, observable failure
mode — is in [`docs/WATERMARK-DESIGN.md`](docs/WATERMARK-DESIGN.md), **written before any code, as
this ADR requires.** Nothing has been built.

#### Q6 — `labonfhir` spike (2026-08-13), feeding the ADR-004 revisit

Requested as a spike, not a re-litigation: does `labonfhir` actually fit, and does it avoid this
problem or merely relocate it? Four findings, all checkable.

**1. It is NOT in the distribution we deploy.** `/openmrs/distribution/openmrs-distro.properties`
declares `omod.fhir2=4.2.0` and nothing else FHIR-related; `/openmrs/data/modules/` contains
`fhir2-4.2.0.omod` alone. ADR-004 described `labonfhir` as "used by the official OpenMRS HIS
distribution" — true, but that is the **HIS/Ozone** distro, not the **reference application** we
run. Adopting it means adding a module our distro does not ship, which is a different risk from
"enable what is already there."

**2. Version gap, and it is not small.** `labonfhir` master (1.5.4-SNAPSHOT) builds against
**`fhir2Version` 2.2.0**. We run **fhir2 4.2.0** — two major versions ahead. Its
`require_version` of 2.6.0 is the *platform* and we satisfy that (we are on 2.7.x). Runtime
compatibility with fhir2 4.2.0 is **unverified and looks like the main risk**; nobody should assume
it works because the module is popular.

**3. Maintenance: real but not brisk.** Last release 1.5.3 on **2025-09-11** — roughly eleven
months before this spike, not "recent". Development is genuine (a Sept 2025 commit is literally
"filter out demo data", so they hit our BLOCKER-2 too), but this is not a fast-moving dependency
that will rush out fhir2 4.x support.

**4. The important one — it does BOTH, and only the half we cannot do ourselves is event-driven:**

| `labonfhir` source | Mechanism | Direction |
|---|---|---|
| `api/event/OrderCreationListener.java`, `EncounterCreationListener.java`, `LabCreationListener.java` | **In-process event listeners** | Order out → LIS |
| `api/scheduler/FetchTaskUpdates.java`, `RetryFailedTasks.java` | **Scheduled tasks — polling** | Results back ← LIS |

So `labonfhir` **does not avoid polling**; it polls for result updates exactly as our design would.
What it genuinely solves is the *order* trigger, because it runs **inside** OpenMRS and can use the
in-process event listeners that Q2 showed are unreachable from a separate container.

**Honest framing of the trade, for the ADR-004 revisit — not a decision:**

- Adopting it **would** remove our polling on the order leg (the stronger half of our design) and
  replace it with upstream's event listeners.
- It **would not** remove polling on the result leg — it relocates that watermark logic into a
  dependency we do not control, cannot audit as easily, and which is built against a two-major-
  version-older `fhir2`.
- The audit ledger, dead-letter handling and notification trigger remain ours under either choice,
  exactly as ADR-004 reasoned.

Nothing here overturns ADR-004. It sharpens what the choice actually costs, which is what the
revisit was for.

### ADR-005 — resolution accepted 2026-08-12 (2026-08-13 in Djibouti)

**Status:** ACCEPTED · **Decided by:** Liban

Liban reviewed the evidence and proposed watermark design in discussion and approved the custom
adapter direction. The earlier `OPEN` labels above are retained as history; this appended entry is
the current status of ADR-005.

**Decision.** Continue the ADR-004 minimal custom adapter for v1. On the OpenMRS order leg, poll
`ServiceRequest` every **15 seconds**, configurable by environment variable, using the inclusive
server-timestamp watermark and durable idempotency ledger in `docs/WATERMARK-DESIGN.md`.

The implementation must:

- round-trip the server's `meta.lastUpdated` value without normalising it;
- use an inclusive lower bound and advance only after contiguous successful processing;
- treat the ledger, dead-letter path, reconciliation, and timezone startup assertion as
  correctness mechanisms, not optional observability;
- process every returned status, including a `DISCONTINUE` ServiceRequest whose `replaces`
  identifies the original order; and
- never rely on the original order's `meta.lastUpdated` advancing when it is stopped.

**OpenELIS result leg.** This acceptance does not convert an advertised capability into a verified
one. Before selecting push for the result path, the adapter must demonstrate OpenELIS outbound
delivery to a receiver and characterise a corrected result's FHIR version and timestamp. Use the
outbound subscriber only if that test passes; otherwise use polling with equivalent watermark,
ledger, reconciliation, and corrected-result safeguards. A stale corrected result in OpenMRS is a
release blocker, not an acceptable degradation.

**`labonfhir`.** Do not add it for v1. It is absent from this distribution, targets `fhir2` 2.2.0
while this stack runs 4.2.0, and still polls the result leg. ADR-004's post-golden-flow revisit
remains the appropriate point to reconsider it using observed payloads and runtime evidence.

**Consequence.** DH-009 meets its acceptance criterion and no longer blocks DH-010. This decision
authorises implementation of the adapter skeleton and OpenMRS order poller; it does not claim that
either exists yet.

### ADR-005 — implementation safety note appended 2026-08-13 (DH-025)

The unique ledger claim required above prevents two workers from owning the same source event, but
does not by itself resolve a crash after the target accepted a write and before the ledger recorded
`completed`. Both “write never happened” and “write happened, completion commit did not” restart as
`processing`; replaying the second can duplicate an order, while skipping the first can lose one.

This does not change the accepted polling decision. It sharpens “advance only after contiguous
successful processing”: stale `processing` is neither success nor a completed duplicate. Before
DH-011 transport activation, DH-025 must attach stable source correlation to the target write,
query the target on recovery, retry only when absence is proven, and remain blocked/operator-visible
when the outcome is ambiguous. A timeout alone never authorises stealing the claim. Crash-point
tests immediately before and after the outbound write are part of that gate.

**Implementation evidence appended 2026-08-13.** The transport-neutral DH-025 core now implements
that rule: opaque SHA-256 source correlation, a PostgreSQL advisory fence retained for the adapter
process lifetime, and typed `found` / `absent` / `ambiguous` recovery. Exactly one target match is
completed without rewriting; only proven zero matches may retry; ambiguity and dead letters stop.
Twenty-seven tests cover concurrent claims and both crash points. A live rollback-only PostgreSQL
probe retained no synthetic row, a second process could not acquire the running adapter's fence,
and restart reacquired it.

This is not yet the whole gate: the concrete DH-011 OpenELIS writer must attach that correlation
to its selected Task/ServiceRequest, and its probe must demonstrate exactly-one and zero-match
search behavior. No target resource was invented and no order was moved while BLOCKER-3 remains.

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

---

## ADR-013 — Keycloak session lifetimes for the pilot

**Date:** 2026-08-12 (2026-08-13 in Djibouti) · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** Shared ward terminals create a real tradeoff: a short session can interrupt a
midwife's work during labour, while an overnight authenticated session can expose patient records
to the next person using the terminal. DH-007 therefore committed explicit provisional values but
did not present them as approved workflow.

**Decision.** For the pilot, use:

- **1 hour** SSO idle timeout (`ssoSessionIdleTimeout = 3600`);
- **10 hours** maximum SSO session (`ssoSessionMaxLifespan = 36000`); and
- **5 minutes** access-token lifetime (`accessTokenLifespan = 300`).

These values are already present in the committed realm and were runtime-verified during DH-007;
approval requires no configuration mutation. Revisit them after observed pilot workflow and the
shared-terminal operating procedure are available. Changing them later is a human workflow and
security decision, not an inert tuning change.

**Consequence at acceptance.** DH-007 was complete and DH-008 still had to wire and demonstrate
one login across OpenMRS and OpenELIS. ADR-014 records the later protocol evidence and current
fail-closed boundary.

---

## ADR-014 — One Keycloak realm, OpenMRS OIDC and OpenELIS SAML

**Date:** 2026-08-13 · **Status:** ACCEPTED (technical protocol constraint)

**Context.** The original plan used OIDC for both applications. OpenELIS 3.2.1.11 contains OIDC
client libraries and configuration keys, but presence is not a working login. Exact source at tag
commit `e2a6bdf7` wires OAuth success to `CustomFormAuthenticationSuccessHandler`; that handler
accepts `UserDetails`, while OIDC supplies an `OidcUser`. Login information remains null, the
handler clears authentication, and the user returns to `LoginPage`. Its SAML path instead uses
`CustomSSOAuthenticationSuccessHandler`, which is designed for the federated principal and is the
working upstream path.

The O3 3.7.x backend does not include an OIDC login module. Official OpenMRS OAuth2 Login 1.5.0
(tag commit `2770233627343a53719ec981e997d94b1d90b7ad`) supports this stack's OpenMRS Platform
version and consumes the exact role/provider claims needed by OpenMRS.

**Decision.** Keep Keycloak as the one identity provider and one browser session, but use the
working protocol for each deployed application:

- OpenMRS authenticates with OIDC through the exact official OAuth2 Login 1.5.0 release;
- OpenELIS authenticates with SAML through its native SSO success handler; and
- local form authentication remains available for break-glass operation.

`SSO_ENABLED` defaults to `false`. Disabled OpenMRS configuration persists no client secret, and
both local login paths remain usable. Keycloak realm import covers fresh databases; a non-deleting,
idempotent reconciliation job applies the same client definitions to existing realms.

**Authorization gate — Human Decision Required: Yes.** This ADR does not authorize a role map.
Keycloak's `clinician`, `lab-tech`, and `sys-admin` labels must be mapped explicitly to native roles
in both applications, and the OpenMRS-provider rule must be decided by Liban. These choices grant
clinical/laboratory access and affect attribution. Until BLOCKER-5 is resolved, the provider claim
is hard-coded false, SSO stays off, and the one-login acceptance criterion remains unverified.

**Consequence.** Protocol uniformity is traded for a path supported by the exact deployed builds.
Both protocol paths and the realm lifecycle are configuration as code, but DH-008 is not `DONE`
until the approved authorization matrix is applied and one login across both applications is
demonstrated.

---

## ADR-015 — The adapter's first watermark starts at deployment time; no historical backfill

**Date:** 2026-08-13 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** ADR-005 specifies exactly how an existing watermark advances but never said where the
*first* one comes from. BLOCKER-3 recorded three candidate boundaries — every historical order,
activation-time-forward only, or a hand-selected set of still-active existing orders plus all new
events — and refused to pick one, because the choice decides which patient orders enter the
laboratory. That is clinical/scope surface, not a scheduler default.

**Decision.** **No historical backfill.** On first start, with no persisted watermark, the adapter
initialises its watermark to the deployment instant and routes only orders at or after it. The
pilot's value is forward-going patients; this project is not digitising existing paper records.

**Implementation constraints.**

- The bootstrap instant must be **read from the OpenMRS server**, not from the adapter container's
  clock. The watermark's whole contract is comparison against server-issued `meta.lastUpdated`
  values, and a few seconds of container clock skew in the wrong direction silently drops orders.
- The bootstrap watermark is persisted on first acquisition and **never re-derived**. A restart
  reuses the stored value; re-deriving "now" on every boot would skip every order placed while the
  adapter was down.
- The inclusive lower bound of ADR-005 §4 still applies at the boundary, so an order written in the
  same instant as bootstrap is routed rather than dropped.

**Consequence, stated plainly.** An order placed in OpenMRS *before* the adapter is deployed will
never reach OpenELIS, **including one that is still active**. If such an order matters, a clinician
re-places it after deployment; the adapter will not discover it, and no operator alert marks its
absence. This is accepted deliberately. It also means the nightly reconciliation of
`WATERMARK-DESIGN.md` §8 must count only orders created at or after the bootstrap watermark —
counting from an earlier point would report a permanent false mismatch and teach operators to
ignore the one signal that reveals genuine silent loss.

**Consequence for BLOCKER-3.** Its first-sync leg is closed. The requester identity and the
cancellation state contract remain open and still block DH-011.

---

## ADR-016 — Manual result entry for v1; no analyzer interface

**Date:** 2026-08-13 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** OpenELIS ships analyzer test-mapping CSVs, and both `horiba-micros60-cbc.csv` and
`horiba-pentra60-cbc.csv` abort on every observed boot trying to give different test rows the
already-existing unique description `LYM#(Whole Blood)` (BLOCKER-4). Whether either analyzer
belongs in this facility could not be inferred from an import failure.

**Decision.** **v1 is manual result entry only.** Laboratory technicians type results into OpenELIS
by hand. No analyzer interface, and no HL7 or ASTM instrument integration, is in scope for v1 —
defer that to v1.1 or later. DH-023 is not scoped or worked now.

**Why this is not a regression.** The technicians currently write results on paper. Typing the same
values into OpenELIS is the workflow they already have, and it is what makes the result available to
the adapter, the chart, and the notification at all. An analyzer interface would be an improvement
on their current practice, not a restoration of something being taken away.

**What this decision explicitly does *not* authorize.** It does not authorize touching OpenELIS's
shipped laboratory configuration. The failing Horiba imports are **left exactly as upstream ships
them** — not disabled, not renamed, not repaired, not suppressed in the logs. Each failed import
rolls back cleanly; the live table has zero duplicate descriptions, uniqueness is preserved, and
the web and FHIR endpoints answer. Deleting or rewriting laboratory test rows to quiet a log
message is laboratory configuration work, it needs the laboratory lead, and "we are not using that
analyzer in v1" is not the same statement as "those test definitions may be removed."

**Consequence.** BLOCKER-4 closes as a *scope* decision, and DH-023 moves to deferred rather than
blocked. The import errors remain in the boot log as a known, non-blocking upstream behaviour,
recorded in `CURRENT_STATE.md` §3a so a future reader does not mistake them for new breakage. When
an analyzer interface is genuinely wanted, the conflict must be resolved with the laboratory lead's
confirmed test identities before any import fix is attempted.

---

## ADR-017 — SSO authorization matrix (role types now, named people later)

**Date:** 2026-08-14 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** BLOCKER-5 held DH-008 activation on "the SSO authorization matrix", which was treated
as a single blocker needing the staff roster. It is two things: the **role-type mapping**, which is
a policy call, and the **named individuals** in each role, which is a real-world fact. Only the
second needs the meeting. Separating them unblocks the mapping today.

An agent may not infer this mapping — it grants access to clinical records and laboratory
functions. This entry is the recorded human decision that AGENTS.md requires.

**Decision — the exact native roles, verified to exist in each deployed system:**

| Keycloak realm role | OpenMRS native role(s) | OpenMRS provider? | OpenELIS native role(s) |
|---|---|---|---|
| `clinician` | `Provider`, `Organizational: Nurse` | **yes** | — |
| `lab-tech` | — | no | `Reception`, `Results`, `Validation` |
| `sys-admin` | `Organizational: System Administrator` | no | `Global Administrator` |

**Two sub-decisions, made explicitly rather than by default:**

- **`lab-tech` receives `Validation`.** OpenELIS separates entering a result from validating and
  releasing it, and some laboratories split them so one person cannot do both. For a birth-centre
  laboratory of a few staff, the same person does both, and withholding it would leave nobody able
  to release a result. Revisit if the laboratory grows enough to separate the duties.
- **One `clinician` role, mapped to `Organizational: Nurse`.** Midwives are the primary users.
  Doctors receive the same access for the pilot rather than adding a second realm role. If scope of
  practice needs to differ in the applications, that is a new realm role and a superseding ADR.

**What still needs the meeting:** only the **names** — which staff hold which role, and their
accounts. That is a roster, not a design.

**Consequence.** This does not by itself activate SSO. Activation additionally needs the public
HTTPS origins from DH-016 (browser cookies and redirect URIs are origin-bound), so `SSO_ENABLED`
stays `false` until the real domain exists. The hardcoded `provider=false` claim is superseded by
this matrix: `clinician` now maps to a provider.

---

## ADR-018 — Order cancellation: revoke before acceptance, manual handling after

**Date:** 2026-08-14 · **Status:** ACCEPTED (interim policy, to be validated against real workflow)
· **Decided by:** Liban

**Context.** BLOCKER-3's remaining leg. ADR-005 proved the adapter will see a discontinuation as a
new `ServiceRequest` carrying `replaces`, but not what the laboratory should then do. Upstream's
reference bridge deletes only a still-`REQUESTED` Task/ServiceRequest and carries a TODO saying it
ought to revoke instead — evidence of an unresolved contract, not a default to copy.

**Decision.**

- If the laboratory has **not yet accepted or started** the test, the adapter **revokes** it in
  OpenELIS when OpenMRS discontinues the order.
- If it is **already in progress**, the adapter **does not touch it**. It is left for manual
  handling and staff communicate directly.

Automatically pulling back work that is already underway is worse than a phone call: it destroys
laboratory context and can discard a specimen already drawn.

**Explicitly interim.** This is a sensible, safe default set as policy now so DH-011 is not blocked
on it. It must be **validated against how the laboratory actually works** at the birth-centre
meeting, and this ADR superseded if practice differs.

**Implementation constraints.**

- "Accepted or started" must map to a **verified** OpenELIS status, not an assumed one. The exact
  state transition is to be confirmed against the deployed OpenELIS before DH-011 activates.
- Revocation must **retain history**. Deleting the record would remove evidence that the order ever
  existed, which is audit data.
- An order that cannot be classified as either state is **ambiguous and must fail closed**,
  operator-visible, exactly as DH-025 treats an ambiguous target.

---

## ADR-019 — Demo content is off for the pilot

**Date:** 2026-08-14 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** `OMRS_DEMO_CONTENT` defaulted to `on`, preserving the O3 reference distribution as
shipped. DH-004's new form-safety guard then found that three demo forms loaded by that switch —
`Structured SOAP note`, `Mental Health Assessment Form`, `Form Engine Cookbook` — contain an
`alert` key, computed `calculateExpression` fields, and expression validators. They are upstream
demonstration content, not maternity forms, but they are live in the database and a clinician could
open them.

**Decision.** `OMRS_DEMO_CONTENT=off` for the pilot. A system whose stated premise is that it
records and routes rather than interprets should not ship demonstration forms that compute and
alert, even incidentally.

**Consequences.** The switch selects a locally built backend target that omits the
`referenceapplication-demo` source directories. It **does not delete** concepts, forms, or data
already imported into an existing database — the existing throwaway volume keeps what it has. This
is separate from `OMRS_DEMO_PATIENTS`, which remains `off` and governs fictional patients. Any
reference vocabulary that later mapping genuinely needs must be added deliberately rather than
inherited from demo content.

---

## ADR-020 — Provisioning architecture: one VM per facility by default, deploy target as data

**Date:** 2026-08-15 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** The clinical stack is being made multi-facility, driven by a provisioning API with a
portal on top. Hospitals and labs are independently provisionable types; neither implies the other.

**Decision — topology.** **One VM per facility is the default.** It is the stronger isolation match
for ADR-001 and for the standing rule that there is never a shared multi-tenant clinical database.
A shared edge proxy across facilities means port routing, per-facility TLS termination, and one
more component whose misconfiguration could cross-contaminate two facilities' traffic. That
complexity is not justified at this number of facilities.

**But the API must not hardcode it.** The **deploy target is a field on the facility record**, not
an assumption in the provisioning logic. The API needs somewhere to deploy to; it does not need to
know whether that host is dedicated or shared. This keeps the shared-host model available later
without a rewrite — design for optionality, while building the simpler and safer thing now.

**Decision — DNS and TLS: swappable, and honestly incomplete.** No domain or DNS provider exists
yet (NEEDS LIBAN #15). The DNS step is therefore built against a **provider-agnostic interface**
(Caddy already selects DNS-challenge providers by configuration rather than code) and is left
**unconfigured and explicitly marked incomplete**. It must not appear to work against invented
values — a provisioning step that silently "succeeds" against a fake DNS provider is exactly the
false green this project has now found five times. Cloudflare is a reasonable later default (free,
real API, pairs cleanly with Caddy) but is a configuration choice, not something locked in now.

**Decision — implementation stack.** The provisioning API is **Python/FastAPI**, matching the
adapter, so the repository has one language and one test idiom rather than a second toolchain. The
portal is a **self-contained FastAPI app with server-rendered Jinja2 pages** on its own subdomain.
There is no existing website to integrate with, and inventing an integration with a site that does
not exist would be building against a guess. If a marketing site later appears in some other
framework, the portal does not need to move into it.

**Consequence.** `FACILITY_ID` (DH-026) namespaces containers, volumes and networks. Facility
records carry identity, type, and deploy target. Nothing in the API assumes co-location.

---

## ADR-021 — Provisioning trust boundary and compromise blast radius

**Date:** 2026-08-15 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** The portal runs on a general-purpose website server; the provisioning API runs on the
infrastructure side. These are different trust levels even though one person owns both. The design
question is not "how do the two talk" but **"what can an attacker do once the website server is
theirs"** — because a public web server is the likeliest thing here to be breached.

**Decision — the portal is an untrusted client.** It authenticates with a **scoped credential over
TLS**, not a shared secret pasted into both codebases. Its scope is exactly: create a facility,
list facilities, read status, rotate credentials, soft-decommission. That credential grants **no
shell, no Docker socket, no database access** on any target host, and it is rotatable without
redeploying anything.

**Decision — the API's audit log is authoritative.** Every provisioning action is recorded by the
API from what it actually did, never from what the client reported. "The portal said it succeeded"
is not evidence. The log is **append-only and written to a destination outside the API host's own
mutable filesystem**, so it survives that host being rebuilt or tampered with. A log an attacker
can edit is decoration.

**Decision — secrets are referenced, never echoed.** Per-facility secrets are generated, never
reused across facilities, and never returned inline in an API response, written to the audit log,
or included in an error or stack trace. Responses carry a **reference** to where credentials can be
retrieved, not the credentials.

### The blast-radius walkthrough — what a compromised website server actually gets

Assume the attacker has full control of the website server, including the portal's credential.

**They can:** create facilities (costing money and noise), list facilities and read their health,
backup status, and image versions, rotate a facility's credentials (disruptive — it breaks that
facility's stack until redeployed), and soft-decommission a facility (stopping its stack). All of
this is loud: every action lands in an audit log they cannot reach.

**They cannot:** read any patient record, obtain a shell on a facility host, connect to any
clinical database, retrieve existing secrets (rotation issues new ones; it does not disclose old
ones), or destroy clinical data — decommission is **soft only**, and there is deliberately no
one-call destructive delete in the standard API surface. A hard delete requires a separate,
harder-to-invoke mechanism with sign-off at the time.

**Worst realistic outcome:** availability damage and a cleanup job — facilities stopped, credentials
churned, spurious facilities created — with a complete record of it. Not a clinical data breach.
**If a future change makes that paragraph untrue, the change is wrong**, not the paragraph.

### The structural no-clinical-data guarantee

"The portal must never show clinical data" is enforced three ways, in increasing order of strength:

1. **Response schemas are closed and allowlisted** — extra fields are rejected at construction, so
   a clinical field cannot be added by accident downstream.
2. **A test asserts every response model against clinical vocabulary**, failing the build if a
   field named for a patient, result, observation, diagnosis, or specimen ever appears.
3. **The provisioning service holds no clinical database credentials at all.** It talks to
   deployment and health surfaces, never to `openmrs` or `clinlims`. It cannot read a patient
   record because it has no way to reach one — which is the only guarantee that does not depend on
   anyone continuing to be careful.

Point 3 is the real one. The first two catch mistakes; the third makes the mistake unreachable.

---

## ADR-022 — Deploy transport: pull-based host agent, outbound-only

**Date:** 2026-08-15 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** The provisioning API must cause work to happen on a facility host: write an env file,
start a stack, report health, stop a stack. How it reaches that host determines whether ADR-021's
blast-radius walkthrough stays true, so the transport was designed before it was built.

### The constraint that eliminates most designs

**Docker access is root access.** Anyone who can cause an arbitrary container spec to run can run
`docker run -v /:/host` and read every clinical database volume on that machine. So the question is
not "can an attacker get a shell" but **"can an attacker cause an arbitrary container spec to run."**

That has a hard consequence, and it is the single most important line in this ADR:

> **The compose file lives on the facility host. The API sends parameters, never a spec.**

If the API could hand a host a compose file, then "provisioning API" and "root on every clinical VM"
would be the same sentence, and every option below would collapse to the same worst case.

### Options considered

| Option | If the API host is breached | If one facility credential leaks | Inbound port on clinical host |
|---|---|---|---|
| **A. Remote Docker API/socket** | **Root-equivalent on every host** — arbitrary specs and mounts | One cert = root on that VM | Yes, high-value |
| **B. SSH push, forced command** | Can invoke the fixed script with chosen params on every host; no arbitrary commands | One key = one host | Yes, locked to one command |
| **C. Signed webhook to a host listener** | Same containment as B, **plus** a custom listener parsing untrusted input — more attack surface for the same benefit | Same as B | Yes, custom code |
| **D. Pull-based agent, outbound-only** | Can **enqueue** malicious params; nothing executes until a correct agent pulls and validates its own job | Attacker can pose as that host, still bounded by the param space | **None** |

**Rejected: A.** It makes ADR-021's walkthrough false while every test still passes — precisely the
failure mode that document exists to prevent.

**Rejected: C.** It buys B's containment while adding a bespoke network listener that parses
untrusted input. Worse surface, same benefit.

**Fallback: B**, if D's build cost ever stops being worth it.

### Decision — D

Facility hosts **poll** for jobs addressed to themselves, authenticating outbound with a per-host
token. **No clinical host exposes an inbound port.** The agent runs a fixed local script with
validated parameters; it never receives or executes a spec.

**The agent validates its own jobs.** It does not trust the queue. A job is executed only if the
target host id is its own, the verb is in its fixed set, and every parameter matches a strict
pattern. That local validation — not the transport — is the real boundary, so it is the part that
gets the most tests.

### Secrets are generated on the facility host, not by the API

Consequence of D worth stating separately: when the host pulls a provisioning job, **it generates
its own credentials locally** and writes its own env file. The API never sees a clinical credential
at all — it receives only a `credential_set_id` back.

This is a large improvement and it is why the walkthrough below can say what it says. A breached API
host does not hold, and has never held, any facility's database passwords.

### Revised blast radius

**Compromised website server (portal):** unchanged from ADR-021 — can create, read, and rotate
within its scopes, loudly, into a log it cannot reach.

**Compromised provisioning API host** — the case this ADR is about:

*Can:* enqueue jobs, including malicious parameter sets; read facility records (identity, type,
deploy target, health, backup status); deny service by flooding the queue.

*Cannot:* execute anything on a facility host — nothing runs until a correct agent pulls and
validates; supply a container spec, because the compose file is local to the host; obtain a shell or
Docker access anywhere; read any clinical credential, because it never had one; reach any clinical
database, because no route and no credential exist.

**Compromised facility host:** that facility is lost — the agent runs there and the databases are
there. **It does not spread.** The host's token authorises pulling its own queue only, and holds no
credential for any other facility.

**Residual risk, stated rather than hidden:** an attacker who owns the API can enqueue a job whose
parameters are hostile. Containment rests entirely on the agent's local validation, which is why the
agent treats every field of a job as untrusted input regardless of it having been signed.

### Bootstrapping — the one manual step that does not disappear

A host cannot poll for work until it has an identity to poll with. That first credential cannot
itself be delivered automatically without recreating the problem, so it is deliberately manual:

1. Operator provisions a bare VM.
2. Operator obtains a **single-use, short-lived enrollment token** from the API.
3. Operator runs the bootstrap command on the VM with that token.
4. The agent generates its own long-lived credential locally, registers its host id, and the
   enrollment token is consumed and cannot be reused.
5. From then on the agent polls outbound. No inbound port is ever opened.

Steps 1–3 are the irreducible manual step. Documented as such rather than presented as
"no manual steps", which would be false.

### Two escalation paths closed at the same time

1. **`deploy_target` must not be taken from an untrusted client.** It remains a field on the
   facility record (ADR-020), but its value is validated against an **API-side allowlist of
   registered hosts**. Otherwise a breached portal could point provisioning at any address it chose.
2. **A create request targeting an already-provisioned host or facility id is a hard conflict**,
   never an overwrite. Same class of bug as the `FACILITY_ID` default trap: a routine-looking
   request silently clobbering something live.
