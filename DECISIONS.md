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

**Date:** 2026-08-14 · **Status:** ACCEPTED · **Decided by:** Liban · **The `clinician` row's target
mapping is superseded by ADR-017F (2026-08-26): `clinician` is now `Provider` + `Daher Clinician
Admission`, `Organizational: Nurse` excluded. The table below is preserved unchanged as the
historical record of what was originally decided and why; it is not the current target mapping.**

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

## ADR-017B — Commit a dedicated `Daher Reception` role, registration only

**Date:** 2026-08-24 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** NEEDS LIBAN #17 (ADR-017A / UI-012A) characterized, but did not choose between, two
options for which staff role may register a patient. Liban chose **Option B**: a dedicated
registration-only role, not an extension of `clinician`. This entry records that choice and its
implementation, scoped to registration only — admission stays blocked on its own separate
privilege-boundary characterization (ADR-017A's `ADMISSION_INCREMENT`, not attempted here).

**A mechanism finding this ticket depended on, measured live before writing any config:** the
Keycloak→OpenMRS role mechanism this repository actually runs on does not match `daher-realm.json`'s
own visual structure. `clinician`/`lab-tech`/`sys-admin` are defined there as **realm-level** roles,
but the `openmrs` client's only role-bearing protocol mapper (`oidc-usermodel-client-role-mapper`,
scoped to `usermodel.clientRoleMapping.clientId=openmrs`) reads only **client roles** on that one
client — realm roles never reach OpenMRS. Confirmed empirically: a fresh Keycloak user carrying only
the realm role `clinician` authenticates but lands with zero OpenMRS privileges. Realm roles remain
load-bearing only for `lab-tech`, via OpenELIS's separate `saml-role-list-mapper`, which (unlike the
OIDC client-role mapper) does include them. The unmodified `oauth2login` module then assigns any
OpenMRS role whose name matches a JWT `roles` claim value **exactly** — no translation table exists;
confirmed both directions live (a matching name round-trips privileges, a non-matching name produces
`authenticated:true` with zero privileges, silently, no auto-created role). Consequently the
Keycloak-side artifact for this role is a **client role on `openmrs`, named identically to the
OpenMRS role** — `Daher Reception` — not a fourth realm-level entry next to `clinician`/`lab-tech`/
`sys-admin`, which would be inert for OpenMRS and would misleadingly imply OpenELIS/SAML participation
reception never has.

**Decision — the exact artifacts committed:**

| Layer | Artifact | Contents |
|---|---|---|
| OpenMRS | Role `Daher Reception` (`openmrs-config/initializer/roles/roles-daher.csv`, Initializer-provisioned) | Exactly `Edit Patient Identifiers`, `Add Patients`, `Get Patients` — ADR-017A's proven minimum, no more |
| Keycloak | Client role `Daher Reception` on the `openmrs` client (`keycloak/reconcile-sso.sh`'s new `reconcile_client_role`, additive, idempotent) | Name-matches the OpenMRS role above; assigned to no one by this ticket |

**Explicitly not done:** no admission privilege (`ADMISSION_INCREMENT`), no `Add Encounters`, no
`Assign Beds`, no change to `clinician`/`lab-tech`/`sys-admin`'s own mappings, no realm-level
`reception` entry in `daher-realm.json`, no real staff assignment. **UI-013 (admission) stays
blocked** on its own separate characterization — this ticket does not unblock it.

**What still needs the meeting:** which real staff member(s) hold this role — a roster fact, not a
design question, same as ADR-017's own remaining gap.

---

## ADR-017D — Commit clinician-only admission authorization

**Date:** 2026-08-25 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** ADR-017C characterized, but did not choose between, three options for who may perform
admission (Visit + linked Encounter). Liban chose **Option B**: admission stays clinician-only;
reception never gains it. This entry records that choice and its implementation.

**Why, stated precisely rather than left implicit:** ADR-017C proved `Add Visits` and `Add Encounters`
are both genuinely blanket privileges in this OpenMRS distribution — no encounter-type, visit-type, or
location scoping exists anywhere in the platform's supported authorization model (confirmed by schema:
`encounter_type` carries `view_privilege`/`edit_privilege` columns but no create-privilege column;
`visit_type` carries none at all; confirmed behaviorally: an admission-capable account created an
unrelated encounter type and an unrelated visit type with identical ease to the intended ones). A role
holding either privilege can therefore write **any** of the platform's configured encounter/visit
types, not a narrow "can admit a maternity patient" capability. Because that authority is broad, it
belongs only with the clinical user class this project already trusts with broad clinical authority
(`clinician`, mapped to `Provider` + `Organizational: Nurse` per ADR-017) — not with reception, whose
entire purpose (ADR-017B) is to hold the narrowest possible privilege set. The frontend does not and
cannot narrow this; the server privilege is the only real boundary, and it is coarse. This is accepted
as a **stated platform limitation for V1**, not solved around.

**Decision — the exact artifacts committed:**

| Layer | Artifact | Contents |
|---|---|---|
| OpenMRS | Role `Daher Clinician Admission` (`openmrs-config/initializer/roles/roles-daher.csv`, Initializer-provisioned, Daher-owned name, no inherited roles) | `Get Patients`, `Get Visit Types`, `Add Visits`, `Get Visits`, `Get Visit Attribute Types`, `Get Beds`, `Get Admission Locations`, `Add Encounters`, `Get Encounters` — nine privileges, self-contained (does not depend on also holding `Daher Reception`) |
| Keycloak | Client role `Daher Clinician Admission` on the `openmrs` client (`keycloak/reconcile-sso.sh`'s existing `reconcile_client_role`, additive, idempotent) | Name-matches the OpenMRS role above; assigned to no one by this ticket |

**Explicitly not done:** `Daher Reception` is not modified and never gains any of these nine
privileges, in either direction — no inheritance path exists between the two roles. `Assign Beds` is
not granted to either role; bed assignment stays a separate, unresolved question. No real staff
assigned, no real SSO activation, no UI-013 frontend work — the role is deployable, not active.

**What still needs the meeting:** which real clinician(s) hold this role, alongside ADR-017's own
`clinician`/`lab-tech`/`sys-admin` roster gap and ADR-017B's `Daher Reception` roster gap.

---

## ADR-017E — OPEN: no supported Keycloak mechanism found for a conditional provider claim

**Date raised:** 2026-08-24 · **Status:** SUPERSEDED by ADR-017E2 (2026-08-25) — see below. The
finding stands (no config-only Keycloak mechanism exists); ADR-017E2 resolves it with a patch
instead, which was outside this entry's own original scope ("not application code or a BFF").
**Human Decision Required:** Yes — this blocks real SSO activation of any clinician identity.

**Context.** ADR-017D closed with two known gaps explicitly out of scope for that ticket: (1)
`Organizational: Nurse` inherits `Privilege Level: High` (tracked below as ADR-017F), and (2)
`keycloak/mappers/openmrs-provider-disabled-mapper.json` still hardcodes `provider=false` for every
OpenMRS OIDC login, despite ADR-017's own text saying clinician now maps to a provider. This ticket
(DH-008G) was scoped to fix gap (2): make the `provider` claim conditional — true only for the
clinician identity, false for reception/lab-tech/sys-admin — using Keycloak's existing supported
protocol-mapper mechanism, not application code or a BFF.

**Mechanism understood first, per the ticket's own requirement.** Read the pinned `oauth2login`
1.5.0 source directly (`OAuth2LoginController.java`, `UserInfo.java`), not just its docs:
`activateProviderAccount` is safely idempotent per-Person — it calls `ps.getProvidersByPerson()`
before creating, so repeated login never duplicates a Provider, and correctly links to the
authenticated user's own Person. The provider claim is read via
`userInfo.getString(UserInfo.PROP_PROVIDER, "true")` — **a genuinely absent claim defaults to
`true`** (fail-open), not false. That default is the load-bearing fact behind everything below.

**What was tried, in a disposable environment (`kc-ui009` / `openmrs-adr017d2`), never production:**

1. **`oidc-usermodel-client-role-mapper`, `multivalued=false`.** Hypothesis: emits a boolean signal
   keyed to whether the user holds the `Provider` client role. Disproven live: it emits one arbitrary
   role name from whatever client roles the user holds, non-deterministically when multiple are held.
   A synthetic user holding both `Provider` and `Daher Clinician Admission` — the real clinician
   identity's actual shape per ADR-017D — got `"Daher Clinician Admission"`, not `"Provider"`, as the
   claim value. It also never produces the literal string `"true"` even for a user holding only
   `Provider`, since the mapper emits the role's *name*, not a computed boolean.
2. **`oidc-usermodel-attribute-mapper`, reading a per-user Keycloak attribute (`daher_provider`).**
   Works correctly when the attribute is explicitly set (`probe-with-provider`, attribute `true`, got
   a correctly-linked Provider). But when the attribute is **unset** — the case for every account not
   individually configured — the mapper omits the claim entirely, and the absent-claim default in
   `oauth2login` fires: a synthetic `Daher Reception`-only user with no attribute set was granted a
   real `currentProvider`. **Confirmed by live login, not inferred from config.** A "reconcile writes
   the attribute onto every managed user" pattern was considered and rejected: any user created
   outside a reconcile run is fail-open until the next run, which is not a safety property, and it
   would be a second materialized truth source alongside the client role that already signals
   clinician identity (ADR-017B/D), which §5 of the ticket explicitly warned against inventing.
3. **Two same-claim mappers simultaneously** (a fail-closed hardcoded `provider=false` baseline
   alongside the attribute mapper, hypothesizing the per-user `true` would override the baseline
   `false`). The baseline `false` won in every trial. Two independent attempts to invert the winner —
   varying mapper creation order, then varying mapper name (alphabetical processing order) — both
   failed to flip it. Keycloak's protocol-mapper REST schema exposes no priority/precedence field for
   resolving same-claim-name collisions between mappers of different types. Without a documented,
   citable ordering contract, this behavior cannot be trusted as a foundation for a clinical safety
   property — a version upgrade or clustering difference could silently invert it.
4. Keycloak 26.7.1's full registered protocol-mapper catalog was enumerated live
   (`kcadm get serverinfo`): no role-conditional claim mapper exists. A scripted mapper is not
   available in this Keycloak version, and would in any case be borderline "application code," which
   the ticket excluded as a fix for an identity-claim configuration problem.

**Conclusion.** No mechanism found in Keycloak 26.7.1's supported protocol-mapper catalog can emit a
deterministic, fail-closed `provider` claim conditional on client-role membership, given that a real
clinician holds multiple client roles simultaneously (`Provider`, `Organizational: Nurse`, `Daher
Clinician Admission` — ADR-017D's own committed shape) and that `oauth2login` defaults an absent
claim to `true`. This is reported as a genuine platform limitation rather than compensated for in
frontend code, per the ticket's own explicit instruction.

**What was left in place.** The stale, committed `openmrs-provider-disabled-mapper.json`
(`claim.value: "false"`, unconditional) is **not changed** — it remains the safe interim posture:
every OpenMRS user, including any real clinician, currently gets `currentProvider: null`. This is
functionally wrong (no clinician can be attributed as a Provider yet) but it is fail-closed, which is
the correct side to be wrong on. The disposable Keycloak realm used for this investigation was
restored to that same single-mapper state before the session ended; two stray leftover mappers
found live on `kc-ui009` mid-investigation (a hardcoded global `provider=true`, origin unverified —
not present in this repo, and a throwaway `provider_probe` client-role probe created by this
investigation) were deleted. One disposable artifact was **not** fully cleaned up: a synthetic
`Daher Reception`-only user (`probe-without-provider`) was left holding a real, incorrectly-created
Provider record on `openmrs-adr017d2` — deleting it requires a privilege (`Get Providers`) that
reception correctly does not hold, and no admin credential was pursued to force it. This is a
disposable, non-production artifact only; it does not persist past that container's lifetime.

**Two candidate paths forward, neither implemented — this needs the meeting, not a unilateral pick:**

- **Patch `oauth2login` itself**, changing the single default-value argument at the `getString`
  call site from `"true"` to `"false"` — absent claim then defaults to *deny*, not *allow*. This is a
  one-line change to logic this project already patches (`openmrs-config/Dockerfile` already carries
  patches to this exact module for `OAuth2LoginBridgeFilter.java`). It would make the whole
  same-claim-mapper-collision problem moot, and make even a single always-present mapper safe by
  construction. Real cost: a maintained fork of upstream logic, not just config.
- **Accept a manual, audited per-user attribute** as the clinician signal (`oidc-usermodel-attribute-mapper`
  finding #2 above), with an explicit compensating control: a script that reports any OpenMRS-relevant
  Keycloak user with the `daher_provider` attribute unset, run as part of onboarding/reconciliation, so
  "unset" is a visible alarm rather than a silent fail-open. Real cost: an operational discipline
  requirement, not a config-only fix, and the ticket's own §5 already flagged a second truth source as
  something to avoid absent necessity.

**Explicitly not done:** no real SSO activation, no real staff assigned, no repo file edited, no
committed mapper changed, no CI run needed (HEAD unchanged from before this ticket started).

---

## ADR-017E2 — Role-derived, fail-closed clinician Provider identity (patches `oauth2login`)

**Date:** 2026-08-25 · **Status:** ACCEPTED · **Decided by:** Liban (via the ticket's own explicit
technical direction: patch `oauth2login` to derive Provider activation from the `roles` claim)

**Context.** ADR-017E exhausted every config-only Keycloak mechanism and found none could emit a
deterministic, fail-closed `provider` claim conditional on client-role membership — the two load
bearing facts were `oauth2login`'s own absent-claim default (`getString(PROP_PROVIDER, "true")`,
fail-*open*) and the real clinician identity's multi-role shape (`Provider` + `Organizational:
Nurse` + `Daher Clinician Admission` simultaneously, per ADR-017D), which every mapper-based
candidate either couldn't isolate deterministically or lost to under undefined same-claim-mapper
precedence. This ticket's explicit technical direction supersedes ADR-017E's own "not application
code" constraint: patch `oauth2login` itself to derive Provider activation directly from the same
`roles` claim OpenMRS role assignment already consumes — exact `"Provider"` client-role membership,
nothing else — reusing the project's existing patch/build mechanism for this exact module
(`openmrs-config/patches/oauth2login-bridge/`, first established by UI-007/UI-008 for the
`/oauth2login` dispatch bridge).

**The failed design, recorded so it is not retried** (ADR-017E, in full): `oidc-usermodel-client-
role-mapper` with `multivalued=false` emits one arbitrary role name, non-deterministically, when a
user holds multiple client roles — proven live, not assumed. `oidc-usermodel-attribute-mapper`
correctly emits `true`/`false` when a per-user attribute is explicitly set, but omits the claim
entirely when unset, and the module's absent-claim default then activates a Provider anyway — a
synthetic `Daher Reception`-only user with the attribute unset was live-confirmed to receive a real
`currentProvider`. Layering a fail-closed hardcoded-`false` baseline alongside either candidate does
not fix this: two mappers targeting the same `claim.name` have no documented precedence in
Keycloak's protocol-mapper REST schema, and two independent attempts to invert which one won (by
mapper creation order, then by mapper name) both failed — undefined behavior is not a foundation for
a clinical safety property.

**The final design — the exact rule.**

```
OpenMRS client role "Provider" (exact membership)
              │
              ▼
      Keycloak "roles" claim   (existing "client roles" mapper — unchanged)
              │
              ▼
   oauth2login's isProviderRoleGranted(UserInfo)   (new, patched)
              │
              ▼
     activateProviderAccount() / deactivateProviderAccount()   (existing, unmodified)
```

One claim (`roles`), one Java-side decision, one existing persistence mechanism. Reception,
lab-tech, and any user without the exact `Provider` role never satisfy the condition; there is no
second truth source.

**The patch — exactly what changed, and what did not.**
`openmrs-config/patches/oauth2login-bridge/OAuth2LoginController.java.patch` (unified diff against
the pinned `1.5.0` tag, commit `2770233627343a53719ec981e997d94b1d90b7ad` — the same clone/pin/patch
mechanism `config.xml.patch` already uses, extended with one more `git apply` step in
`openmrs-config/Dockerfile`) adds a package-private `isProviderRoleGranted(UserInfo)` method and a
`DAHER_PROVIDER_ROLE_NAME = "Provider"` constant, and replaces the single line in `login()` that read
`userInfo.getString(UserInfo.PROP_PROVIDER, "true")` with a call to it. The new method calls
`userInfo.getRoleNames()` (an existing, unmodified method already used for OpenMRS role sync) and
checks **exact** `List.contains` membership — never a substring, case-fold, or prefix match, so
`"Provider Assistant"` or `"SuperProvider"` cannot qualify. Any `RuntimeException` from a missing,
null, or malformed `roles` claim is caught and returns `false` — fails closed, does not propagate out
of the login flow. `activateProviderAccount`/`deactivateProviderAccount` — the persistence mechanism
itself, including its per-Person duplicate-prevention check (`getProvidersByPerson` before create)
— is untouched by this patch; only *whether* it is called changed, not *how* it behaves once called,
per the ticket's own §6 instruction.

**Live proof, disposable environment only (`kc-ui009`, `mariadb-ui009`, a fresh clone
`openmrs-adr017e2` built from the patched image `daher-health/openmrs-backend:adr017e2-test`, never
`openmrs-adr017d2` or any other JVM with prior role/provider cache state):**

- **Reproducible build.** The real `openmrs-config/Dockerfile` stage (`oauth2login-build`) built
  clean end-to-end with the patch wired in: exact pinned clone, commit assertion, both patches
  applied via `git apply`, Maven package, `.omod` produced. Independently, the exact committed patch
  file was also verified to apply via plain `patch -p1` (not just `git apply`) against a fresh
  archive of the pinned commit — two independent tools, same result, confirming the patch is a
  standard, portable unified diff.
- **Unit coverage, real `UserInfo` parsing, no mocking of that layer** — a new
  `ProviderRoleDerivationTest` (`openmrs-config/patches/oauth2login-bridge/test-verification/`,
  verification-only, not wired into the production build since the Dockerfile always skips tests):
  10 cases, all passing — `["Provider"]`→true, `["Provider","Daher Clinician Admission"]`→true,
  `["Daher Reception"]`→false, `["Organizational: Nurse"]`→false, `[]`→false, missing claim→false,
  null claim→false, malformed (non-array) claim→false (no exception escapes), `["SuperProvider"]`→
  false, `["Provider Assistant"]`→false. The existing `ProviderAttributeUserInfoTest` was updated
  (its old fixture asserted the *opposite* of the new, correct behavior) rather than left
  contradicting the shipped logic. Three pre-existing upstream integration tests
  (`KeycloakAuthenticationTest`, `GoogleAPIAuthenticationTest`, `OAuth2LoginControllerTest`) fail
  under this sandbox's JDK 17 Maven image on old CGLIB/PowerMock incompatibilities — confirmed, by a
  control run against **pristine, unmodified** upstream source under the identical toolchain, to be
  pre-existing and unrelated to this patch, not something it caused.
- **Fresh synthetic clinician, genuine OIDC round trip, never-before-authenticated, exactly the
  committed identity** (`Provider` + `Organizational: Nurse` + `Daher Clinician Admission`):
  `authenticated: true`; Keycloak user id `6dcd82af-4cf8-40d6-a9eb-1cb6faa578c5`; OpenMRS user UUID
  `04133caa-8b7f-4d89-8b01-20a349dca2aa`; Person UUID `36116ed9-13ce-425f-b438-42ce27d98331`;
  Provider UUID `f870d211-e03a-4e12-9e5c-72a021939f62`; Provider identifier
  `6dcd82af-4cf8-40d6-a9eb-1cb6faa578c5` (= the user's own `systemId`, matching `oauth2login`'s
  documented identifier choice); Provider creator `daemon` (matching its documented proxy-privileged
  creation); Provider's linked Person UUID = the authenticated user's own Person UUID, exactly;
  `currentProvider` UUID = the Provider UUID, exactly. All required equalities hold, measured, not
  assumed — even though the token's legacy `provider` claim (still `"false"` on this disposable
  realm's leftover mapper at login time) said the opposite, proving the patch genuinely ignores that
  claim rather than merely defaulting past it.
- **Repeated-login idempotence, three total logins:** `users`, `provider`, and distinct-`person_id`
  counts for this identifier were `1/1/1` before, and remained `1/1/1` after two additional logins;
  `currentProvider` UUID identical across all three. Restart-survival was **not** re-confirmed this
  ticket — blocked by this sandbox's own recurring, pre-existing `docker restart` permission denial
  (documented at the end of nearly every prior ticket this session, unrelated to this change) — a
  disclosed gap, not a fabricated pass.
- **Reception negative control, fresh user, `Daher Reception` only:** `authenticated: true`,
  `currentProvider: null`, zero Provider rows for that identifier, exactly the three approved
  registration privileges present in the session. This is the exact fail-open failure mode ADR-017E's
  attribute-mapper attempt produced — closed here.
- **Zero-role negative control, fresh user with no client role at all:** `authenticated: false` —
  `oauth2login`'s own pre-existing, unmodified role-sync/authentication logic does not complete
  authentication for a zero-role user (a `Context.authenticate` failure this patch's own decision
  method never reaches, since it only runs after `Context.isAuthenticated()` is already true); zero
  Provider rows created. Consistent with the ticket's own anticipated fallback for this case.
- **Sys-admin boundary — documented, not fabricated.** No OpenMRS client role currently exists for
  `sys-admin` at all: ADR-017's `sys-admin → Organizational: System Administrator` mapping is a
  realm-level row, inert for OpenMRS OIDC per ADR-017B's own finding, and no `reconcile_client_role`
  call for it has ever been added (only `Daher Reception` and `Daher Clinician Admission` have).
  Creating one here would be exactly the kind of roster/scope decision this ticket's own §12
  explicitly excludes ("do not manufacture unsupported sys-admin mappings"). The nearest safe,
  already-existing proxy for "broad administrative authority without the exact `Provider` role" was
  used instead: a fresh user holding only `Organizational: Nurse` (which ADR-017D already found
  carries `Privilege Level: High` — effectively the platform's full privilege set) —
  `currentProvider: null`, zero Provider rows. Broad privilege alone does not imply clinical Provider
  identity; only exact `Provider` membership does.
- **Lab-tech boundary.** Zero OpenELIS/SAML files touched this ticket (`git diff` confirms it); no
  OpenMRS client role exists for lab-tech; lab-tech does not authenticate to OpenMRS by design
  (ADR-017B).
- **Admission re-proof with the now-provider-enabled clinician:** patient → `POST /visit` (`201`,
  `Facility Visit @ Inpatient Ward`) → `POST /encounter` (`201`, `Admission`, linked via `visit`) —
  the existing UI-001A/ADR-017D payload, unmodified, `"encounterProviders": []`. Read-back (`v=full`)
  on both: `auditInfo.creator.uuid` on Visit and Encounter both equal the authenticated clinician's
  own OpenMRS user UUID — not admin, not `daemon`, not the registering reception account. Per the
  ticket's own explicit instruction, **`encounterProviders` was not populated by OpenMRS merely
  because `currentProvider` now exists**, and no provider field was added to the payload to
  compensate — recorded as-is. Whether the Admission Encounter should explicitly name the clinical
  Provider is a distinct clinical/data-contract question, not decided here.
- **Three identities, explicitly separated, not conflated:** authenticated user (the synthetic
  clinician's Keycloak/OpenMRS identity) = Visit/Encounter audit creator (same OpenMRS user UUID,
  confirmed) ≠ clinical Provider (a separate `Provider` entity, correctly *linked* to the same Person
  as the authenticated user, but not itself the audit creator of anything, and not present on the
  Encounter's own `encounterProviders` field).
- **Provider ambiguity control, isolated throwaway DB clone only (`openmrs_adr017e2_ambig`,
  never the canonical `openmrs_adr017e2`):** a second, independent, non-retired `Provider` row was
  inserted by direct SQL for the same Person as the synthetic clinician, then the clinician logged in
  against a fresh container booted on that clone. Result: `activateProviderAccount` created no third
  Provider (its existing per-Person check holds — confirmed, not merely cited); OpenMRS's own
  session-context `currentProvider` computation — unmodified by this patch, pre-existing platform
  behavior — returned **`null`**, not an arbitrary pick of either existing Provider. This is the safe
  side of ambiguity: a clinician session never silently attaches to the wrong Provider. No STOP was
  needed; the throwaway clone and its one-off container are the only artifacts affected.
- **Fresh-realm / existing-realm parity, proven, not asserted:** `keycloak/realm-config/daher-
  realm.json`'s `openmrs` client no longer carries the stale `provider disabled until approved`
  mapper (removed, alongside the `client roles` mapper, which is unchanged). `keycloak/reconcile-
  sso.sh` gained `remove_mapper_if_present` (mirrors `reconcile_mapper`'s own list-and-filter idiom,
  applied to deletion) and calls it in place of the old `reconcile_mapper` call for that mapper name.
  Run twice against the live disposable realm: run 1 removed the stale mapper (confirmed via listing:
  only `client roles` remained afterward, all four existing client roles — `Daher Reception`, `Daher
  Clinician Admission`, `Provider`, `Organizational: Nurse` — untouched); run 2 reconciled cleanly
  with no error and no change. Importing `daher-realm.json` fresh into an independent test realm
  produced the identical mapper set — exactly one mapper, `client roles`, generated ID differs (as
  expected), semantics identical. Exactly one provider-relevant mechanism exists in either path: the
  patched Java logic; zero Keycloak mappers reference a `provider` claim anywhere under `keycloak/`
  after this ticket (grepped, not assumed).

**CI guard, added and control-tested six ways.** A new `static` job step
(`.github/workflows/ci.yml`, "ADR-017E2 — Provider identity is role-derived, fail-closed, and a
single mechanism") asserts: the decision method and its call site exist in the committed patch; the
legacy fail-open call is not reintroduced on an *added* diff line (distinct from the `-` line that
legitimately still shows the old code being removed); the role constant is exactly `"Provider"`;
membership is checked via exact `List.contains`, never a substring/case-fold/prefix pattern; the
method fails closed on a caught exception; the Dockerfile applies the patch; no JSON file under
`keycloak/` mentions a `provider` claim anywhere; the stale mapper file is gone; `reconcile-sso.sh`
removes rather than recreates it. A pre-existing ADR-017D guard's blunt `"delete" in reconcile.lower()`
check — which would have false-failed on this ticket's own legitimate `remove_mapper_if_present`
mapper deletion — was narrowed to the specific dangerous shape (a *role* deletion call), since roles
may carry real staff-assignment history and mappers do not. Six deliberate breaks were run in-memory
(no file mutated) and each correctly failed the guard: reverting to the fail-open call site; making
`"Daher Reception"` the role constant; substituting a substring match; reintroducing a global
hardcoded `provider` mapper anywhere under `keycloak/`; leaving the stale mapper reconciled instead of
removed; unwiring the patch from the Dockerfile. The unmodified baseline was re-confirmed passing
after every break.

**Regressions, checked not assumed.** `git diff` shows zero changes to any role-privilege file
(`roles-daher.csv` untouched) and zero deletions of any existing client role, mapper (other than the
one superseded), or reconcile-sso.sh block. `Daher Reception`, `Daher Clinician Admission`, `Provider`,
`Organizational: Nurse` all survived two live reconciliation runs unchanged. No OpenELIS/SAML file
touched. No application code (`adapter/`, `provisioning/`, `ui/`, `openmrs-config/seed/`,
`openmrs-config/forms/`) touched — the 774-test baseline (adapter 94, provisioning 580, seed 16,
form-safety 19, ui 65) is unaffected by construction, matching this exact repo's own established
reasoning for config/infra-only tickets (ADR-017B/D).

**Explicitly not done:** no real SSO activation, no real staff assigned, no real facility write, no
patient/Visit/Encounter written outside the disposable/throwaway environments above, `Organizational:
Nurse`'s own breadth left untouched (tracked separately as ADR-017F, not built here), OpenELIS
untouched. `Organizational: Nurse`-only login, restart-survival, and the sys-admin/lab-tech client
roles remain either honestly disclosed gaps or explicitly out of this ticket's scope, as recorded
above rather than silently skipped.

**Cleanup.** The disposable realm's now-superseded mapper, and the two stray leftover mappers found
mid-investigation by ADR-017E, were removed from `kc-ui009` (not committed config, disposable-only).
Five synthetic Keycloak users created this ticket
(`adr017e2-clinician`, `adr017e2-reception`, `adr017e2-norole`, `adr017e2-nurse-only`, and ADR-017E's
own three `probe-*` users) remain on `kc-ui009`, left in place deliberately, matching this session's
established hygiene pattern (`Provider`/`Daher Reception` client roles have persisted the same way
since ADR-017B). The throwaway ambiguity database (`openmrs_adr017e2_ambig`) and its throwaway user
were dropped successfully; its one-off container (`openmrs-adr017e2-ambig`) could not be torn down —
the same intermittent daemon-level `docker kill`/`rm` permission denial recorded at the end of nearly
every prior ticket this session — needs a human `docker rm -f openmrs-adr017e2-ambig` alongside every
prior ticket's own still-pending leftovers, plus this ticket's own new `openmrs-adr017e2` (the
successfully-used clone) and its database `openmrs_adr017e2` in `mariadb-ui009`, and the throwaway
realm-import test (`daher-fresh-test`) was already deleted cleanly via `kcadm`.

### ADR-017E2 — final closure pass appended 2026-08-25 (before commit)

**Test accounting, precise, not just "774 unchanged."** The 774-test repository baseline (adapter
94, provisioning 580, seed 16, form-safety 19, ui 65) was **re-run live this pass**, not cited from
memory: adapter under `python:3.13.14-slim-bookworm` (CI's exact pinned image) — `ruff check src
tests migrations` clean, `ruff format --check` clean, `pytest` **94 passed**; provisioning, same
image — `ruff check src tests` clean, `ruff format --check` clean, `pytest` **580 passed**; seed —
`python -m unittest discover` **16 tests, OK**; form-safety — **19 tests, OK**; ui, Node 22 (CI's
exact pinned version) — `oxlint` clean, `tsc -b --noEmit` clean, `vitest run` **65 passed**, `vite
build` clean, secrets-in-bundle check clean. **Total: 774, confirmed live under CI's exact
toolchain, unchanged.** Separately, the entire `static` job was re-run locally, including the new
ADR-017E2 guard and the narrowed ADR-017D guard, plus `.env`-untracked, no-tracked-secrets,
image-pinning, compose-invariants, shell-syntax, JSON/XML/CSV-parse, doc-links, and the
`app.$BASE_DOMAIN` reconciliation checks — all pass. All three `compose` job profiles (default,
`backup`, demo) parse clean with a throwaway env file.

**The 10 new `ProviderRoleDerivationTest` cases are NOT part of any counted CI suite and were never
claimed to be.** They live under `openmrs-config/patches/oauth2login-bridge/test-verification/`,
test a third-party vendored module (`oauth2login`) that CI never compiles, tests, or even builds —
no CI job builds `openmrs-config`'s Docker image at all (confirmed by reading every job in
`.github/workflows/ci.yml`; the closest is `docker compose ... config -q`, which only parses YAML,
never builds an image). The Dockerfile's own `oauth2login-build` stage runs Maven with
`-Dmaven.test.skip=true`, so even a real image build would not execute them. They are a manual,
out-of-band verification artifact for this ticket's Java patch, run by hand against the pinned
Maven toolchain (documented above and in `PROVENANCE.md`) — real evidence that the patch behaves
correctly, but not part of the repository's own gated test count, and the 774 figure has never
included them.

**Restart-survival, via a genuine fresh process — not a literal `docker restart`.** `docker
restart`/`kill`/`rm` remain blocked by this sandbox's own recurring daemon-level permission denial
(unchanged from every prior attempt this session). Instead: a **second, independently-booted**
container (`openmrs-adr017e2-freshproc`, a distinct JVM/process, `docker run`, not `restart`) was
started from the same final patched image against the **same, already-populated**
`openmrs_adr017e2` database. A fresh OIDC login for the same synthetic clinician produced
byte-identical results to the original process: OpenMRS user UUID `04133caa-8b7f-4d89-8b01-
20a349dca2aa`, Person UUID `36116ed9-13ce-425f-b438-42ce27d98331`, Provider UUID `f870d211-e03a-
4e12-9e5c-72a021939f62`, `currentProvider` equal to that Provider, and the Provider row count for
that identifier held at exactly 1 both before and after. This is real evidence that Provider
identity survives a fresh JVM reading persisted state — the honest scope of what was proven, not
inflated to "restart-tested."

**Negative controls, re-run against that same fresh process** (not merely cited from the prior
process): `adr017e2-reception` — `authenticated: true`, `currentProvider: null`, 0 Provider rows.
`adr017e2-nurse-only` (`Organizational: Nurse` only, no `Provider`) — `authenticated: true`,
`currentProvider: null`, 0 Provider rows — the fail-closed non-Provider control, since a zero-role
account's own authentication behavior is orthogonal to this patch (see below). `adr017e2-norole` —
its **first-ever** login attempt still fails to authenticate (pre-existing `oauth2login` behavior:
`Context.authenticate` rejects a user with zero roles), though the underlying User row is
auto-provisioned regardless; a **second** login attempt against that now-existing zero-role User
succeeded in authenticating with `roles: []`, and still correctly produced `currentProvider: null`,
0 Provider rows — reported precisely because the two attempts differ, not smoothed into one claim.

**Provider ambiguity, reconfirmed on the current final image, not merely cited from before.** A
second isolated throwaway clone (`openmrs_adr017e2_ambig2`, never the canonical database) was built
fresh this pass, a duplicate active Provider row inserted by direct SQL for the same Person, and the
clinician logged in against a new container on that clone: no third Provider was created, and
`currentProvider` returned **`null`** — identical to the first ambiguity run, confirming the
unmodified upstream behavior is stable, not a one-off result.

**Admission, reconfirmed on the fresh-process container**, a new patient (`10000NH`): `POST /visit`
`201`, `POST /encounter` `201` (`Admission`, correctly linked to the Visit), `encounterProviders:
[]` (still not auto-populated, still not compensated for in the payload), both Visit and Encounter
`auditInfo.creator.uuid` equal to the authenticated clinician's own OpenMRS user UUID.

**Provider-creation-failure control: PROVIDER CREATION FAILURE — source-inspected, not
live-demonstrated.** `activateProviderAccount`'s own body wraps Provider creation in
`try { ... } catch (Exception e) { log.error(...); }` with proxy privileges removed in a `finally`
block regardless of outcome — a creation failure (e.g. a constraint violation) is caught, logged,
and the method returns normally; it does not throw out of `login()`, corrupt the authenticated
session, or attach the user to an unrelated Provider. This patch does not touch that method at all —
only whether it is called. No live control was run to force a genuine creation failure this pass
(doing so safely would require corrupting a throwaway database's schema/constraints in a way not
yet designed); this remains residual, source-level evidence, not a completed live control, reported
as such rather than inflated.

**Fresh/existing Keycloak parity, reconfirmed a third time.** `kc-ui009`'s `daher` realm carries
exactly one `openmrs`-client protocol mapper (`client roles`) after a third live reconciliation run
this pass (converged identically to runs 1–2 from the prior pass — same mapper ID, zero
duplication); all four client roles (`Daher Reception`, `Daher Clinician Admission`, `Provider`,
`Organizational: Nurse`) intact. Grepping every JSON file under `keycloak/` finds zero references to
a `provider` claim anywhere — zero unconditional `true` mappers, zero unconditional `false`
mappers, confirmed on-disk this pass, not merely asserted.

**Reproducible build, reconfirmed from the current tree, not the prior pass's cached artifact.** A
clean rebuild of the full `openmrs-config` image from this pass's tree (`docker build`, both
patches applied, Docker's own layer cache correctly recognized the patch inputs as byte-identical to
the prior pass and reused that layer — the same build, not skipped) produced the identical `.omod`
checksum: **`ae148634ec06817efcc99fdf9c0a1a4e05565ef76292c8eb7899b3b4aec8966b`**. That exact hash was
independently confirmed to match the OMOD actually **loaded and running** inside
`openmrs-adr017e2-freshproc` (`sha256sum` on `/openmrs/data/modules/oauth2login-1.5.0.omod` inside
the live container) — direct proof the running disposable container uses the build produced from
this exact final tree, not an assumption.

**Cleanup, this pass.** `curl-ui010` (the shared driver container) had exited on its own after ~27h
and was restarted via `docker start` (which, unlike `restart`/`kill`, is not blocked by this
sandbox's permission issue). The second ambiguity clone's database (`openmrs_adr017e2_ambig2`) was
dropped successfully; its one-off container (`openmrs-adr017e2-ambig2`) could not be torn down —
same daemon-level denial. `openmrs-adr017e2-freshproc` is left running deliberately as this pass's
primary evidence container. Needs a human `docker rm -f openmrs-adr017e2-ambig openmrs-adr017e2-
ambig2` alongside every prior ticket's own still-pending leftovers.

### DH-008G-CI — the 10 provider-derivation tests are now mandatory CI, not manual-only

**Gap closed.** ADR-017E2's static `static` job guard proves the committed patch *text* has the
right shape; it never compiled or ran a single line of the patched Java. A new job,
`oauth2login-provider-tests`, closes that: it runs inside the exact pinned
`maven:3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82`
image (byte-identical to `openmrs-config/Dockerfile`'s own build stage, not a generic runner
toolchain), clones the pinned `oauth2login` `1.5.0` commit
(`2770233627343a53719ec981e997d94b1d90b7ad`) and fails if that commit ever moves, applies the same
committed patch files the Docker build consumes (bridge patch, then the ADR-017E2 controller
patch — not a test-only approximation), installs `ProviderRoleDerivationTest.java`, and runs
`mvn package` scoped to the `omod` module, asserting the literal Maven output line
`Tests run: 10, Failures: 0, Errors: 0, Skipped: 0 ... ProviderRoleDerivationTest`. Compiles and
executes the real patched Java — a string-search assertion would not have caught this ticket's own
first draft bug (see below).

**`ProviderAttributeUserInfoTest` deliberately excluded from the gate.** It is a full
Spring-context integration test (extends `BaseModuleContextSensitiveTest`) that fails to load its
`ApplicationContext` under this exact JDK17/CGLIB toolchain — the same pre-existing failure
ADR-017E2 already confirmed via a control run against pristine, unpatched upstream source. Gating
CI on infrastructure this repo does not own and cannot fix would make the new job flaky, not
stronger. It remains a documented, manually-verified artifact (`PROVENANCE.md`), unchanged.

**A real bug in the gate's own first draft, caught by control-testing, not assumed correct.** The
first version anchored its result check to `^Tests run: 10, Failures: 0, Errors: 0` (line-start).
Every line Maven prints is prefixed `[INFO] `, so that anchor never matches anything — the check
would have silently reported the gate as failing on every genuinely passing run, including this
ticket's own first attempt. Caught by actually running the job locally end-to-end rather than
reasoning about the regex; fixed to `Tests run: 10, ... Skipped: 0.*ProviderRoleDerivationTest`
(unanchored, tied to the specific class name so a passing-looking match from an unrelated class
elsewhere in the log could not slip through).

**Control-tested, four ways, on a throwaway copy — never the committed patch file.** (1) Substring
matching instead of exact `List.contains` (`roleNames.toString().contains(...)`) — 2 test failures
(`SuperProvider`, `Provider Assistant` now incorrectly qualify), gate correctly failed. (2) Missing/
malformed roles made fail-open (`roleNames == null || ...`, catch block returns `true`) — 2 test
failures (missing-claim and malformed-claim cases), gate correctly failed. (3) The role constant
changed from `"Provider"` to `"Daher Reception"` — 3 test failures (both exact-`Provider` cases now
wrongly fail, `Daher Reception`-only now wrongly passes), gate correctly failed. (4) A patch context
line corrupted so `git apply` itself cannot locate its anchor — `git apply` failed outright with
"patch does not apply", halting the pipeline before any test ran, exit nonzero. All four restored
to the exact committed content afterward; a `diff` against the real committed file confirmed
byte-identical restoration.

**CI runtime.** Warm (Maven dependency cache hit via `actions/cache`, keyed on the pinned commit —
a fixed upstream `pom.xml` at a fixed SHA fully determines the dependency set, so the key never
goes stale): clone + patch + compile + test in well under a minute. Cold (cache miss, full
dependency download): ~4 minutes, matching ADR-017E2's own manual build timings. No clinical stack
is built or booted — only the two-module `oauth2login` Maven reactor (`api` + `omod`), consistent
with this file's own header ("What is deliberately NOT here: the ~50-minute clinical cold boot").

**Test accounting, honest, not inflated.** Application test total stays **774** (adapter 94,
provisioning 580, seed 16, form-safety 19, ui 65) — this ticket touches only
`.github/workflows/ci.yml`. `oauth2login` provider-patch tests: **10**, now genuinely gated by CI
for the first time, reported as a separate figure rather than folded into 774 — the project's
convention for counting third-party-patched-module tests has not been changed, and this ticket does
not change it.

**No provider architecture change.** `OAuth2LoginController.java.patch` itself, the Keycloak
mappings, role privileges, `Daher Reception`, `Daher Clinician Admission`, `Organizational: Nurse`,
UI code, and OpenELIS are all byte-for-byte untouched — confirmed via `git diff --stat` showing
exactly one file changed. No real facility state touched.

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

**DH-011J closure (2026-08-29, append-only — the policy above is unchanged, not re-litigated).**
The "verified, not assumed" mapping this ADR asked for is now supplied, live, against a disposable
stack built from the pinned `3.2.1.11` / `e2a6bdf7999fa3754e7cdafbd2a91259c3f0ce23` source:

- **"Not yet accepted or started" = `AnalysisStatus.NotStarted`** ("Not Tested", status id 4) —
  confirmed live as the default status of every newly created `Analysis`, both the initial order's
  own first test and every added test, with no native UI action taken. Cancellation from this
  status alone is permitted.
- **"Already in progress" = every other native status** — `TechnicalAcceptance`, `TechnicalRejected`,
  `BiologistRejected`, `Finalized`, `NonConforming_depricated`, `SampleRejected`, and `Canceled`
  itself. This is an allowlist (one safe starting status), not a blocklist of specific "dangerous"
  statuses, so a future OpenELIS upgrade adding a new status cannot silently bypass it.
- Cancellation from `TechnicalAcceptance` and `Finalized` was both **live-attempted and refused**
  (`REFUSED_IN_PROGRESS`, no mutation) — the second of these is the exact gap the pristine
  `ServiceRequestProvider.deleteServiceRequest` primitive has **no native guard against at all**
  (it will happily overwrite a `Finalized`/validated result). DH-011J's implementation adds the
  guard this ADR's policy requires; the native code was left unmodified.
- This closes ADR-018's own open implementation item for **added-test cancellation** specifically.
  Whole-initial-order cancellation was deliberately **not** implemented this ticket (DH-011J §7) —
  whether cancelling an initial order should cascade to independently-added tests is a distinct,
  still-open product question, not decided here. See PROVENANCE.md's DH-011J section for the full
  evidence and DH-011J's own answer to that question.

**DH-011J2 addendum (2026-08-29, append-only).** Liban issued a second, narrower decision on top
of the above, not a replacement: **for Daher V1, an added-test cancellation additionally requires
the associated specimen to NOT have been collected or received**, in addition to the existing
`AnalysisStatus.NotStarted` gate. If specimen collection/receipt state cannot be determined
authoritatively, the code must **fail closed** (refuse), never interpret a missing/unknown value
as "not collected."

**Implementation blocked — decision package, not a design choice made unilaterally.** DH-011J2
found, live and by source citation (full evidence: PROVENANCE.md's DH-011J2 section), that **no
reliable native signal exists** for this predicate on the specific code path Daher's added-test
cancellation targets: `SampleItem.collection_date`/`received_date` are unconditionally populated
with the creation timestamp by `SampleUtil.buildSampleXml`/`SampleAddService` for every added test,
walk-in or FHIR-originated, live-confirmed at t=0 immediately after creation; the SAMPLE-namespace
status vocabulary has no "Received"/"Collected" state at all (only `SampleEntered`/`SampleCanceled`/
`SampleDisposed`); the genuinely separate `sample_type_request` REQUESTED→COLLECTED state machine
exists but is structurally unreachable from this path; and `SampleItem.collector`/`quantity`/
`source_id` — never auto-populated by this path, confirmed NULL live — are also never written by
the only reachable edit surface (`SampleEditItem`/`SampleEditServiceImpl`), so they stay NULL
regardless of what happens to the specimen in the real world. Applied honestly, per this ADR's own
"cannot determine authoritatively → refuse" instruction, the gate as specified would refuse every
added-test cancellation unconditionally — a real product consequence, not an implementation
detail, and not decided here. Three options recorded in PROJECT_STATE.md, no recommendation
weighting beyond what the evidence supports. **Human Decision Required: Yes — which option, before
the specimen-state gate can be implemented.**

**DH-011J FINALIZE resolution (2026-08-30, append-only — supersedes the addendum immediately
above; the addendum's own investigation and its three-option framing are not retracted, kept as
the record of why this was decided this way).** **Liban selected option (b).** The specimen
-collection/receipt addendum above is **withdrawn as a V1 requirement**, not because the underlying
investigation was wrong — it was correct, and is not re-litigated — but because the exact OpenELIS
path Daher currently uses provides no authoritative signal to satisfy it, and this repo's own rule
is not to block a proven, narrower capability on an open product decision (AGENTS.md: don't invent
a gap-filling answer; also don't let an unrelated open question stall finished, correct work).

**Canonical V1 cancellation-eligibility contract, superseding the two-gate rule stated at the top
of this ADR's DH-011J2 addendum**: eligibility is based **solely** on the added-test Analysis's own
workflow state, exactly as DH-011J originally built and proved, before the specimen-gate ticket
was ever opened:

- `AnalysisStatus.NotStarted` → cancellation permitted.
- `AnalysisStatus.Canceled` → idempotent success (no second mutation).
- Every other recognized status (`TechnicalAcceptance`, `Finalized`, `TechnicalRejected`,
  `BiologistRejected`, `NonConforming_depricated`, `SampleRejected`) → refuse, zero mutation.
- Any unmapped/unrecognized status → refuse, zero mutation (fail-closed by construction: the
  allowlist checks for `NotStarted` and `Canceled` specifically, so anything else — known or not
  — falls through to refusal without needing to be named).

**Stated explicitly, not left implicit**: this is **not** a claim that a physical specimen has or
has not been collected. It means only that **Daher cannot observe that fact reliably through the
path it currently uses**, so V1 does not attempt to gate on it. A later ticket may reopen this if a
genuine signal becomes available (e.g., option (c) from the addendum above — a Daher-owned
specimen-state marker) — that remains a live, undecided possibility, not foreclosed by this
resolution.

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

---

## ADR-023 — The provisioning API is not publicly reachable

**Date:** 2026-08-16 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** ADR-021 established that the portal is an untrusted client holding a scoped
credential. It left unstated *who else can reach the API*, and an unstated assumption is one that
regresses silently — typically when somebody opens a port to debug something at 2am.

**Decision.**

1. **The provisioning API is firewalled to the portal server's address.** It does not listen to
   the internet. One rule, and it removes scanners, credential stuffing, and whatever CVE lands
   in the web stack next year — essentially every attacker who is not already inside the portal.
2. **The portal runs on its own domain**, separate from any marketing site and separate from
   `BASE_DOMAIN`'s facility subdomains. If a marketing site is ever WordPress or similar — a
   plugin-heavy, frequently-patched codebase — sharing an origin would let its XSS act *as* the
   fleet-provisioning portal. If subdomains of one registrable domain are used instead, cookies
   must be host-scoped; a cookie on `.example.org` is readable across all of it.
3. **The browser never talks to the provisioning API.** It talks to the portal; the portal calls
   the API server-side. A scoped provisioning credential in browser JavaScript is one XSS away
   from being stolen, and an admin page is exactly the sort that gets less scrutiny than a public
   app. ADR-020's server-rendered choice already produces this — it is recorded here so it stays
   deliberate.

**Rejected: a VPN tunnel, for now.** Genuinely stronger, and the right upgrade later. But it is a
persistent moving part — key rotation, tunnel-down debugging — and above the IP rule it only
defends against an attacker who has *already* stolen the portal credential *and* can spoof a
source address. Not worth the operational cost at one pilot facility. Upgrade path: WireGuard
between portal and API host, then tighten the firewall to the tunnel interface. **No code
changes** — same credential, same HTTPS, different route.

**Rejected: same domain.** See point 2.

**Defence in depth, because firewalls get edited.** The application enforces its own source
allowlist in addition to the firewall. If someone opens a port to debug and forgets, the API
still refuses and records the attempt. The check uses the **real peer address only** and never
`X-Forwarded-For`, which any client can set — trusting a forwarded header here would hand an
attacker the allowlist bypass for free.

**What this does NOT change.** It does nothing against a compromised *portal server* — the
attacker is already at the allowed address. That case is ADR-021's walkthrough and is unchanged:
create, inspect, stop facilities, loudly, with no clinical data reachable. This decision removes
every attacker who is not already inside the portal, which is nearly all of them.

---

## ADR-024 — Credential rotation rotates what can be rotated, and refuses the rest

**Date:** 2026-08-16 · **Status:** ACCEPTED · **Decided by:** Liban (build prompt) with the
constraint below discovered during implementation

**Context.** The provisioning API was specified with a `rotate-credentials` endpoint. Building
it revealed that "rotate this facility's credentials" is not one operation, and the obvious
implementation — regenerate the secrets, rewrite the env file, restart — would **break the
facility it was meant to protect.**

### What rewriting the env file actually does to a running facility

| Credential | Effect of rewriting it | Safe? |
|---|---|---|
| `OMRS_DB_*`, `OE_DB_*`, `OE_POSTGRES_*`, `KEYCLOAK_DB_*`, `ADAPTER_DB_*` | The databases were provisioned by `initdb`, which **runs once**. The app now presents a new password to a database that still holds the old one — **every service loses its database** | ❌ |
| `OMRS_ADMIN_PASSWORD` | Applied on **first boot only** (DH-019). Rewriting it changes nothing; the account keeps the old password while the file claims otherwise | ❌ |
| `BACKUP_ENCRYPTION_PASSPHRASE` | Decrypts existing archives. Changing it without escrowing the old value makes **every prior backup unrecoverable** — including the one needed in the disaster that prompted the rotation | ❌ |
| `OE_KEYSTORE_PASSWORD`, `OE_TRUSTSTORE_PASSWORD` | DH-021's generator detects the store no longer opens and regenerates the material. Self-healing by design | ✅ |
| Keycloak client secrets | Applied by the idempotent reconciliation job on the next `up` | ✅ |

**Decision.** Rotation is **scoped and honest**. The endpoint rotates the internal TLS material
and Keycloak client secrets — the credentials that can be rotated without coordination — and
**refuses** the others with the specific reason, rather than performing a rewrite that would take
the facility down or orphan its backups.

Each refusal names what would actually be required:

- **Database credentials** need `ALTER USER` inside each engine, coordinated with a restart. That
  is a real procedure and it belongs in a runbook someone follows deliberately, not behind a
  button labelled "rotate".
- **The OpenMRS admin password** is changed through OpenMRS, not through configuration.
- **The backup passphrase** requires the old value to be escrowed and every retained archive to be
  re-encrypted or accepted as lost. That is a decision about data recoverability, not an
  operation.

**Why not implement the hard ones anyway.** Because a rotation that silently breaks a clinical
facility is worse than one that says what it will not do. The `credentials:rotate` scope exists
and the portal holds it; the value of that scope is bounded by this ADR, which is the honest
bound rather than an implied one.

**Consequence for ADR-021's blast radius.** Unchanged and slightly narrower than previously
implied: an attacker who owns the portal can force TLS material and client secrets to be
regenerated — disruptive, requiring a restart, and loud in the audit log — but cannot use
rotation to lock a facility out of its own databases or destroy the ability to decrypt its
backups.

---

## ADR-025 — The backup passphrase is escrowed by construction; backup is scheduled only when it can succeed

**Date:** 2026-08-16 · **Status:** ACCEPTED · **Decided by:** Liban (build prompt) with two
constraints found during implementation

**Context.** The build prompt requires that a facility created through the API gets a scheduled
backup automatically — "a human should not have to remember to add backup". Implementing that
surfaced two problems, one of them serious.

### Problem 1 — the passphrase would live only on the machine being backed up

ADR-022 has the agent generate a facility's credentials locally, which is what keeps the API from
ever holding them. Applied to `BACKUP_ENCRYPTION_PASSPHRASE`, that produces a backup encrypted
with a key stored **only on the host being backed up**.

If that host dies — the exact disaster backups exist for — the passphrase dies with it and every
off-site archive is permanently undecryptable. **That is not a backup.** It is an archive nobody
can open, which is worse than no backup because it looks like one.

**Decision.** The backup passphrase is **supplied by the operator during host bootstrap**, not
generated by the agent. Bootstrap is already a manual on-host step (ADR-022), so this adds no new
ceremony — and it means the passphrase exists in at least two places by construction: the
operator's keeping, and the host.

This does not weaken ADR-022. The API still never holds it: the operator carries it to the host,
exactly as they already carry the enrollment token. A credential that must survive the loss of a
machine cannot be generated on that machine.

### Problem 2 — scheduling a backup that cannot succeed

DH-022 removed the backup scheduler from the default Compose path precisely because an
unconfigured scheduler restart-loops. Auto-enabling it for every provisioned facility would
recreate that bug at fleet scale.

Worse would be enabling it with `BACKUP_REQUIRE_OFFSITE=false` to keep it quiet: that produces
local-only archives on the same host as the data, presented as working backups. A facility would
show green while being one disk failure from losing its patient records.

**Decision.** Backup is scheduled **only when it can actually succeed** — a passphrase and an
off-site destination both present. When they are not, the scheduler is not enabled and the
facility's status says so **as a problem, not a neutral state**: a facility with no backup is
reported as such in the API and shown as a warning in the portal.

**The honest consequence:** a facility can be provisioned without backup. That is deliberate, and
it is visible rather than silent. The alternative designs either crash-loop, or lie.

**Unchanged:** DH-014's off-site verification and DH-015's performed restore remain the pinned
gates. A scheduled backup is still not a *verified* backup, which is why `BackupState` carries
`unverified_no_restore_drill` and the portal shows the restore-drill date beside the backup state.

---

## ADR-026 — OPEN: Option F, OpenELIS's own business FHIR providers, as an order ingestion candidate

**Date raised:** 2026-08-16 · **Status:** OPEN — candidate recorded, **not selected**
**Human Decision Required:** Yes — this is BLOCKER-6's fifth option, and BLOCKER-6 is a human decision
**Supersedes nothing.** BLOCKER-6's options A–D stand exactly as recorded in `PROJECT_STATE.md`.

**Context.** BLOCKER-6 was raised on 2026-08-16 after probing showed OpenELIS's documented inbound
path is a **poll** (`org.openelisglobal.remote.source.uri`, empty, polled every 120s) rather than a
push into its FHIR store — and that the lab has never received an order (`electronic_order`,
`sample`, `analysis` all 0 rows; FHIR store `total: 0` on every resource type). Four options were
recorded. A fifth now exists.

**The candidate.** The OpenELIS **webapp** — not the separate HAPI container — ships its own
business-level FHIR providers. Confirmed in the deployed bytecode of the pinned
`itechuw/openelis-global-2:3.2.1.11` image:

```
/usr/local/tomcat/webapps/OpenELIS-Global/WEB-INF/classes/org/openelisglobal/fhir/providers/
  ServiceRequestProvider.class   PatientProvider.class      PractitionerProvider.class
  SpecimenProvider.class         DiagnosticReportProvider.class
  ObservationProvider.class      OrganizationProvider.class  FhirProviderUtils.class
```

Proposed shape, **if it proves out**:

```
OpenMRS → Daher Adapter → OpenELIS native business FHIR API → Sample / SampleItem / Analysis
```

**Why this is materially different from option C.** Option C is writing into the generic HAPI
`fhirstore`, which the configuration describes as where OpenELIS *publishes*, not an inbox. Option F
targets providers inside the application itself, which is the layer that would plausibly create
native laboratory state. If it works, it keeps the adapter as the routing, ledger and audit boundary
that ADR-005 made load-bearing for patient safety — without turning it into a FHIR server (option B)
and without introducing a second system that stores patient data (option D).

**What is established, and what is emphatically not.** Established: the classes exist in the image
we run. **Not established:** the servlet path (it is absent from `web.xml`, so it is Spring-registered
and must be found), whether a create reaches native `Sample`/`SampleItem`/`Analysis` rather than
stopping at a FHIR façade, whether the normal OpenELIS UI workflow then sees the order, whether an
adapter correlation identifier survives ingestion and is exactly searchable, and what cancellation
does. **The existence of a class is not evidence of a working ingestion path** — this repository has
been burned repeatedly by treating an advertised capability as a demonstrated one (ADR-005 Q4 is the
standing example: OpenELIS advertises `Subscription` and no delivery has ever been observed).

**Decision.** None. Option F is recorded as a candidate so the roadmap is not written against a
narrower set of choices than actually exists. **DH-011B is the runtime spike that must answer the
questions above, using synthetic data only, before anyone selects A, B, C, D or F.**

**Constraint carried forward.** Direct inserts into the OpenELIS PostgreSQL schema are excluded
under every option. If OpenELIS turns out not to preserve or search an incoming adapter correlation,
the fallback is DH-011D's smallest upstream-compatible modification — which would itself need a
superseding ADR, because ADR-016 and BLOCKER-4 established that shipped laboratory configuration is
left untouched.

### ADR-026 — runtime evidence appended 2026-08-17 (DH-011B). **Status remains OPEN.**

Status stays `OPEN`: BLOCKER-6 is a human decision and nothing below selects an option. What follows
is evidence, gathered against the running stack with synthetic data only.

**Confirmed — Option F is not a façade.** The providers are served by a HAPI `RestfulServer` mapped
at `/fhir/*` **inside the webapp** (`AnnotationWebAppInitializer` → `FhirRestfulServer`), running
**HAPI 7.0.2**, distinct from the standalone container's 6.6.0. Authentication is **HTTP Basic** via
`SecurityConfig`'s `BasicAuthRequestedMatcher`; a form-login cookie is refused on POST with
`access=denied`, and a wrong password returns 401. `POST /fhir/Patient` created a real
`clinlims.patient` + `clinlims.person` row with a `fhir_uuid`, logged as
`Patient persisted with ID: 2`. **The business providers write native OpenELIS domain state.**

**Two findings that constrain any Option-F design:**

1. **`PatientProvider` returns `201 Created` even when the persist fails**, with no `Location`
   header. Two identical requests differing only in a given name — one containing digits, which the
   `Person` validator rejects — both returned 201; one wrote a row, one wrote nothing. Status code
   is therefore not evidence of persistence, and the response does not carry the created id. An
   adapter must **verify by read-back**. Known bug 19.
2. **`ServiceRequest` is not `identifier`-searchable on this server** — rejected with HTTP 400, and
   the controls hold (an unknown parameter is also 400; the advertised `_id` returns 200). The
   standalone HAPI 6.6.0 *does* support it. So the option that reaches native lab state cannot be
   queried by DH-025's correlation, and the option that can be queried may not reach native lab
   state. Resolving that is DH-011C, and it is the concrete trigger for DH-011D.

**Not disproven, unfinished.** `ServiceRequestProvider` refused a synthetic order with HTTP 400
`Generated lab number is invalid` — and correctly created nothing, so the false-green above is
specific to `PatientProvider`. The cause is configuration: the accession prefix is upstream's
placeholder `DEV01` with `validateAccessionNumber=true`. A laboratory accession number is a
facility-scoped real-world identifier, so completing this is gated on facility identity
(NEEDS LIBAN #7), not on engineering judgement.

**Nothing was modified:** no OpenELIS laboratory test, mapping, accession setting, or role was
changed to make the spike progress. The synthetic patient row is retained and documented rather than
deleted, consistent with this repository's practice of never removing patient rows to tidy up.

### ADR-026 — full spike verdict appended 2026-08-17 (DH-011B, authorized by Liban). **Status remains OPEN.**

Status stays `OPEN` because BLOCKER-6 is Liban's decision and this appendix selects nothing. What it
does is remove the guesswork: Option F is no longer a candidate resting on the existence of some
classes.

**Established — Option F writes OpenELIS's own domain tables.** Creating a synthetic Practitioner
through `POST /OpenELIS-Global/fhir/Practitioner` over HTTP Basic produced a real `clinlims.provider`
+ `clinlims.person` row; the earlier pass produced a real `clinlims.patient` row. `ServiceRequestProvider`
calls `SamplePatientEntryService`, `SamplePatientUpdateData`, `SampleOrderItem` and
`SampleAddService$SampleTestCollection` and constructs `Sample` and `Analysis` — the same services the
human sample-entry workflow uses. **This is not a FHIR façade over the generic store**, which was the
question the spike existed to answer, and it is what makes Option F preferable to A, B, C and D.

**Blocked — two gates, neither of which an engineer may close.**

1. **A numeric facility site code is required.** `acessionFormat = SITEYEARNUM`, and that validator
   formats the accession as **`%013d`** — thirteen digits, all numeric — while the configured prefix
   is upstream's alphabetic `DEV01`. Every ServiceRequest is therefore refused with
   `Generated lab number is invalid`, before subject or requester is even examined. A client-supplied
   accession does not help: two variants proved the value is always server-generated and a supplied
   one is ignored. A laboratory accession is a facility identifier convention (NEEDS LIBAN #7).
2. **The adapter correlation does not survive ingestion, and the search surface cannot express the
   contract at all.** Our `identifier` is discarded — the returned resource carries only OpenELIS's
   own `provider_uuid`/`facility_id`, and `provider.external_id` is NULL. Worse, search is delegated
   to `http://fhir.openelis.org:8080/fhir` — a port this stack does not serve — so a submitted
   identifier and a never-submitted control **both return `total: 0`**. DH-025 requires
   0 = proven absent, 1 = proven present, 2+ = ambiguous; through this surface those are
   indistinguishable, which is a stronger negative than a missing field.

**Consequence.** Option F remains the preferred ingestion path, and the follow-on is **DH-011D** — the
smallest upstream-compatible change that persists an external order identifier and makes it exactly
searchable. Its *verification* is gated on gate 1, because proving exact search requires an order that
can be created.

**Not observed, and not claimed:** no `Sample`, `SampleItem`, `Analysis` or `electronic_order` was ever
created, so native laboratory workflow state and the OpenELIS UI's view of an incoming order remain
**unverified**. Cancellation is likewise unobserved — it needs an order to cancel.

**Nothing was modified to make the spike progress:** laboratory tests unchanged (177 active),
`site_information` unchanged (121 rows), no accession setting altered, no role granted, no database row
written or deleted directly. Two clearly-labelled synthetic resources were created through the
supported FHIR surface and are retained rather than deleted.

### ADR-026 — correction appended 2026-08-17. **Status remains OPEN.**

**The 2026-08-17 spike appendix above overstated one finding, and this corrects it rather than
editing it.** The file is append-only so the original reasoning stays auditable — including the
part that was wrong, because how it was wrong is the useful bit.

**What was claimed:** that the DH-025 contract — 0 = proven absent, 1 = proven present, 2+ =
ambiguous — was *"inexpressible through this surface at all"*, on the grounds that every search
response's `self` link pointed at `http://fhir.openelis.org:8080/fhir/…`, a port this stack does not
serve.

**Why that was wrong:** the `self` link is a cosmetic HAPI artifact and does not indicate where the
query was answered. The inference was made from a link rather than from a query result, and it was
wrong in the **expensive** direction — it would have justified a substantially larger OpenELIS patch
than the evidence supports.

**Re-tested directly, with controls:**

| Query | `total` |
|---|---|
| `Practitioner?_summary=count` | 1 |
| `Practitioner?identifier=<OpenELIS's own provider_uuid>` | **1** — proven present |
| `Practitioner?identifier=0000…0000` | **0** — proven absent |
| `identifier=http://openelis-global.org/provider_uuid\|<uuid>` | **1** |
| `identifier=urn:wrong-system\|<same uuid>` | **0** |

The last row is the control that makes the rest meaningful: a **wrong system with a right value
returns 0**, so the server performs real token matching rather than ignoring the system or matching a
substring.

**Corrected position.** Exact identifier lookup on the business FHIR API **works**, and DH-025's
contract **is** expressible there. The one thing that fails is that OpenELIS does not retain an
identifier the *client* supplied — it assigns its own and discards ours.

**Consequence for DH-011D, which gets smaller.** Its scope narrows from "persist an external
identifier *and* make it exactly searchable" to "**persist the incoming identifier**". The search
half already exists and is control-proven. A patch that also added search machinery would be adding
something that is already there.

**What is still unknown, and must not be inferred:** whether `ServiceRequestProvider` retains a
submitted identifier. `Patient` and `Practitioner` do not, but it is a different provider and may
differ. That is untestable until the accession blocker (NEEDS LIBAN #7) is resolved, so DH-011D
should not be built until it is known whether it is needed for the resource that actually matters.

**BLOCKER-6 remains OPEN and no option is selected.** This correction makes Option F look *better*,
not worse, which is precisely why it needed correcting rather than leaving a pessimistic record in
place.

### ADR-026 — OPTION F SELECTED (2026-08-28, DH-011D ticket) — Liban's decision. **BLOCKER-6:
architecture question RESOLVED BY DECISION. DH-011 itself remains NOT complete.**

**The decision, verbatim as given.** Select Option F for DH-011/BLOCKER-6. Canonical V1 order path:

```
OpenMRS ServiceRequest
  → DH-011E poller (built, idle)
  → Daher adapter
  → DH-025 safety/idempotency ledger
  → OpenELIS embedded BUSINESS FHIR API (/OpenELIS-Global/fhir/*, HAPI 7.0.2)
  → native OpenELIS laboratory domain state (clinlims.sample/analysis/electronic_order)
```

Explicitly **not** selected: A (OpenELIS pulls straight from OpenMRS — removes the adapter's audit
boundary), B (adapter becomes a FHIR server), C (push into the standalone `openelis-fhir` store —
unverified, likely not a real inbox), D (a third exchange datastore — adds a second system storing
patient/order data). These remain recorded in `PROJECT_STATE.md`'s BLOCKER-6 table exactly as first
written; this decision does not retract or rewrite them.

**Correlation direction, also decided:** the canonical OpenMRS-order ↔ OpenELIS-order correlation is
a **`ServiceRequest`-owned external order identifier** — not an overload of `Specimen`/accession
identifiers. Reason given: the correlation identifies the *order* itself, must survive independently
of specimen/accession configuration (which is facility-owned, laboratory-scoped configuration, not
an engineering surface — see NEEDS LIBAN #7), and must satisfy DH-025's exact 0/1/2+ reconciliation
contract on the resource that is actually the order, not a downstream artifact of it.

**What this decision resolves, precisely.** BLOCKER-6 was raised as an architecture question — *how
does an order reach OpenELIS at all* — and that question now has an answer with Liban's sign-off.
It does **not** mean DH-011 (the ticket that delivers a working order path end to end) is done: the
delivery implementation, the numeric site code, the patient/practitioner/test mapping decisions, and
live end-to-end proof all remain outstanding, exactly as enumerated below.

**What this same pass (DH-011D) built, verified live, and did not build** — full technical account
in `PROJECT_STATE.md`'s DH-011D entry and `openelis-config/patches/order-correlation/PROVENANCE.md`.
In summary: the `ServiceRequest?identifier=` search-parameter gap is patched, built from the exact
pinned upstream commit, and live-proven with a genuine before/after/negative-control triad against a
disposable container sharing the real stack's database (400→200, scoping control still 400, auth
control still 401). The identifier-*persistence*-on-create half, and the DB-level uniqueness
question, are designed from source with exact file/line anchors but **not implemented or built** —
both are gated on the numeric site code existing, because neither can be exercised without a
creatable `ServiceRequest`, and this repository's evidence discipline (`AGENTS.md`) treats an
unexercised change to a clinical order-creation path as worse than leaving it undone.

**BLOCKER-6 STATUS: RESOLVED BY DECISION (architecture + correlation direction).** `DH-011` overall
STATUS: **NOT COMPLETE** — see `PROJECT_STATE.md`'s DH-011 status matrix for what remains and why.

### ADR-026 — correction appended 2026-08-28 (DH-011D2 closure pass): persistence and uniqueness
are now BUILT, not merely designed

**The paragraph above ("identifier-persistence-on-create half... not implemented or built") is
superseded, not retracted — the file is append-only for the same reason as always.** A follow-on
pass in the same session completed both:

- **Persistence**: `ServiceRequestProvider.createServiceRequest` now extracts, validates
  (exact-one-match, exact system+value comparison, hex-digest format), and persists the Daher order
  correlation into `clinlims.electronic_order.external_id` (a dedicated `FHIR`-typed row, isolated
  from the existing human "external order number" accessioning field's different semantics), and
  `FhirTransformServiceImpl.transformToServiceRequest` echoes it back onto the outbound-synced
  resource so the already-patched search half can find it.
- **Uniqueness**: a new Liquibase changeset adds a genuine PostgreSQL partial unique index —
  `electronic_order(external_id) WHERE type='FHIR'` — scoped so it does not touch the existing
  HL7/CSV "last one wins" tolerance for any row this patch does not itself create.

**What changed the earlier judgment.** The original reasoning — "an unexercised change to a clinical
order-creation path is worse than leaving it undone" — was correct for the parts that genuinely
cannot be exercised without a live `ServiceRequest` create (which stays blocked on NEEDS LIBAN #7).
What the follow-on pass established is that the persistence and uniqueness *mechanisms* — as opposed
to their end-to-end trigger — **can** be built and proven at the boundaries that don't require a
live create: a full pinned-source build and compile; the Liquibase migration applying cleanly on a
genuinely fresh database with zero errors; a real, two-process PostgreSQL concurrency race against
the patched schema (one `COMMIT`, one constraint-violation rejection, exactly one row persisted for
the contested correlation); a scoping control proving HL7-typed duplicate tolerance is untouched. All
of this ran against fully isolated, disposable infrastructure — never the real shared database, and
never a fabricated native clinical order. Full account, including exactly what still cannot be
proven without the site code:
`openelis-config/patches/order-correlation/PROVENANCE.md`.

**BLOCKER-6 STATUS: unchanged, RESOLVED BY DECISION. `DH-011` overall STATUS: still NOT COMPLETE** —
what changed is how much of DH-011D is now built-and-proven versus merely designed, not the overall
completion state of DH-011 itself, which still needs the site code and several human decisions
untouched by this pass (DH-011G's identifier-system choice, DH-011H's real requester, DH-006's test
mapping).

### ADR-026 — correction appended 2026-08-28 (DH-011D3 closure pass): three pre-activation gaps
closed, one new P0 finding, still NOT COMPLETE

**Every statement above that DH-011D's remaining gap is "the site code" is superseded, not
retracted — append-only, same as always.** An independent review of the committed DH-011D
implementation, requested in the same session as DH-011D2's closure, found three pre-activation
safety gaps the DH-011D2 pass had not closed. This pass closed all three, closed a fourth defect
the fix work surfaced, and found — but did not fix, because fixing it is a design decision, not an
engineering one — a fifth that is larger than anything ADR-026 has recorded so far.

**Closed**: (1) the native create and the correlation write were two separate database
transactions, not one — a mid-sequence failure or a race could commit native Sample/Analysis rows
with no correlation. Fixed with a real outer Spring transaction, in a separate bean (`@Transactional`
directly on the HAPI provider method broke provider registration entirely — `HAPI-0289` — a
constraint this ADR did not previously know about). (2) the correlation search read the possibly-
stale secondary FHIR store, which upstream's own order-creation logs-and-continues on export
failure — a native order could exist while the search reported zero. Fixed by deriving existence
from native OpenELIS state directly, plus a `Bundle.total` correction (both the OpenELIS and adapter
sides needed it) so a real multi-test order does not permanently misread as ambiguous. (3) the
delivery writer that would eventually POST a `ServiceRequest` did not exist — supplying the site
code alone could never have made delivery work. Now implemented, with activation kept on its own
gate, unchanged from ADR-026's "the site code is one input, not a green light" framing.

**Also found and fixed**: `clinlims.electronic_order.external_id` was `varchar(60)`; the
correlation value this whole design depends on is a 64-character SHA-256 hex digest. Every real
correlation write would have failed silently defeating Option F before the writer was ever reached.
Widened via a Liquibase migration, proven by a genuine value failing before the fix and succeeding
after it against the same database.

**Found, not fixed — `Human Decision Required: Yes`, recorded in `PROJECT_STATE.md`.**
`ServiceRequestProvider.createServiceRequest` (pre-existing upstream code, confirmed by grepping
every committed DH-011D/D2/D3 patch for the failing line — no match) cannot create a new order at
all. It requires an already-existing `Sample`, reached through a `specimen` reference on the
inbound `ServiceRequest`; accession-number generation never runs for a request with no such
reference. A live `POST /fhir/ServiceRequest` with no site code configured, and the identical
request with a synthetic test-only numeric site code configured, both failed identically
(`Generated lab number is invalid`) — ruling out the site code as the cause. The endpoint appears
to expect a Specimen-creation step first (`SpecimenProvider` has its own `@Create`), but designing
or building that prerequisite was **not attempted**: it decides what a Specimen represents and who
creates it in this system, which is exactly the kind of gap ADR-026's own history has repeatedly
treated as a decision for Liban, not an assumption to make while closing a ticket.

**Consequence.** Every control this ticket's own review asked for that requires a live
`createServiceRequest` success — the formal transaction-capture proof, the induced-failure
rollback control, the service-layer concurrency control, the FHIR-sync-failure control, the
crash-recovery control — is blocked by this finding, not by NEEDS LIBAN #7. Full account:
`openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011D3 section.

**BLOCKER-6 STATUS: unchanged, RESOLVED BY DECISION (this finding does not reopen Option F or the
correlation-identity decision — it is a downstream implementation gap in the endpoint Option F
already selected, not a reason to reconsider which endpoint). `DH-011` overall STATUS: still NOT
COMPLETE. `DH-011E`: still NOT ACTIVATED.**

### ADR-026 — BLOCKER-11 resolved by decision 2026-08-29 (DH-011M): Option P, built and live-proven

**BLOCKER-11** (raised by the DH-011D3 evidence pass above) asked which native OpenELIS lifecycle
correctly turns a new external order into pre-analytic state when no Sample/SampleItem/Specimen
exists yet. The evidence ticket that answered it found: Specimen-first (Option S) cannot bootstrap
a Sample at all (`SpecimenProvider.createSpecimen` requires an *already-existing* Sample, found by
accession number); the native `TaskWorker`/`DBOrderPersister` pipeline (Option O/R) is a complete,
coherent, already-existing external-order model, but is pull-based (OpenELIS polls a remote FHIR
server it trusts) and defers real Sample/SampleItem/Analysis creation to a later human accessioning
step, which would require redefining what DH-025 considers a successful write; and Option P --
patching `ServiceRequestProvider` itself to generate a real accession number and let
`SamplePatientUpdateData.createPopulatedSample`'s already-existing "create new sample" branch run
-- needs no change to DH-025's contract at all and reuses the exact accession generator the human
Sample Entry workflow already trusts.

**Liban's decision, 2026-08-29: Option P.** Do not build Option O, Option R, or Specimen-first.
Do not redesign DH-025.

**Built and live-proven** (full account: `openelis-config/patches/order-correlation/
PROVENANCE.md`'s DH-011M section) against fully synthetic, disposable infrastructure -- `.env`
never read, the real facility stack never touched. A new order with no specimen reference now
succeeds end to end: a genuine `SITEYEARNUM` accession number, exactly one Sample/SampleItem/
Analysis, the Daher correlation preserved and discoverable by the real adapter writer and probe
(not a mocked double), no fabricated specimen-collection fact (`collector`/`quantity`/`source_id`
stay unset; the lifecycle status reads "entered", never "received" or "collected", confirmed both
at the database and through the real dashboard endpoint a lab worker's UI actually renders from).
Duplicate delivery and a genuine concurrent-delivery database race were both proven safe live: the
race's loser leaves zero orphan native rows, extending Finding A's atomicity proof from DH-011D2's
raw-SQL demonstration to a real concurrent create through the business API. One safety gap was
found and closed during this ticket's own testing: a *dangling* specimen reference (present but
unresolvable, as opposed to no reference at all) was indistinguishable from a genuinely new order
and would have silently created a duplicate -- now fails loud instead.

**One further finding, deliberately not fixed here.** Adding a second test to an already-existing
order (a real, resolvable specimen reference -- the branch Option P leaves entirely untouched)
throws a live `StaleObjectStateException`. Rather than assume this was caused by DH-011D3's
transaction-atomicity wrapper, it was control-tested: the identical scenario was reproduced against
a completely pristine build of the exact same pinned upstream tag and commit, with zero Daher
patches of any kind applied. The identical exception occurred. This is conclusive: the defect is
pre-existing in upstream OpenELIS 3.2.1.11 itself, not introduced by any Daher patch, and DH-011M's
own new-order path never reaches the branch where it lives. Fixing a pre-existing, unrelated defect
in an already-shipped code surface is out of this ticket's scope by its own instructions ("smallest
patch", "one ticket only") and AGENTS.md's guidance on a fix that exposes a second, unrelated bug --
recorded here as a new finding requiring its own ticket, not silently repaired and not silently
left undocumented.

**BLOCKER-11 STATUS: RESOLVED BY DECISION.** BLOCKER-6 stays RESOLVED BY DECISION, Option F
unchanged. `DH-011` overall STATUS: still NOT COMPLETE -- new-order creation is now built and
live-proven, but the pre-existing existing-order-edit defect, the real numeric site code (NEEDS
LIBAN #7, for the facility's actual accession prefix, no longer for order creation itself),
DH-011G/H/I's human decisions, and DH-011J/K/L all remain open. `DH-011E`: still NOT ACTIVATED.

**Correction appended 2026-08-29 (DH-011N).** The paragraph above's claim that the existing-order
`StaleObjectStateException` was "conclusively" proven pre-existing-upstream by control-testing
against pristine OpenELIS was **correct in conclusion but overstated in evidentiary basis**: both
runs used a directly-SQL-inserted `Sample`/`SampleItem`/`Analysis` fixture (labeled at the time
"FHIR SEARCH FIXTURE ONLY / NOT NATIVE ORDER PROOF"), and a hand-written INSERT is itself a common
artificial cause of exactly this exception. DH-011N closed that gap: reproduced the identical
failure live, three times, against a genuinely native, Hibernate-managed `Sample` created through
Option P's own path -- the defect is real, not a fixture artifact -- and root-caused it to
detached-entity reuse across session boundaries (no ambient transaction spans
`ServiceRequestProvider.createServiceRequest` until `daherOrderTransaction.persist(...)` opens
partway through the method, per DH-011D3's own finding A). This does not change BLOCKER-11's
RESOLVED status or Option P's own correctness -- Option P's new-order path never reaches the
affected branch, re-confirmed live by DH-011N. It only strengthens the evidentiary basis for the
"pre-existing, not a DH-011M regression" claim, which now rests on a genuine reproduction rather
than an unproven fixture. The fix itself remains unbuilt, blocked on two Human Decisions recorded
in `PROJECT_STATE.md`'s DH-011N entries. Full account:
`openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011N section.

---

## ADR-027 — Daher UI production identity: the browser holds the real upstream sessions; no BFF in V1

**Date:** 2026-08-22 · **Status:** ACCEPTED (architecture pattern) — **activation blocked on BLOCKER-5**
**Ticket:** UI-003. **Does not supersede** ADR-013 (session lifetimes), ADR-014 (one realm, OpenMRS
OIDC / OpenELIS SAML), or ADR-017 (the 3-role authorization matrix) — it decides how a browser-based
Daher UI reaches those already-decided protocols without inventing a fourth identity.

### Context

UI-002 shipped a real frontend with a dev-only proxy that holds the shared OpenMRS admin credential
in the Vite server's own Node process — explicitly not a production answer. UI-001A already found
the production path `UNRESOLVED`. This ADR is the result of inspecting the actual running
configuration (Keycloak realm export, `configure-sso.sh`, `reconcile-sso.sh`, the Caddyfile, and the
live containers) rather than reasoning from the generic OIDC/SAML brief.

**Measured, not assumed:**

- The Keycloak `daher` realm has exactly three clients: `openmrs` (confidential OIDC, standard flow
  only, no direct grants, no service account), `openelis` (confidential SAML, same shape), and
  `adapter` (service-account only, `client_credentials`, no browser flow at all). **No client for a
  browser-based Daher UI or a BFF exists today**, and no token-exchange capability is configured on
  any client.
- `openmrs-module-oauth2login` and `org.itech.login.saml` are both server-side confidential clients
  that complete the browser-redirect handshake themselves and establish their own Tomcat session
  (`JSESSIONID`) — neither is a bearer-token resource server. **Demonstrated, not inferred:** sending
  `Authorization: Bearer <token>` to `/ws/rest/v1/patient` gets `WWW-Authenticate: Basic` and the
  same anonymous "Privileges required" rejection UI-001A already characterized — the header is
  silently ignored.
- `configure-sso.sh` never gives OpenMRS its own public URL; `OPENMRS_PUBLIC_URL`/
  `OPENELIS_PUBLIC_URL` are consumed **only** by `reconcile-sso.sh`, to set the *Keycloak client's*
  `rootUrl`/`redirectUris`/`webOrigins` — never passed into either backend container. This means
  OpenMRS's own redirect_uri is derived from the incoming request, not fixed to one origin.
  **Demonstrated:** `openmrs-gateway` and `openelis-webapp` both route purely by path — an arbitrary,
  unconfigured `Host` header still reaches the real backend on both. One OpenMRS instance can
  therefore legitimately answer for two origins. The equivalent claim for OpenELIS's SAML ACS URL
  derivation is **not verified** — its SP integration ships compiled, with no source in this repo —
  and is called out below as the one open implementation risk, not assumed favorable.
- Session cookie posture, measured directly and again through Caddy: OpenMRS's `JSESSIONID` carries
  only `HttpOnly` — no `Secure`, no `SameSite`, in both places. A cookie-only `POST` to
  `/ws/rest/v1/patient` (no `Authorization` header, no CSRF token) reached real validation logic
  despite an `org.owasp.csrfguard.CsrfGuardFilter` in the filter chain — that filter does not cover
  this path. OpenELIS's `JSESSIONID` carries `Secure; HttpOnly; SameSite=Strict` in both places, and
  its login page embeds a real Spring Security `_csrf` token; per-endpoint CSRF enforcement on
  authenticated **business** writes (as opposed to the login form) was not exercised and is not
  assumed either way.
- `DELETE /ws/rest/v1/session` genuinely invalidates OpenMRS's own session (confirmed: the same
  cookie gets `401 Session timed out` immediately after). No SAML Single Logout is configured for the
  `openelis` Keycloak client, and neither client has `frontchannelLogout` configured — there is no
  coordinated logout across OpenMRS, OpenELIS, and the Keycloak SSO session today. An OpenELIS logout
  endpoint was not identified or verified.
- Caddy already has three origins: `emr.$BASE_DOMAIN` (OpenMRS gateway, full SPA+API), `lab.
  $BASE_DOMAIN` (OpenELIS, path-split between the webapp and its static frontend), and `id.
  $BASE_DOMAIN` (Keycloak) — the last one's own comment already ties its existence to SSO
  activation. No `app.$BASE_DOMAIN` exists yet.

### Decision

**No service account, anywhere, ever stands in for a staff user for a write.** A read-only BFF using
its own account was considered and rejected — it would replace each user's own OpenMRS read
authorization with "whatever the BFF's account can see," which is a bigger authorization hole than
the audit-identity problem it would avoid, and it is exactly the "identity silently falls back to a
service identity" failure this ticket's own acceptance criteria (§3, §12) rule out. **There is no
BFF in V1.**

Instead: **the browser authenticates directly with OpenMRS (OIDC) and OpenELIS (SAML) through their
own already-built browser-redirect flows**, each establishing its own native session cookie. The
Daher UI never intermediates that handshake and never sees a password, a client secret, or a token
it could replay as someone else.

**Same-origin, achieved by proxying each backend's own API prefix, not its SPA.** The Daher UI gets
its own origin, `app.$BASE_DOMAIN`, served by Caddy. Under that same origin, Caddy also
reverse-proxies exactly the prefixes the Daher UI's own calls need — `/openmrs/*` to
`openmrs-gateway:80`, `/OpenELIS-Global/*` to `openelis-webapp:8443` — the same prefixes the native
session cookies are scoped to (`Path=/openmrs`, `Path=/OpenELIS-Global`). `emr.$BASE_DOMAIN` and
`lab.$BASE_DOMAIN` are untouched and keep serving the native SPAs at their own origins for anyone who
uses them directly; nothing about ADR-008's "subdomains, not path prefixes" rule for the *SPAs*
is violated, because the Daher UI never tries to own or rewrite the SPA's own root — only the API
prefix behind it, which is already a distinct, path-scoped surface today. This makes the login
redirect land the session cookie on the same origin the Daher UI's own `fetch()` calls use, with no
CORS, no `SameSite=None`, and no relaxation of OpenELIS's already-good `SameSite=Strict` posture —
same-origin is not merely nicer here, `SameSite=Strict` makes it close to mandatory.

**Composed reads, if ever needed, stay user-scoped.** If a screen genuinely needs data from both
systems at once, that composition happens in the browser (two same-origin calls, one per backend),
or, if a server-side composer is later justified, it must be a strict pass-through that forwards the
user's own upstream session rather than substituting a credential of its own. That is a narrower
question than this ADR needs to answer for V1, since UI-002 built exactly one read-only screen and
UI-003 builds none.

**Audit identity, satisfied by construction.** Every request OpenMRS or OpenELIS receives carries
the real user's own session — `auditInfo.creator` (UI-001A, §13a.6) and `currentProvider` (measured
in this ticket: `/ws/rest/v1/session` already distinguishes `user` from `currentProvider`) are
populated from that real identity, never a service account's.

### What must change to build this — mechanical, not clinical, none of it done here

- Add an `app.$BASE_DOMAIN` block to `caddy/Caddyfile`, handling `/openmrs/*` → `openmrs-gateway:80`
  and `/OpenELIS-Global/*` → `openelis-webapp:8443` (mirroring `lab.`'s existing TLS/trust-anchor
  handling for the latter), plus whatever serves the Daher UI's own static assets at that origin.
- Add `https://app.$BASE_DOMAIN/*` to the `openmrs` client's `redirectUris`/`webOrigins` in
  `reconcile-sso.sh` (a list append — the client already carries three entries). Do the same for the
  `openelis` client **only after** its SAML ACS derivation is verified to be per-request rather than
  fixed — see the open risk below.
- Give OpenMRS's session cookie explicit `Secure`/`SameSite` attributes (Tomcat `context.xml` or a
  Caddy `header_down` rewrite on the `app.`/`emr.` routes) before any Daher-built write relies on
  that cookie, and require a custom header (e.g. `X-Requested-With`) on Daher-originated
  state-changing calls to OpenMRS — a plain cross-site HTML form cannot set one, which closes the gap
  the measured CSRF finding above identified without waiting on Tomcat/CSRFGuard configuration this
  ADR does not control.
- For any Daher-built write against OpenELIS, fetch and echo its `_csrf` token like its own UI does,
  rather than assuming the cookie's `SameSite=Strict` alone is sufficient.
- Design real coordinated logout (Keycloak front-channel/back-channel logout wired to both clients,
  or an explicit "you are not fully logged out" UI state) before any write ships — today, ending a
  Daher session does not prove either backend's session or the Keycloak SSO session ended, which is
  exactly the shared-workstation risk (§9) this ADR is required to flag, not solve.

**None of the above is "activating production SSO."** `SSO_ENABLED` stays `false`, and
`keycloak/realm-config/daher-realm.json` is not edited by this ADR — these are the exact,
named additions a future ticket makes once BLOCKER-5's role mapping is approved.

### What remains genuinely open

- **UNRESOLVED — requires BLOCKER-5.** The live end-to-end proof (a real browser completes the
  Keycloak login, OpenMRS/OpenELIS identify that same human) needs `SSO_ENABLED=true` and an approved
  role mapping. Neither was done here, and no staff identity was invented.
- **OpenELIS's SAML ACS derivation** — *superseded by the UI-005 note below: resolved, per-request,
  app-origin derived.* (Left here rather than deleted, since this ADR's implementation notes are
  append-only — see UI-005 for the measured evidence.)
- **Coordinated logout** — *superseded by the UI-005 note below: a working design is now proven,
  not just undesigned — see UI-005 for what's verified and what's not yet wired in.*
- **The reception/front-desk role** (§24a of `docs/UI-001-CLINICAL-UX.md`) is untouched by this ADR.
  Whatever the human decision there turns out to be, this architecture enforces it exactly the way it
  enforces every other role today: server-side, by OpenMRS/Keycloak, never by the Daher UI's own
  navigation.

### Implementation note (UI-004, 2026-08-22)

The mechanical foundation this ADR specified is now built and verified live against the pinned
stack: `app.$BASE_DOMAIN` exists in `caddy/Caddyfile`, proxying `/openmrs/*` to `openmrs-gateway:80`
with the session cookie hardened in transit (`Secure; SameSite=Lax`, verified via a real
`Set-Cookie` header) and a CSRF guard (`X-Daher-App-Request: 1` required for `POST`/`PUT`/`PATCH`/
`DELETE`, verified: absent → `403` before reaching OpenMRS; present → reaches OpenMRS's own
validation). `keycloak/reconcile-sso.sh` now adds `app.$BASE_DOMAIN` to the `openmrs` client's
`redirectUris`/`webOrigins` alongside — not replacing — `emr.$BASE_DOMAIN`'s entries, run twice
against the live realm to confirm idempotency. `emr.`/`lab.`/`id.` and the `openelis` client are
confirmed byte-for-byte unaffected. Full detail, including the OpenELIS SAML ACS-URL investigation
(still `UNRESOLVED`, so OpenELIS is not mounted under `app.`), is in `PROJECT_STATE.md`'s UI-004
entry. No clinical write was enabled and `SSO_ENABLED` was not touched.

### Implementation note (UI-005, 2026-08-22) — measured protocol evidence, disposable environment only

UI-004 left OpenELIS's ACS derivation and coordinated logout as open questions, answerable only by
running the actual protocols. Doing that against the real facility SSO config was explicitly
forbidden, so this ticket built a throwaway Keycloak realm plus disposable OpenMRS and OpenELIS
instances (own Docker network, own volumes, synthetic realm `daher` with one synthetic user
`ui005-synthetic-user`, no real staff identity, no patient data) and drove the actual redirects with
a scripted HTTP client. `SSO_ENABLED` on the real stack was not touched. The disposable containers
and the throwaway realm/client secrets used to test them are not part of this repository. They
remain running in this environment as of this writing — this sandbox's Docker daemon denies
`stop`/`kill`/`restart` (confirmed again this ticket, the same constraint UI-004 hit), so they could
not be torn down from here. The exact manual removal command is recorded in `PROJECT_STATE.md`'s
UI-005 entry.

**OpenELIS — APP-ORIGIN SUPPORTED, conditional on a Keycloak client change this ticket did not make.**
Hitting `/OpenELIS-Global/saml2/authenticate/keycloak` with different `Host` headers produced a
different `AssertionConsumerServiceURL` each time — `https://<host>/OpenELIS-Global/login/saml2/sso/keycloak`
— proving OpenELIS's Spring SAML2 module derives its ACS **per-request from the incoming Host**, not
from a fixed lab-origin value. This directly resolves UI-004's open question and rules out the
"fixed to lab origin" hypothesis. The full round trip (AuthnRequest → Keycloak login → SAMLResponse
posted back to the app-origin ACS → real `JSESSIONID`) completed end to end for the synthetic user,
with the cookie posture `Secure; HttpOnly; SameSite=Strict`, and a subsequent authenticated read
showed the synthetic username in the page — the session is genuinely bound to that identity, not a
service account. **The conditional:** this only worked after adding `https://app.<disposable
origin>/*` to the disposable `openelis` SAML client's `redirectUris` in Keycloak — without it,
Keycloak rejected the AuthnRequest with `invalid_redirect_uri`. On the real stack,
`reconcile-sso.sh` deliberately leaves the `openelis` client untouched (a UI-004 decision, kept in
force by this ticket per §5/§13 — no doubt-free basis yet to touch the real client, see the OpenMRS
finding below). Mounting OpenELIS under `app.$BASE_DOMAIN` for real requires that same redirect-URI
addition against the real `openelis` client — a small, named, reviewable change, not a
rearchitecture. **A second condition, untested here:** this ticket's captures went straight to
OpenELIS's TLS port, so the reconstructed scheme was always `https`. Production Caddy terminates TLS
in front of OpenELIS; if that hop to the backend were plain HTTP without `X-Forwarded-Proto: https`
forwarded and honored, the derived ACS would come out `http://app...` and fail Keycloak's `https://`
redirect-URI matching. Since UI-004 deliberately added no OpenELIS route to the `app.` block yet,
there is nothing to check today — but confirming that header is forwarded correctly is the second
condition alongside the redirect-URI addition, not an assumption to carry into that future ticket.

**OpenMRS OIDC — STILL UNRESOLVED, exact blocker identified.** The disposable OpenMRS backend
(image `daher-health/openmrs-backend:3.7-off`, `oauth2login` 1.5.0, `SSO_ENABLED=true` from first
boot against a fresh, empty database) correctly redirects unauthenticated browser requests to
`/oauth2login` (`OAuth2LoginRequestFilter`, wired via `config.xml`, confirmed working). But
`/oauth2login` itself returns **404** — nothing serves it. The boot log shows
`java.lang.IllegalStateException: Filters cannot be added to context [/openmrs] as the context has
been initialised`, thrown from `WebComponentRegistrar.setServletContext` during the module's one
context refresh. The module's `webModuleApplicationContext.xml` declares a Spring Security
`<security:http>` chain (default bean name `springSecurityFilterChain`) with
`login-page="/oauth2login"` — this is the only thing that could ever serve that path — but the
deployed WAR's `web.xml` has no `DelegatingFilterProxy`/`springSecurityFilterChain` entry, and this
exact exception is absent from the live stack's own boot logs (checked directly: zero occurrences,
`SSO_ENABLED=false` there). So the failure is neither ambient OpenMRS noise nor a disposable-setup
mistake (`oauth2.properties` was confirmed correctly written by `configure-sso.sh` with the
disposable Keycloak's real URLs) — it is this module's Spring-Security-managed filter chain failing
to register on a fresh first boot with SSO enabled from the start. Untested: whether enabling SSO
against an **already-initialized** OpenMRS database (rather than an empty one) avoids this exact
race — that variable was not exercised and is not assumed either way. Per §13/§17, this is reported
as the exact blocker rather than worked around; patching module wiring to force the chain in would
be exactly the "fight the SAML/OAuth2 implementation" this ADR's process forbids.

**Correction (UI-006, 2026-08-23) — the attribution above is wrong; the symptom and blocked
conclusion are not.** UI-006 ran the fresh-DB-vs-initialized-DB experiment this note flagged as
untested, and went further into primary source. Two things in the paragraph above do not hold up:

1. **The `IllegalStateException` is not SSO-specific.** UI-006 reproduced it on a disposable OpenMRS
   instance with `SSO_ENABLED=false`. It is also non-deterministic — a second, otherwise-identical
   restart of the same disposable stack did not show it at all. It belongs to `legacyui`'s own
   `WebComponentRegistrar` (confirmed by extracting the class from `legacyui-2.1.0.omod`, not
   OpenMRS core), registering `legacyui`'s own three filters
   (`redirectAfterLoginFilter`/`forcePasswordChangeFilter`/`adminPageFilter`) — nothing to do with
   `oauth2login`. The exception is caught and printed via `e.printStackTrace()` inside that same
   method (confirmed by decompiling it) and does not abort module loading. The live stack's apparent
   absence of this exception is not log rotation either — its `openmrs-backend` log was checked
   directly and starts at `Initiating OpenMRS startup`, the genuine first line, with zero occurrences
   in 1514 lines. It is simply a flaky, unrelated `legacyui` timing race that happened to co-occur in
   the same log capture as the real finding below.
2. **The real cause has nothing to do with `springSecurityFilterChain` failing to register — it
   never needed to.** Reading `OAuth2LoginController`'s actual source (pinned commit
   `2770233627343a53719ec981e997d94b1d90b7ad`) shows `/oauth2login`'s handler is a plain
   `@RequestMapping`-annotated Spring MVC method, dispatched the same way every other OpenMRS
   controller is — not through Spring Security's filter chain at all. The real, measured mechanism,
   in three parts: **(a)** OpenMRS's `openmrs` `DispatcherServlet` is mapped only by file extension
   (`.htm`/`.form`/`.list`/`.json`/`.field`/`.portlet`/`.page`/`.action`, added dynamically by
   `legacyui`) plus a static `/ws/*` prefix — nothing maps the bare, extensionless `/oauth2login`
   path, so it 404s before Spring ever sees it. **(b)** `OAuth2LoginRequestFilter`'s own exclusion
   list (`servletPaths=/oauth2login,/oauth2logout`) matches on `HttpServletRequest.getServletPath()`,
   which equals that literal string only for a request that hit an exact servlet mapping — so the
   one path the filter won't redirect is the one path nothing serves, and every extension-suffixed
   alias that *is* served gets caught by the filter and bounced back to the dead path (measured:
   `/oauth2login.htm`, `.form`, `.list`, `.json` all `302` back to `/oauth2login`). **(c)** Hitting
   the controller directly via the one mapping that does reach it —
   `/ws/oauth2login`, whose `pathInfo` Spring matches against `@RequestMapping("/oauth2login")` —
   while authenticated (to bypass (b)) reaches real controller logic and produces a measured `500`:
   `org.springframework.security.oauth2.client.resource.UserRedirectRequiredException: A redirect is
   required to get the users approval`. That exception is Spring Security OAuth2 1.x's designed
   signal to redirect the browser to the identity provider — and it is supposed to be caught by
   `oauth2ClientContextFilter`, the exact bean `webModuleApplicationContext.xml` declares inside its
   `<security:http>` block. Nothing in `config.xml` — the only mechanism proven to reliably wire a
   module's filter into OpenMRS's real request pipeline — ever invokes that filter. So the module's
   own designed OAuth2 flow does trigger correctly; the one piece that would turn it into an actual
   redirect to Keycloak is simply never wired into the path a request takes.

This changes what a fix needs to do, but not the ticket-relevant conclusion: OpenMRS OIDC is still
blocked, on both a fresh database and a database already initialized with SSO disabled and then
enabled on restart (both tested; the 404/500 pattern above was identical in each). See UI-006's own
entry in `PROJECT_STATE.md` for the full evidence chain, the Case-B-specific `oauth2login.started`
global-property finding, and the precise candidate fix.

**Coordinated logout — PARTIAL as currently implemented, but a working fix is now proven, not just
theorized.** OpenELIS's own local logout is real: a CSRF-protected POST to `/Logout` genuinely
invalidates the `JSESSIONID` (replaying the old cookie afterward gets redirected to `LoginPage`, not
served). Left at that, Keycloak's own SSO session survives untouched — measured directly: after
OpenELIS logout, re-running the identical SP-initiated SAML flow **in the same browser session** got
a fresh, valid `SAMLResponse` back with **zero credential prompt**, `KEYCLOAK_SESSION`/
`AUTH_SESSION_ID` silently re-authenticating the synthetic user. This is exactly the
shared-workstation failure this ADR flagged as a risk, confirmed present in the naive
"just call each app's own logout" design. But a genuine fix was found and verified, not merely
theorized: navigating (a real browser redirect, not a background call) to Keycloak's own
`/realms/daher/protocol/openid-connect/logout` and **submitting the confirmation page it returns**
(a `session_code`-bearing form Keycloak renders when no `id_token_hint` is supplied) does terminate
the SSO session — measured directly: an SP-initiated login attempted afterward in the same session
required credentials again, no silent re-auth. A variant supplying `client_id=openelis` +
`post_logout_redirect_uri` but still no `id_token_hint` was fetched and also rendered a confirmation
page rather than skipping it (not submitted, since the no-`client_id` path above already proved
submission works) — the click itself is unavoidable without a real ID token either way. **Untested,
and a condition on using `post_logout_redirect_uri` for real:** Keycloak validates that parameter
against the client's own registered post-logout redirect list, and the real `openelis` client has
none registered (`post.logout.redirect.uris` unset, per UI-003) — a follow-on ticket wiring this in
would need to either register one or land the user on Keycloak's own default page after the click.
**So the coordinated-logout
design this ADR can now specify with evidence:** after a user's local application logout succeeds,
redirect their browser (not a `fetch`/XHR, which cannot complete a cross-origin navigation-based
form submission the way Keycloak's page expects) to Keycloak's end-session endpoint; the resulting
one extra confirmation click is real but genuinely closes the SSO session — no OpenMRS OIDC fix and
no SAML Single Logout configuration are required to get this working today. **What remains
undone:** nothing in the codebase currently performs this redirect — "Déconnexion" today would only
call each app's own local logout, leaving the SSO session alive exactly as measured above. Wiring
the Keycloak end-session redirect into the real logout action is a small, well-scoped follow-on
ticket, not a research question anymore. A silent (no confirmation click) version remains blocked on
the OpenMRS `oauth2login` finding above, since only a valid `id_token_hint` — obtainable only from a
completed OIDC login — skips the confirmation page.

**Negative controls run, exact results:** unauthenticated read of `/OpenELIS-Global/Home` → redirect
to `LoginPage` (no session). Bogus `JSESSIONID` → same redirect (no session inherited). Logout
without a CSRF token → `302` to `Home?access=denied`, session left alive (CSRF is genuinely
enforced on this endpoint, not just present as a form field) — the same call with a valid token
succeeded. Silent-relogin control above. OpenMRS's own unauthenticated-read and wrong-ACS controls
were not exercised because the OIDC flow itself never completes (see the blocker above) — nothing to
control against yet.

Full evidence transcript (commands, raw HTTP responses) is in this session's transcript; only this
summary and `PROJECT_STATE.md`'s UI-005 entry are committed. No production Caddyfile, compose file,
or realm-config change was made — the disposable-environment findings inform, but do not yet justify,
any of the real-client changes described above.

**Update (UI-007, 2026-08-23) — the bridge UI-006 designed works; two independent, separate blockers
were found behind it.** UI-007 built and proved the minimal `OAuth2LoginBridgeFilter` UI-006
specified: registered in `config.xml` against the exact literal path `/oauth2login` only, it looks
up the module's own unmodified `oauth2ClientContextFilter` Spring bean by name
(`Context.getRegisteredComponent("oauth2ClientContextFilter", Filter.class)` — a by-name lookup,
distinct from the by-type lookup implicated below) and forwards through it to `/ws/oauth2login`,
which the core `openmrs` servlet's existing `/ws/*` mapping already routes to
`OAuth2LoginController`. On a disposable Keycloak+OpenMRS+MariaDB stack this produced a genuine
`302` to Keycloak's real authorization endpoint — the dispatch premise holds; the forward does not
loop. Source, patch diff, and checksums are recorded in
`openmrs-config/patches/oauth2login-bridge/` (not wired into `openmrs-config/Dockerfile` — see
below for why).

Driving the flow further (synthetic Keycloak user, full authorization-code exchange) reached
`OAuth2LoginController.login()`'s `Context.authenticate(new OAuth2TokenCredentials(userInfo))`
call and failed there with OpenMRS logging `Multiple authentication schemes overrides are being
provided, this is currently not supported. Sticking to OpenMRS default authentication scheme.`
Reading `Context.setAuthenticationScheme()`'s source confirms the mechanism: a by-type
`getBean(AuthenticationScheme.class)` Spring lookup throws `NoUniqueBeanDefinitionException` when
more than one bean implements that interface, and OpenMRS's designed fallback is to silently keep
the default `UsernamePasswordAuthenticationScheme` — which cannot process `OAuth2TokenCredentials`.
Decompiling every module bundled in the disposable image found the second bean:
`org.openmrs.module.authentication`'s `DelegatingAuthenticationScheme`, a `@Component`-annotated
class implementing the same `org.openmrs.api.context.AuthenticationScheme` interface as
`oauth2login`'s own `OAuth2UserInfoAuthenticationScheme` (also `@Component`-annotated). Both are
legitimate, intentional beans from their respective modules; neither is wrong on its own. No
scheme-selection global property exists to disambiguate them (checked: `authentication`'s own
`authentication.scheme` GP configures which scheme `DelegatingAuthenticationScheme` delegates to
*internally*, but does not affect which bean Spring's by-type lookup returns — the ambiguity is
resolved before that logic ever runs). Setting `authentication.started=false` on the disposable
database (a global-property flip, not a schema or code change) removed the competing bean and the
authentication-scheme override was accepted on the next boot
(`Context.setAuthenticationScheme(198) | An authentication scheme override was provided.`) — this
was done, and reverted, only in the disposable environment. **This is an upstream
compatibility gap between `oauth2login` 1.5.0 and the `authentication` 2.3.0 module the O3 3.7.x
reference-application backend bundles by default — not something UI-007's bridge patch caused or
can fix, and not something to resolve by patching either module's Spring wiring.
`Human Decision Required: Yes`** — disabling `authentication` removes `ForcePasswordChangeFilter`
and its 2FA/secret-question/lockout surface; forcing `oauth2login`'s bean `@Primary` requires
patching a second module, which is exactly the "second source patch, second fork" this ticket's
minimal-bridge framing was meant to avoid. See `PROJECT_STATE.md`'s UI-007 entry for the full
account, including the two further blockers found only after this one was worked around
disposably: `oauth2login`'s own request filter removes any non-`/ws/rest/v1/session` unauthenticated
path once the module is active (including admin Basic Auth and the legacy login form — break-glass
is not preserved, contradicting this ADR's and the ticket's expectation that it would be), and the
already-documented `oauth2login.started=false` persistence does not self-heal when `SSO_ENABLED`
is later flipped back to `true` (confirmed by recreating the disposable container twice: OFF →
`started=false` and the module unloaded via `OAuth2LoginActivator.willStart()`'s own
`ModuleFactory.unloadModule()` call; ON again on the same database → the module did not return,
`/oauth2login` `404`d again). None of these three are solvable by extending UI-007's bridge patch;
each needs its own human decision before real facility SSO can be turned on.

---

## ADR-028 — `Daher Maternity Admission`, a dedicated Daher-owned OpenMRS Visit type

**Date:** 2026-08-25 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** BLOCKER-7 / NEEDS LIBAN #20 (raised by UI-013's own pre-write gate) found that no
approved OpenMRS Visit type represented a Daher maternity admission. UI-001A's `Facility Visit` was
explicitly recorded as "the closest generic match, not a clinical decision," and every later
admission ticket (ADR-017C, ADR-017D, ADR-017E2) reused it unchanged only as an inherited
authorization-test fixture. Liban's decision: create a dedicated Daher-owned Visit type,
**`Daher Maternity Admission`**, rather than promote any of the five upstream generics
(`Facility Visit`, `Home Visit`, `OPD Visit`, `Offline Visit`, `Group Session`). None of the five is
modified, renamed, retired, or deleted.

**Mechanism confirmed before writing any config, per the ticket's own instruction not to assume a
CSV path or schema.** The pinned base image (`openmrs/openmrs-reference-application-3-backend:
3.7.x`, digest-pinned in `openmrs-config/Dockerfile`) ships **Initializer 2.12.0**
(`/openmrs/data/modules/initializer-2.12.0.omod`, confirmed by listing the running container's
modules directory). Its `initializer-api-2.12.0.jar` was extracted and disassembled directly
(`jar xf` + `javap -c -v`, not read from documentation):

- `org.openmrs.module.initializer.Domain` is a Java `enum` with a `VISIT_TYPES` constant. Its
  `getName()` method — bytecode-confirmed, not assumed — computes the on-disk domain folder name as
  `name().replace("_", "").toLowerCase()`, i.e. `VISIT_TYPES` → **`visittypes`**. This is the exact
  same mechanism already backing this repo's committed `locations`/`roles`/`globalproperties`
  domains (`LOCATIONS`→`locations`, `ROLES`→`roles`, `GLOBAL_PROPERTIES`→`globalproperties`),
  confirmed by the same disassembly technique, not by analogy alone.
- `org.openmrs.module.initializer.api.visittypes.VisitTypeLineProcessor extends BaseLineProcessor
  <VisitType>` — a first-class, supported CSV domain, not a workaround. Its `fill()` method
  (bytecode-confirmed) reads exactly two domain-specific columns, `name` and `description` — both
  matched case-insensitively per `BaseLineProcessor.createIndexMap()`'s own `.trim().toLowerCase()`
  normalization — plus the generic `uuid`/`void/retire` columns every `BaseLineProcessor` domain
  shares (also bytecode-confirmed against `BaseLineProcessor.class`'s own `HEADER_UUID="uuid"`,
  `HEADER_VOID_RETIRE="void/retire"` constants). This is the identical CSV shape already used by
  `openmrs-config/initializer/locations/locations-daher.csv` and
  `openmrs-config/initializer/roles/roles-daher.csv` — no new schema was invented.

**Decision — the exact artifacts committed:**

| Layer | Artifact | Contents |
|---|---|---|
| OpenMRS | Visit type `Daher Maternity Admission` (`openmrs-config/initializer/visittypes/visittypes-daher.csv`, Initializer-provisioned, domain `visittypes`) | Fresh Daher-owned UUID `194bf546-68d7-42c0-8371-5b0f2b1cf5f3`, `Description` naming this as the Daher-owned replacement for using `Facility Visit` as anything beyond an experimental fixture |
| Compose | New bind mount, `docker-compose.yml`, additive, alongside the existing three `zz-daher-health` domain mounts | `./openmrs-config/initializer/visittypes:/openmrs/distribution/openmrs_config/visittypes/zz-daher-health:ro` |

**Explicitly not done:** no upstream Visit type (`Facility Visit`, `Home Visit`, `OPD Visit`,
`Offline Visit`, `Group Session`) modified, voided, or retired — the CSV adds exactly one new row
and touches nothing else; no patient, Visit, or Encounter written; no real staff or SSO activation;
UI-013's admission workflow itself is not built here — this ticket only clears its pre-write gate.

**Live proof — fresh database, already-initialized database, and idempotent second pass — is
recorded in this ticket's own `PROJECT_STATE.md` log entry**, including the exact runtime query
confirming exactly one `Daher Maternity Admission` result and zero drift on the five upstream types.

**Consequence.** BLOCKER-7 / NEEDS LIBAN #20 is closed. UI-013 may resume against
`Daher Maternity Admission` (name) / `194bf546-68d7-42c0-8371-5b0f2b1cf5f3` (UUID) and the
already-committed `Admission` encounter type and `Maternité` location.

---

## ADR-029 — Make the existing "Admission" Encounter type reproducible on fresh deployments

**Date:** 2026-08-26 · **Status:** ACCEPTED · **Decided by:** Liban

**Context.** UI-013's live closure pass found that a genuinely fresh OpenMRS database, booted
against the current committed Daher configuration, has **zero** Encounter types beyond the core
`Attachment Upload` — no `Admission` type at all, the one UI-013's Encounter-half payload requires.
Recorded as **BLOCKER-8 / NEEDS LIBAN #21**.

**Root cause, re-confirmed with evidence before this ticket touched anything:**

- The real main stack's `encounter_type` table shows **all 19 encounter types** (`Admission`,
  `Vitals`, `Discharge`, …) created within the same 2-second window, `2026-08-11 09:35:50`–`52`, by
  `creator=2` (the daemon/system account) — a single historical bulk upstream reference-content
  import, not Daher config. `visit_type` shows the identical pattern one second earlier for the
  five upstream Visit types. Only `Attachment Upload` (core platform, not reference-app content)
  falls outside that cluster.
- No file in this repo defines it: `openmrs-config/initializer/` had no `encountertypes/` domain
  directory before this ticket, and the base image's own
  `/openmrs/distribution/openmrs_config/encountertypes/` directory exists but ships **empty**.
- UUID `e22e39fd-7db2-45e7-80f1-60fa0d5a4378` has been the same value across every characterization
  of it this repo has ever done (UI-001A, ADR-017C, ADR-017D, ADR-017E2, this ticket) — stable for
  this pinned upstream content, just not reproducible from an empty database today.

**Decision.** Liban approved reproducing the **existing** upstream semantic — same name (`Admission`),
same UUID (`e22e39fd-7db2-45e7-80f1-60fa0d5a4378`) — as committed, reproducible Daher config. This is
explicitly **not** a new clinical concept: no `Daher Admission` type, no rename, no change to what
the type means. It is a deployment-reproducibility fix for content that already existed and was
already in use (ADR-017C, ADR-017D, ADR-017E2 all proved admission against this exact type), which
historical/reference-content seeding cannot be relied on to reproduce for a future fresh deployment.

**Mechanism confirmed by disassembly, not assumed to mirror ADR-028's `visittypes` domain** (`jar
xf` + `javap -c` against the pinned `initializer-2.12.0.omod`'s `initializer-api-2.12.0.jar`):
`Domain.ENCOUNTER_TYPES.getName()` computes `"encountertypes"` (same `name().replace("_","")
.toLowerCase()` mechanism, bytecode-confirmed) — and the distribution's own `encountertypes/`
directory (present, empty) corroborates this independently. `EncounterTypeLineProcessor extends
BaseLineProcessor<EncounterType>` — a **different shape** from `VisitTypeLineProcessor`: it calls
`CsvLine.getName(true)` (name required) and `.get("description")` (not `.getString()`), and
additionally supports two **optional** columns, `view privilege` and `edit privilege`
(`HEADER_VIEW_PRIV`/`HEADER_EDIT_PRIV`, bytecode-confirmed literal header strings) — if non-empty,
resolved via `Utils.fetchPrivilege` and thrown as `IllegalArgumentException` if the named privilege
doesn't exist. The existing `Admission` row has `view_privilege`/`edit_privilege` both `NULL` in the
live database (`SELECT … FROM encounter_type`, confirmed directly), so both CSV columns are left
blank — preserving the exact existing state rather than introducing a privilege scope that was
never there.

**Decision — the exact artifact committed:**

| Layer | Artifact | Contents |
|---|---|---|
| OpenMRS | Encounter type `Admission` (`openmrs-config/initializer/encountertypes/encountertypes-daher.csv`, Initializer-provisioned, domain `encountertypes`) | Existing UUID `e22e39fd-7db2-45e7-80f1-60fa0d5a4378`, existing name `Admission`, existing description ("Indicates that the patient has been admitted for inpatient care, and is not expected to leave the hospital unless discharged."), `View privilege`/`Edit privilege` left blank to match the existing `NULL`/`NULL` state, `Void/Retire` empty |
| Compose | New additive bind mount in `docker-compose.yml`, alongside the existing four `zz-daher-health` domain mounts, none of which were touched | `./openmrs-config/initializer/encountertypes:/openmrs/distribution/openmrs_config/encountertypes/zz-daher-health:ro` |

**Explicitly not done:** no new Encounter type concept (`Daher Admission` or otherwise); no
unrelated encounter type provisioned, renamed, or retired; no upstream Visit type touched; no
change to `Daher Maternity Admission` (ADR-028) or `Maternité` (ADR-017B); no patient/Visit/Encounter
write; no real SSO activation; no real staff assigned; no OpenELIS touched.

**Live proof, existing-database gate and fresh-database gate — recorded in full, including an
honestly-disclosed environmental blocker, in `PROJECT_STATE.md`'s own BLOCKER-8 closure entry.**

**Consequence.** BLOCKER-8 / NEEDS LIBAN #21 is closed — decision, mechanism, and existing-database
reproducibility are all proven. **Liban reviewed the fresh-database boot failure and accepted it as
a DH-017 fresh-deployment verification item, not a gap in this decision**: the Initializer
mechanism was confirmed directly from the pinned module, the exact existing semantic/UUID was
preserved, existing-database provisioning and a second idempotent pass both succeeded, the CI guard
and its five controls all behaved correctly, and the fresh-boot failure reproduced identically on a
config-identical control with the new mount removed — so the `/initialsetup` failure does not
implicate this config. DH-017's eventual fresh-VM walkthrough is where a genuine fresh boot gets
demonstrated. UI-013 itself remains uncommitted and unaffected; its canonical Encounter UUID
(`e22e39fd-7db2-45e7-80f1-60fa0d5a4378`) required no change.

---

## ADR-017F — `Organizational: Nurse` closes over 302 of 345 privileges; clinician identity narrowed

**Date raised:** 2026-08-26 · **Date decided:** 2026-08-26 (Bug #24 ticket) · **Status:** ACCEPTED ·
**Decided by:** Liban — **Option A**: the Daher clinician identity is `Provider` + `Daher Clinician
Admission`. `Organizational: Nurse` is excluded from the Daher clinician authorization model going
forward; no broader nurse permissions are implemented. Live proof of this exact narrow identity is
appended below (§ Live closure proof, 2026-08-26) — this entry was not marked ACCEPTED until that
proof succeeded, per this ticket's own instruction not to claim closure early.

**Context.** ADR-017D closed with a disclosed, out-of-scope gap: the synthetic clinician identity
(`Provider` + `Organizational: Nurse` + `Daher Clinician Admission`, per ADR-017's own committed
mapping) authenticated with "the full nine-privilege admission set plus (unexpectedly) hundreds
more," traced to `Organizational: Nurse` inheriting `Application: Enters ADT Events` (among three
others), which itself inherits `Privilege Level: High`. That finding was reported but never
enumerated, categorized, or tested for removal — tracked here as ADR-017F. This ticket's job is not
to count privileges but to determine whether `Organizational: Nurse` is actually necessary for any
proven Daher clinician workflow, and to establish the narrowest safe role model before real
clinician SSO activation.

**§3/§4 — the role graph, DB- and REST-verified, both directions of a naming trap resolved before
trusting either.** `role_role`'s raw columns are `parent_role`/`child_role`; a naive read (`parent_role`
as "the role", `child_role` as "what it inherits") is **backwards** — confirmed by cross-checking
against the OpenMRS REST API's own `inheritedRoles` representation (`GET /role/{uuid}?v=full`), which
disagreed with the naive SQL join and settled it: **`child_role` is the inheriting role, `parent_role`
is what it inherits** (Hibernate's mapping predates and inverts the plain-English column names). Read
against the main stack's own database (`daher-health-openmrs-db-1`, carrying the full committed
Initializer config plus upstream reference content — not `openmrs-ui009c`, whose `Daher Reception` row
ADR-017C/D already documented as a JVM-cache-stuck, privilege-less husk):

```
Organizational: Nurse (1 direct: "App: Service Queues Clinician")
      ↓ inherits
Application: Enters ADT Events, Application: Enters Vitals,
Application: Records Allergies, Application: Uses Patient Summary
      (0 direct privileges each)
      ↓ each inherits
Privilege Level: High
      ↓
301 direct privileges — 87.2% of the 345 privileges that exist in this system
```

`Provider` — confirmed **0** direct privileges, no inherited roles (matches PROJECT_STATE.md's
existing citation; re-verified, not re-cited from memory). `Daher Clinician Admission` — confirmed
exactly the 9 committed privileges, no inherited roles. `Daher Reception` — confirmed exactly its 3
committed privileges, no inherited roles.

**§4 — the difference, computed, not asserted:**

```
EFFECTIVE_CURRENT   = Provider(0) ∪ Organizational: Nurse(closure, 302) ∪ Daher Clinician Admission(9)
                    = 302   (all 9 approved admission privileges are already a strict subset of the
                              Nurse closure — confirmed by set intersection, not eyeballed)
APPROVED_ADMISSION  = 9     (Get Patients, Get Visit Types, Add Visits, Get Visits,
                              Get Visit Attribute Types, Get Beds, Get Admission Locations,
                              Add Encounters, Get Encounters — ADR-017D's own committed set)
EXTRA_PRIVILEGES    = 293
```

**§5 — the 293 extras, categorized by real name, not summarized as "Privilege Level: High":**

| Category | Representative privileges | Risk |
|---|---|---|
| Destructive delete/purge (30) | `Delete Patients`, `Delete Users`, `Delete Encounters`, `Delete Visits`, `Delete Orders`, `Delete People`, `Delete Relationships`, `Delete Patient Identifiers/Programs`, `Purge Cashier Bills/Metadata/Timesheets`, `Purge Queues/Queue Entries/Rooms`, `Purge Procedures/Procedure Types/Field Types`, `Delete Cohorts`, `Delete Concept Proposals`, `Delete Conditions`, `Delete Diagnoses`, `Delete HL7 Inbound Archive/Exception/Queue`, `Delete Medication Dispense`, `Delete Notes`, `Delete Observations`, `Delete Report Objects`, `Delete Reports`, `Delete Tasks` | High — irreversible data loss, none proven needed by any Daher workflow |
| User/role administration | `Add Users`, `Edit Users`, `Manage Roles` | High — self-service privilege escalation surface |
| Provider administration | `Manage Providers`, `Manage Provider Roles`, `Get Providers`, `Get Provider Attribute Types`, `Get Provider Roles`, `Appointments: Invite Providers` | Medium-high — can create/edit other clinicians' Provider records |
| Concept administration (25) | `Manage Concepts`, `Manage Concept Classes/Datatypes/Sources/Map Types/Attribute Types/Name tags/Stop Words/Reference Terms`, `Add/Edit/Delete Concept Proposals` | Medium — can alter the shared clinical vocabulary underlying every form |
| Encounter (broad) | `Delete Encounters`, `Edit Encounters`, `Manage Encounter Roles/Types` | High — write access to every encounter type, not just the 9 approved admission privileges' scope |
| Order (full CRUD) | `Add Orders`, `Edit Orders`, `Delete Orders`, `Manage Order Types/Frequencies/Sets/OrderTemplates` | High — order entry/deletion is not part of the proven admission workflow at all |
| Global properties / system | `Manage Global Properties`, `Get/View Global Properties`, `Manage Modules`, `Assign System Developer Role` | Critical — system-wide configuration and module control |
| Patient (broad) | `Delete Patients`, `Edit Patients`, `Add/Delete/Edit Patient Identifiers/Programs`, plus 11 Patient Dashboard/Overview view sections | High — full patient CRUD, not the read/admit scope the approved 9 privileges grant |

No destructive live test was run against the real facility database or any non-disposable
environment — this table is built from the committed privilege names and OpenMRS's own privilege
table, cross-checked against the approved-9 set by exact string match, not from guessing at what a
name implies.

**§6 — why `Organizational: Nurse` was present, evidenced, not assumed.** Traced across every
committed reference: `grep -rn "Organizational: Nurse"` outside `DECISIONS.md`/`PROJECT_STATE.md`
finds exactly two hits — a CI guard's denylist (`roles-daher.csv` must not reuse this upstream role
*name*, unrelated to granting it) and a UI unit test (`deriveReadiness.test.ts`) asserting the
frontend **never** treats it as sufficient for the admission workflow. `keycloak/reconcile-sso.sh`
has no `reconcile_client_role` call for `Provider` or `Organizational: Nurse` at all — only `Daher
Reception` and `Daher Clinician Admission` are provisioned by committed automation; the code's own
comment says the other two are "created and assigned manually per ADR-017, same as this one." There
is therefore no committed assignment mechanism to remove — the only artifact asserting `clinician =
Provider + Organizational: Nurse` is **ADR-017's own decision table**, with its stated rationale
("Midwives are the primary users… One `clinician` role, mapped to `Organizational: Nurse`"). Answers,
evidenced:

- Required for OIDC authentication? **No** — both halves of the narrow shape have each independently
  authenticated in a real OIDC round trip without it: ADR-017B live-proved a fresh user holding only
  the `Provider` client role authenticates (`roles:[Provider]`, `privileges:[]`); UI-013's own log
  entry live-proved a fresh user holding exactly `Daher Clinician Admission` without `Provider`
  authenticates too. Neither path inspects `Organizational: Nurse`. The `Provider`+`Daher Clinician
  Admission` two-role *union* has not itself been round-tripped this pass (see §7 below).
- Required for OpenMRS user creation? **No** — not referenced anywhere in the creation path.
- Required for Provider creation / `currentProvider`? **No** — ADR-017E2's own live proof: a
  `Organizational: Nurse`-only user (no `Provider`) got `currentProvider: null`, zero Provider rows;
  derivation is exact `"Provider"` client-role membership in the patched Java, nothing else. Adding
  `Organizational: Nurse` alongside a working `Provider`-only login cannot change that outcome — the
  decision method never inspects it.
- Required by UI-013? **No** — `ui/src/features/admission/deriveReadiness.test.ts` explicitly tests
  that it is *not* sufficient for the admission role; `git grep` finds no reference to it in
  `ui/src/api/client.ts` or any admission code path.
- Required by any other committed Daher workflow? **No** committed reference found.

`Organizational: Nurse` is present in the clinician identity for exactly one reason: on 2026-08-14
(ADR-017), it was the OpenMRS role whose *name* matched the workforce ("midwives are the primary
users"), chosen before its `Privilege Level: High` inheritance chain was known — that chain was only
discovered eleven days later, mid-ticket, in ADR-017D.

**§7/§8/§9/§10 — NOT DEMONSTRATED this pass, blocked on a newly found, unrelated bug — disclosed
rather than worked around with a guess.** A disposable `kc-ui009`/cloned-OpenMRS environment
(`openmrs-adr017f`, cloned from `openmrs_ui013_live` — the lineage carrying the full canonical
contract: `Daher Maternity Admission`, `Admission`, `Maternité`, all three roles) was stood up for a
genuine `/oauth2login` round trip on three fresh synthetic identities (`adr017f-narrow` =
`Provider`+`Daher Clinician Admission`; `adr017f-current` = `Provider`+`Organizational: Nurse`+`Daher
Clinician Admission`; `adr017f-reception` = `Daher Reception`). Both `adr017f-narrow` and
`adr017f-current` failed **identically** — same `authenticated:false`, same malformed 6617-byte JSON
exception body, same `OAuth2LoginController.login(118)` WARN — on **first-time login**, in
`Daemon.createUser` → `RequiredDataAdvice`/`ValidateUtil.validate` throwing a `ValidationException`
whose field-level detail Jackson fails to serialize (the response body itself is truncated/malformed
JSON, confirmed reproducible, not a transient glitch). Ruled out by reading the pinned upstream
source (`UserInfo.java`, `OAuth2UserInfoAuthenticationScheme.java`, `Daemon.java`) rather than
guessing: gender (`PersonValidator` only length-checks it, and `"n/a"` is supplied as a default —
confirmed via source, not assumed), given/family name (present and mapped correctly, confirmed by
decoding the real ID token), username pattern (matches prior tickets' own successful synthetic
usernames), and — the one genuine, disclosed side-finding — a **mapper-scoping discrepancy**:
`keycloak/mappers/openmrs-client-roles-mapper.json` carries no `usermodel.clientRoleMapping.clientId`
key, live or committed, contradicting ADR-017B's own text ("scoped to
`usermodel.clientRoleMapping.clientId=openmrs`"); the `roles` claim measurably included Keycloak's
default `account`-client roles (`manage-account`, `manage-account-links`, `view-profile`) until they
were stripped from the two test users by removing `default-roles-daher`. **This is not merely a
documentation mismatch: ADR-017E2 made exact `"Provider"` client-role membership the sole,
fail-closed signal for clinical Provider identity, and an unscoped mapper means that membership is
not actually confined to the `openmrs` client — a client role named `Provider` on *any* client in
the realm would reach `getRoleNames()` and satisfy `isProviderRoleGranted`.** Nothing collides today
(`manage-account` etc. match no OpenMRS role, and no other client currently defines a `Provider`
role) and SSO is not live, so there is no active exposure — but it is a latent gap directly under
ADR-017E2's fail-closed property, not cosmetic. That fix did **not** resolve
the `createUser` failure (re-tested, byte-for-byte identical result after the roles claim was
confirmed clean via direct ID-token decode), so it is reported as a real, separate discrepancy — not
the cause of this failure. The root cause of the `createUser` failure itself was **not isolated**
this pass (a genuinely fresh empty-database boot, which would have given working local Basic Auth to
diagnose it cleanly, stalled for 15+ minutes on liquibase with no progress between two checks and was
abandoned rather than waited on indefinitely). One candidate lead, found in the upstream source and
recorded as a lead only, **not a confirmed cause**: `Daemon.createUser`'s role-resolution
(`roleNames.stream().map(...getRole...).collect(...)`) does not filter `null` results before
`user.addRole(role)`, whereas the existing-user update path (`UpdateUserTask.updated`) explicitly does
(`.filter(r -> r != null)`) — an asymmetry that could matter for *some* malformed-claim shape, though
it did not reproduce this specific failure once the claim was cleaned.

Because this failure is **symmetric across both identity shapes under test**, it is direct evidence
that whatever is broken is orthogonal to `Organizational: Nurse` — but it also means no *new* live
OIDC evidence was produced this pass for either shape. The nearest existing evidence is ADR-017E2's
own already-proven, already-committed result, cited rather than restated as new: a `Provider`-only
user authenticated with `currentProvider` non-null and correct admission-audit attribution; an
`Organizational: Nurse`-only user (no `Provider`) authenticated with `currentProvider: null`; a
`Daher Reception`-only user authenticated with the three registration privileges and `currentProvider:
null`. Those results establish that `Provider` and `Organizational: Nurse` act on independent code
paths for provider derivation — narrowing evidence, not full proof that a
`Provider`+`Daher Clinician Admission`-only identity completes a live admission end-to-end, which
remains undemonstrated.

**§11/§12 — architectural outcome.** All three of §11's outcomes reduce to the same gate at §12:
**No**, removal is not a purely technical cleanup, for two independent reasons that each alone would
be sufficient. First, the evidence itself: `EXTRA_PRIVILEGES` is 293 privileges wide, including
destructive (`Delete Patients`, `Delete Users`), administrative (`Manage Roles`, `Manage Global
Properties`, `Manage Modules`), and clinically consequential (full `Order` CRUD, `Manage Concepts`)
capability — removing it plainly changes what a real clinician account can do, which is exactly the
class of change AGENTS.md requires stopping for. Second, structurally: there is no committed
mechanism to "remove" — the only committed artifact is **ADR-017's own decision table**, a
Liban-decided mapping with a stated clinical-workforce rationale. Changing it supersedes a recorded
human decision about scope of practice, not a technical narrowing, regardless of how the privilege
evidence comes out.

**What the evidence *does* support, offered as the narrowest evidenced floor, not a recommendation to
implement without sign-off:** every one of the 9 privileges ADR-017D approved for admission is
already a strict subset of `Organizational: Nurse`'s closure, and `Provider` contributes zero direct
privileges of its own. If Liban decides clinician identity should be narrowed, **`Provider` + `Daher
Clinician Admission`** is not a guess — it is the measured floor for every Daher workflow this repo
has actually proven (registration is a separate role; nothing else has been characterized). Whether
real nurses/midwives need any of the 293 extra privileges for work outside what this repo has built
(e.g. vitals entry, allergy recording — several of the `Organizational: Nurse` inherited roles' own
names suggest legitimate clinical uses this project has simply never characterized) is **explicitly a
clinical scope-of-practice question this ticket does not answer** — per §12, that is Liban's call, not
inferred from "no code currently uses it."

**Options, costs stated, none selected:**

| Option | What it means | Cost |
|---|---|---|
| **A. Narrow clinician to `Provider` + `Daher Clinician Admission`** | Removes all 293 extra privileges; matches the measured floor for every proven Daher workflow | Real clinicians lose whatever legitimate non-admission capability `Organizational: Nurse`'s inherited roles were meant to provide (vitals entry, allergy recording, ADT events, patient summary) — none of which this repo has built UI for yet, but which may be expected at the birth centre regardless |
| **B. Keep `Organizational: Nurse`, accept the residual breadth** | No change; matches ADR-017's original decision and its stated rationale | Every real clinician account (midwife or doctor) carries `Delete Patients`, `Delete Users`, `Manage Roles`, `Manage Global Properties`, and 289 other privileges never exercised by anything this project has built or tested |
| **C. A new, Daher-owned, narrower "clinical" role** carrying only specific inherited-role privileges (e.g. `Application: Enters Vitals`, `Application: Records Allergies`) without `Privilege Level: High` | Preserves legitimate non-admission clinical capability without the destructive/administrative tail | Requires Liban to specify which non-admission clinical actions real staff actually need — an even more explicit scope-of-practice decision than A, and this ticket's §12/AGENTS.md forbid inventing it |

**Explicitly not done:** no committed role/mapping file changed (`roles-daher.csv`,
`reconcile-sso.sh`, `daher-realm.json` all byte-for-byte unchanged — confirmed by `git status`); no
real staff assigned; no real SSO activation; no production/facility database touched (every read
against `daher-health-openmrs-db-1` was `SELECT`-only); no mutation on anything but the disposable
`kc-ui009`/`mariadb-ui009` environment; no CI guard added (nothing to guard — no config changed); the
mapper-scoping discrepancy and the `createUser` bug are both **reported, not fixed** — fixing either
would be new, separate scope.

**Disposable leftovers, disclosed for a human `docker rm -f` / `DROP DATABASE` pass:** container
`openmrs-adr017f` (SSO-enabled, cloned from `openmrs_ui013_live`, network `daher-ui009_internal`,
network-alias `openmrs-gateway`) and its database `openmrs_adr017f` in `mariadb-ui009`; three
synthetic Keycloak users on `kc-ui009` (`adr017f-narrow`, `adr017f-current`, `adr017f-reception` —
`default-roles-daher` deliberately removed from all three during diagnosis, a disposable-only,
a reversible change); one additive redirect URI added to the `openmrs` client on
`kc-ui009` (`http://openmrs-gateway:8080/*`, alongside the existing four, none removed) so the
disposable container's own `/oauth2login` redirect would validate. `openmrs-adr017f-fresh` and its
databases (`openmrs_adr017f_fresh`, `openmrs_adr017f_probe`) were already torn down this ticket.
`kc-ui009`/`mariadb-ui009` themselves were left running (matching this session's established
hygiene pattern of leaving the shared disposable test harness up between tickets).

**Tests.** No application, config, or CI file changed — the existing baseline (adapter 94,
provisioning 580, seed 16, form-safety 19, ui 148 = application total 857; oauth2login provider
tests 10/10) is unaffected by construction, matching this repo's established reasoning for
evidence-only tickets (ADR-017A/C/E). Not re-run this pass since `git diff` confirms zero source
changes.

**READY TO COMMIT (at the time this gate was first written): No. HUMAN DECISION REQUIRED: Yes.**
Liban's decision (Option A, above) was made outside a live agent session and delivered as this
follow-up ticket's own starting instruction. What follows is that ticket's live closure proof.

---

### ADR-017F — live closure proof, 2026-08-26 (Bug #24/#25 ticket)

**What this section proves.** With Liban's Option A decision given, this pass produced the live
OIDC round trip §7/§8/§9/§10 of the original ticket could not — first-login proof for the exact
narrow identity, the canonical admission contract, and a dangerous-privilege negative control — on
a fresh synthetic clinician that had never before authenticated. Full method/tooling detail (the
disposable `openmrs-adr017f`/`kc-ui009` environment, the `/oauth2login` curl round-trip script) is
unchanged from the prior evidence-gathering pass; only the results are new. **What blocked this the
first time and how it was actually root-caused is Bug #24, documented separately in
`PROJECT_STATE.md`'s Known Bugs table — it was this ticket's own prior-session synthetic test data
(an email using the `.invalid` TLD), not a platform defect.** Once corrected, first-login
provisioning worked cleanly.

**Scope of what this proves, stated precisely.** This entire proof ran against `openmrs-adr017f`, a
disposable container built from `daher-health/openmrs-backend:3.7-off` (the image DH-028 built and
verified carries both patches, tag `f1376269c286`) on a *clone* of the `openmrs_ui013_live` database
lineage — never the main stack's own running container or database. **DH-028's own redeploy has
since been completed and independently re-verified in this pass, correcting an earlier stale
statement**: the real main stack's running OpenMRS container (`daher-health-openmrs-backend-1`) is
confirmed on image `sha256:f1376269c286fd94c5559a18d429acaf70cf9b7f0e801b89d38d2e23a048e0ed`, its
`oauth2login-1.5.0.omod` confirmed by direct `sha256sum` at
`ae148634ec06817efcc99fdf9c0a1a4e05565ef76292c8eb7899b3b4aec8966b`, and `OAuth2LoginBridgeFilter`
confirmed present in that exact artifact (`unzip -l`/Python `zipfile` listing, not assumed from the
hash alone). `SSO_ENABLED=false` (unchanged, deliberate), REST `/session` and FHIR `/metadata` both
`200`. Data preservation reconfirmed by direct count: patient `103`, users `6`, encounter `725`,
visit `143` — unchanged, matching DH-028's own pre-redeploy sentinels. **The main stack now runs the
same patched image this proof used on the disposable clone** — DH-028 is done, not a remaining
prerequisite. Real SSO activation still needs BLOCKER-5's roster names and SSO's public domain
(#15); it does not additionally need a main-stack image change.

**Fresh clinician OIDC, never-before-authenticated (`adr017f-narrow`, Keycloak user
`ad1d5b37-c694-455c-a3eb-aa5eba28a76c`), genuine `/oauth2login` round trip:**

- `authenticated: true`.
- Exactly **one** Person (`1bb04f84-8833-46ac-9d14-544b093b7107`), **one** User
  (`8ce2a54e-5d48-4cf7-ad1e-f87d87421424`), **one** Provider (`34145f4c-c761-4938-bea2-d57684968cf8`)
  — measured by direct `SELECT COUNT(*)` against `users`/`person`/`provider`, joined on this
  identity, not inferred from the session response alone.
- `roles: [Daher Clinician Admission, Provider]` — **exactly** the intended set. **No
  `Organizational: Nurse`.**
- `privileges`: exactly the 9 approved (`Get Encounters`, `Get Visit Types`, `Add Visits`, `Get
  Visit Attribute Types`, `Get Visits`, `Get Patients`, `Get Admission Locations`, `Get Beds`, `Add
  Encounters`) — count and names both confirmed, not just counted. **No `Privilege Level: High`
  inheritance reached this session** — the same DB closure computed in this ADR's original pass
  (§3/§4 above) has no path from either `Provider` or `Daher Clinician Admission` to `Privilege
  Level: High`, and the live session's 9-privilege count confirms it empirically as well as
  structurally.
- `currentProvider`: non-null (`34145f4c-c761-4938-bea2-d57684968cf8`), role-derived per ADR-017E2,
  reconfirmed working with the narrow identity specifically (ADR-017E2's own live proof used the
  broader three-role shape; this is the first live confirmation with `Organizational: Nurse` absent).
- **Repeat-login idempotence, three total logins:** two additional logins after the first, `users`/
  `person`/`provider` counts identical (`1/1/1`) after each, same three UUIDs throughout, no
  duplicate rows at any point.

**Canonical admission contract, disposable synthetic patient (`9b1ae281-48b4-4222-987d-fa04a836648c`,
identifier `10000LM`, registered via a genuine `Daher Reception` OIDC session, not fabricated by
SQL):**

- `POST /visit` with `{patient, visitType: "Daher Maternity Admission" (194bf546-…), location:
  "Maternité" (6538f097-…)}` → **201**, `Visit` UUID `5f385a1f-6cae-40a5-8445-81f4fbd53d8d`.
- `POST /encounter` with `{patient, encounterType: "Admission" (e22e39fd-…), visit: <above>,
  location: Maternité, encounterProviders: []}` → **201**, `Encounter` UUID
  `72446fb7-b6d9-44ee-bf95-684e447ba428`, linked (`encounter.visit.uuid` = the Visit UUID above).
- **Linkage confirmed via read-back**, not assumed from the write payload alone.
- **Audit creator**: `v=full` read-back on both — `Visit.auditInfo.creator.uuid` and
  `Encounter.auditInfo.creator.uuid` both equal `8ce2a54e-…`, the authenticated clinician's own
  OpenMRS user UUID exactly. Not admin, not daemon, not the registering reception account.
- `encounterProviders: []` on read-back, unmodified — exactly the ADR-017E2/UI-013 contract; no
  provider field was added to compensate for the narrower identity.

**Dangerous-privilege negative control.** Two candidates were probed, one discarded honestly rather
than silently swapped for a cleaner result: `GET /ws/rest/v1/systemsetting` returned **200** for the
narrow clinician — this is **not** evidence of excess privilege, it reconfirms ADR-017A's own
already-recorded finding that global-property reads are open to any authenticated session
regardless of privilege level, unrelated to `Organizational: Nurse`. `GET /ws/rest/v1/role`
(requires `Manage Roles`, part of the 293 extra privileges the Nurse closure carried and the narrow
identity does not) returned **403**, `"Privileges required: Manage Roles"` — the intended,
successful negative control: an operation the *previous* three-role shape would have passed is
cleanly denied to the narrow one.

**Reception, fresh synthetic (`adr017f-reception`), genuine first `/oauth2login`:**

- `authenticated: true`, `currentProvider: null`, roles `[Daher Reception]`, privileges exactly the
  3 approved (`Add Patients`, `Edit Patient Identifiers`, `Get Patients`).
- **Registration intact**: this exact session registered the synthetic patient used for the
  admission contract above (`201`, real write, not simulated).
- **Admission denied**: `POST /visit` → `400` ("visitType on class org.openmrs.Visit" — the same
  `ConversionException`-during-deserialization shape ADR-017C/D already characterized); `POST
  /encounter` → `403`, `"Privileges required: Edit Encounters,Add Encounters"`. Both writes refused.
- This also demonstrates first-login auto-provisioning is not clinician-specific — Reception's own
  first login provisioned cleanly once its test email carried a valid TLD, the identical fix as the
  clinician identity.

**Partial-write safety, explicitly tested, not assumed.** A deliberately bad-email synthetic user
(`adr017f-pwtest`, same `.invalid`-TLD trigger later diagnosed as Bug #24's cause) was logged in
once: `authenticated: false`, and **zero** rows in `users`/`person` — the failed `createUser` call's
transaction rolled back completely, confirmed by direct count, not inferred from the error response.
The identical user, corrected to a valid-TLD email, then authenticated cleanly on a subsequent
attempt with no orphaned state from the failed one. **Separately, a genuinely new finding surfaced
during this test — recorded as Bug #25, not folded into this ADR's own decision**: a deterministic
(reproduced on the very first attempt of a dedicated, single-shot repro user, `adr017f-race1`)
same-request read-after-write gap — most probably (mechanism narrowed, not independently confirmed
at the transaction/isolation level) `Daemon.createUser`'s cross-thread commit not yet visible to
the calling thread's own re-fetch — reporting `authenticated: false` to the caller while User+Person
were in fact persisted (Provider not yet created, no error logged anywhere — a silent soft-fail, not
an exception). The **very next** login (now hitting the existing-user path) completed normally with
no duplicate Person/User/Provider. This is a real, disclosed, open bug — full detail in
`PROJECT_STATE.md`'s Known Bugs table (#25) — but it is **not** a §13 STOP: no duplicate or
ambiguous identity occurred in any trial; the system converges to exactly one of each row every
time, just not always on the very first attempt.

**Bug #23 boundary, observed not fixed, exactly per this ticket's own instruction.** The unscoped
`openmrs-client-roles-mapper` (Known Bug #23) was reconfirmed reproducing on a token from a user
still carrying `default-roles-daher` (`adr017f-pwtest`/`adr017f-race1`): `roles` claim included
`manage-account`, `manage-account-links`, `view-profile` alongside the two real `openmrs`-client
roles. Confirmed still inert: OpenMRS's own session for that same login shows exactly `[Daher
Clinician Admission, Provider]` server-side and exactly 9 privileges — the leaked names don't
name-match any OpenMRS role, so they were silently dropped, not escalated. Left open, not fixed,
per this ticket's own §14 boundary (fixing it was not required to reproduce or resolve Bug #24/#25).

**Regressions, checked not assumed.** `git diff` against the session start shows zero changes to
any application, config, or CI file — `DECISIONS.md`/`PROJECT_STATE.md` only. The 857-test
application baseline and the 10/10 `oauth2login` provider-patch tests were not re-run this pass
(no source changed to re-run them against); `git diff` is the basis for that claim, not a copied
figure. No new automated coverage was added: no source or config was patched (Bug #24 was test
data, not code; Bug #25 is reported, not fixed), so there is no CI job to extend per this ticket's
own conditional instruction, and the role-derivation properties this ticket's §15 asked to cover
(`Provider` role creates a Provider only when exact `Provider` membership exists; Reception never
becomes Provider) are already covered by ADR-017E2's existing 10-case `ProviderRoleDerivationTest`
suite — this pass's live proof exercises those same properties end-to-end rather than duplicating
them as new unit tests.

**Disposable leftovers, this pass, disclosed alongside the prior pass's own list (still pending):**
containers `openmrs-adr017f` (unchanged from before), `mariadb-ui009`/`kc-ui009` (still running);
database `openmrs_adr017f` now additionally carries the `adr017f-narrow`/`-current`/`-reception`
Persons/Users/Providers created this pass, one synthetic patient (`9b1ae281-…`, identifier
`10000LM`), one Visit (`5f385a1f-…`), one Encounter (`72446fb7-…`), and two throwaway diagnostic
identities (`adr017f-pwtest`, `adr017f-race1`, both partially or fully provisioned, harmless
synthetic data). On Keycloak (`kc-ui009`): the three original test users' `email` attributes were
changed from the `.invalid`-TLD originals to `@example.com` (the fix); `default-roles-daher` was
**removed** (not additive — a reversible change) from `adr017f-narrow`/`-current`/`-reception`;
two new synthetic users (`adr017f-pwtest`, `adr017f-race1`) were created and left in place. No
production/facility state touched — every write above was against the disposable
`openmrs_adr017f` schema on `mariadb-ui009`.

**READY TO COMMIT: Yes** — this section is evidence/decision-recording only, no source or config
touched, matching this repo's own precedent for evidence-only tickets (ADR-017A/B/C/E). **Per this
ticket's own §20, no commit was made regardless** — stopped for Liban's authorization as instructed.

---

## Bug #25 — use OpenMRS's authoritative daemon-created User on the first OIDC callback

**Date:** 2026-08-26
**Status:** Implemented, automated-tested, and live-demonstrated on the disposable SSO stack; not
committed; real facility not redeployed and SSO remains off.
**Human Decision Required:** No. This is a narrow correction to third-party integration lifecycle
usage. It does not change clinical content, role policy, patient-data routing, or global OpenMRS
transaction behavior.

**Measured reproduction.** Ten completely fresh synthetic clinicians with valid `@example.com`
emails and exactly `Provider` + `Daher Clinician Admission` were each sent through one genuine OIDC
callback against the pre-fix disposable artifact. **10 failures / 10 attempts** returned
`authenticated:false`. Direct database checks after every attempt found exactly one User, one
Person, the exact two role assignments, and zero Providers. Separate exploratory smoke/probe users
were not counted in that formal rate. The series is 100% for the measured conditions; this record
does not generalize it to every possible database/isolation schedule by calling it deterministic.

**Pinned execution path and proven root cause.** oauth2login tag `1.5.0`, commit
`2770233627343a53719ec981e997d94b1d90b7ad`, ran with OpenMRS API 2.8.7. The callback enters
`OAuth2LoginController.login`, which calls `Context.authenticate(new OAuth2TokenCredentials(...))`.
`OAuth2UserInfoAuthenticationScheme` is class-level `@Transactional`; `authenticate` first calls
`ContextDAO.getUserByUsername(preferred_username)`. For a new identity it constructs the OpenMRS
User and calls `ContextDAO.createUser`, which delegates to OpenMRS 2.8.7's `Daemon.createUser`.
That pinned implementation performs duplicate checks (id, UUID, username, email), resolves roles,
starts a separate daemon thread/session, calls `UserService.createUser`, waits synchronously on
`Future.get()`, and returns the newly created `User` to the caller.

Disposable instrumentation measured the outer authentication transaction active at MariaDB
isolation `REPEATABLE-READ`, with auto-commit off. Its initial username lookup returned null and
established the snapshot. The awaited daemon call returned a fully populated User (User id, Person
id, username, Keycloak-subject systemId, and both roles), proving creation and daemon completion.
The immediate same-outer-transaction `getUserByUsername` still returned null. At that exact point a
separate SQL connection already saw the committed User/Person/roles, and a subsequent HTTP request
with a fresh transaction saw the User. Thus persistence flush/commit was complete; this was not a
delayed daemon write. The outer session had made no write to flush, and neither flushing nor global
cache invalidation could make an older `REPEATABLE-READ` snapshot see the newer commit.

oauth2login 1.5.0's private `createUser` wrapper discarded `ContextDAO.createUser`'s return value,
then performed that invalid reread. It consequently returned `new BasicAuthenticated(null, ...)`.
`Context.isAuthenticated()` was false, so the controller did not execute its property or Provider
reconciliation. The second HTTP request diverged only because its fresh transaction's *initial*
username lookup saw the committed User; authentication succeeded and controller-level Provider
reconciliation created exactly one Provider for that Person. The lookup key remained the same
`preferred_username` both times; the created `systemId` remained the Keycloak subject. Direct
instrumentation and source tracing ruled out username/systemId mismatch, a stale pre-provision
UserInfo object, OpenMRS authentication/user cache, daemon completion timing, and Provider timing as
the cause. A separately instrumented new Hibernate session inside the original HTTP request was not
introduced; the daemon's own fresh session returned the committed object, the independent SQL
connection saw it, and the following request's fresh Hibernate transaction saw it.

**Implementation.** `OAuth2UserInfoAuthenticationScheme.java.patch` changes the private wrapper
from `void createUser(...)` to `User createUser(...)`, returns
`getContextDAO().createUser(...)`, and assigns that authoritative result in `authenticate`. The
same-transaction post-create username reread is removed. No sleep, retry, browser redirect, manual
pre-provisioning, cache flush, transaction annotation, isolation setting, or global OpenMRS
semantic was added or changed. `openmrs-config/Dockerfile` applies the patch after the existing
bridge and ADR-017E2 patches. CI applies the same production patch to the exact pinned upstream
clone and runs both identity suites.

**Automated proof and negative controls.** New `FirstLoginAuthenticationTest` executes the actual
authentication method for three properties: use the authoritative DAO-created User when the
calling snapshot remains blind; perform exactly one pre-create username lookup and no post-create
reread; propagate creation failure. It passes **3/3** with the patch. On pristine upstream, the
same suite ran all three and failed exactly the first two (null authenticated User; two lookups),
while failure propagation remained green. ADR-017E2's unchanged exact-role suite passes **10/10**,
including missing/malformed/non-exact role fail-closed cases. The companion static guard checks the
production patch and Dockerfile wiring and rejects retry/sleep/cache workarounds; a throwaway
mutation removing `user =` made it fail specifically because authentication no longer used the
created User. The real Dockerfile `off` target built successfully. Application tests rerun:
adapter 94, provisioning 580, seed 16, form safety 19 (one expected skip), UI 148; **857 total**.
Compose interpolation, affected static guards, Ruff/format/type/build checks, and diff whitespace
validation passed.

**Identity safety and live proof.** Creation before Provider reconciliation remains OpenMRS's
existing daemon transaction: validation failure rolls back Person/User/roles together. A fresh
invalid-email identity was demonstrated returning unauthenticated with **0 Person, 0 User,
0 Provider, and 0 role rows**. Duplicate checks remain in `Daemon.createUser`. After successful
authentication, the unmodified controller looks Providers up by the same Person: zero creates one;
an existing one is reused/unretired. If Provider persistence itself throws, upstream already logs
and swallows that reconciliation error, leaving the unique User authenticatable so a later login
can reconcile the Provider; this pre-existing behavior was not broadened or hidden by this patch.
No ambiguous identity state or duplicate path was found.

Two fresh disposable clinicians then authenticated on their genuine first callbacks. Each had
exactly one Person/User/Provider, a non-null currentProvider, exactly `Provider` +
`Daher Clinician Admission`, exactly nine privileges, and neither `Organizational: Nurse` nor
`Privilege Level: High`. Each logged in twice more: all six callbacks returned authenticated and
kept identical User/Person/Provider UUIDs and counts. Clinician 1 created a canonical `Daher
Maternity Admission` Visit and linked `Admission` Encounter at `Maternité`; both audit creators were
that clinician and encounterProviders remained `[]`. A fresh Reception identity authenticated on
its first callback with `currentProvider:null`, registered one synthetic patient, and was refused
admission. These were synthetic writes confined to `openmrs_adr017f`/`kc-ui009`.

**Boundaries.** Bug #23 remains open and unchanged; this fix neither edits the Keycloak mapper nor
weakens exact Provider role matching. The main facility stack was read-only: no restart, image or
OMOD replacement, staff/patient write, OpenELIS change, SSO activation, or configuration change.
The tested disposable OMOD SHA-256 was
`5f2a7e064e490668f8311097e3e9cc94e9ff6a179621a31e14c091cb140b12f7`.

**Decision/result:** Bug #25 is closed at source and disposable proof level. The smallest correct
fix is to honor the existing OpenMRS return-value contract; a fresh query or broader transaction
change is unnecessary. **READY TO COMMIT: Yes**, subject to Liban's review. Per ticket instruction,
no commit or push was made.

---

## Bug #23 — scope the OpenMRS client-role mapper to the openmrs client only

**Date:** 2026-08-27
**Status:** Fixed, automated-tested, and live-demonstrated on disposable Keycloak/OpenMRS; not
committed; real facility not redeployed and SSO remains off.
**Human Decision Required:** No. Keycloak-native mapper configuration scoping; no clinical content,
role policy, or patient-data routing change; ADR-017E2's exact-match Provider derivation untouched.

**Pre-fix mapper, traced exactly.** `keycloak/mappers/openmrs-client-roles-mapper.json` (applied to
both the fresh-realm import at `keycloak/realm-config/daher-realm.json`, inline and byte-identical,
and the existing-realm path via `reconcile-sso.sh`'s `reconcile_mapper`) configures Keycloak's
built-in `oidc-usermodel-client-role-mapper` on the `openmrs` client, emitting claim `roles`
(`multivalued`, present on id/access/userinfo tokens). This mapper type has an optional
`usermodel.clientRoleMapping.clientId` config key that scopes it to one client's roles; **the
committed config never set it**, on either path. Confirmed on a disposable Keycloak 26.7.1 realm
imported from the committed JSON: the running mapper's config had no such key. Separately confirmed
`fullScopeAllowed: true` on the `openmrs` client (also absent from committed config — Keycloak's
import default), which is what lets a foreign client's roles reach this client's token at all before
the mapper even runs.

**Pre-fix claim contents, measured, not assumed.** A genuine first-callback OIDC login for a fresh
`openmrs` → `Provider` + `Daher Clinician Admission` clinician produced `roles: ["Daher Clinician
Admission", "Provider", "manage-account", "manage-account-links", "view-profile"]` — the last three
are the built-in `account` client's default roles, reaching the token via the realm's own
`default-roles-daher` composite role (§11/§12: this is real composite-role expansion in this realm,
not a hypothetical one, and it is realm-composite-driven, not realm-role-driven — a
realm-role-only identity, tested below, got no such leak because it carried no client-role grant to
relay). Confirmed inert for that specific case: no OpenMRS role is named `manage-account` etc.

**Foreign-Provider collision — the dangerous negative control.** A disposable `foreign-test-client`
was created on the same realm with client roles named exactly `Provider`, `Daher Clinician
Admission`, and `Daher Reception` — none assigned as `openmrs` roles. A synthetic user given
*only* `foreign-test-client` → `Provider` (plus, for the OpenMRS-side proof, the minimum legitimate
`openmrs` → `Daher Reception` grant needed for OpenMRS itself to authenticate the user, per this
ticket's own §9 instruction) produced a pre-fix `roles` claim of `["Daher Reception",
"manage-account", "manage-account-links", "view-profile", "Provider"]` — **`Provider` present**,
despite zero `openmrs`-client `Provider` grant. ADR-017E2's derivation is `roleNames.contains("Provider")`,
an exact string match against the `roles` claim's contents with no client-origin check — this
positively would have granted Provider identity to that user. **Privilege escalation was possible,
not merely a token-shape defect.** The two other foreign-role collisions (`Daher Clinician
Admission`, `Daher Reception`, each foreign-only) also leaked into `roles` pre-fix, confirming the
vector is general, not `Provider`-specific.

**Root cause.** Keycloak's `oidc-usermodel-client-role-mapper` relays *all* client roles a user holds,
across every client in the realm, unless `usermodel.clientRoleMapping.clientId` names one client.
The committed mapper omitted that key on both provisioning paths — an under-scoped mapper
configuration, not an oauth2login or ADR-017E2 defect.

**Fix.** Added `"usermodel.clientRoleMapping.clientId": "openmrs"` to the mapper `config`, in both
`keycloak/mappers/openmrs-client-roles-mapper.json` and the inline copy in
`keycloak/realm-config/daher-realm.json` — the two files were already byte-identical duplicates
before this change and remain so after. No Java/oauth2login change; ADR-017E2's exact-`Provider`
membership check, `Daher Reception`/`Daher Clinician Admission` reconciliation, `fullScopeAllowed`,
OpenELIS's SAML client/mapper, and every other Keycloak/OpenMRS config are untouched — `git diff
--stat` shows exactly 3 files: the two mapper configs and a new CI guard step.

**Files changed:** `keycloak/mappers/openmrs-client-roles-mapper.json`,
`keycloak/realm-config/daher-realm.json`, `.github/workflows/ci.yml` (new static guard).

**Fresh-realm mapper, live.** A second disposable Keycloak 26.7.1 realm was imported from the
*fixed* `daher-realm.json` from scratch. Its `openmrs` client's `client roles` mapper carried
`usermodel.clientRoleMapping.clientId: "openmrs"` natively, with no reconciliation step. A synthetic
user with only a foreign client's `Provider` role authenticated against this fresh realm and
produced `roles: null` (absent claim — zero `openmrs`-client roles held) and `resource_access`
still showed the foreign client, proving the token carried the grant elsewhere but the OpenMRS
claim excluded it.

**Existing-realm reconciliation, live.** `reconcile-sso.sh` was re-run against the *same running*
pre-fix disposable realm (`kc-ui009`, unchanged admin credentials, unchanged `openmrs` client secret)
with only the mapper file's content changed. It updated the existing mapper (not create — reused the
existing mapper id, confirmed by `reconcile_mapper`'s own update-not-create branch) and reported
success. Every subsequent test below authenticates through this exact reconciled realm.

**Post-fix token proof, both paths, all six identities (pure-Keycloak `kc23-*` test identities on
the disposable `keycloak-bug23` realm — distinct from the `bug23-*` identities in the OpenMRS-side
proof below, which used a smaller role set per identity):**

| Identity | openmrs-client roles held | foreign-client roles held | `roles` claim (post-fix) |
|---|---|---|---|
| genuine clinician | `Provider`, `Daher Clinician Admission` | — | `[Daher Clinician Admission, Provider]` |
| genuine Reception | `Daher Reception` | — | `[Daher Reception]` |
| **foreign-Provider negative control** | `Daher Reception` | `Provider` | `[Daher Reception]` — **no `Provider`** |
| foreign-Admission-only | — | `Daher Clinician Admission` | absent (no `roles` claim at all) |
| foreign-Reception-only | — | `Daher Reception` | absent |
| mixed (genuine Provider+Admission, foreign Provider+arbitrary-role) | `Provider`, `Daher Clinician Admission` | `Provider`, `arbitrary-role` | `[Daher Clinician Admission, Provider]` — foreign roles make no difference |
| realm-role-only (`clinician` realm role, no client roles) | — | — | absent |

No `manage-account`/`manage-account-links`/`view-profile` leakage in any row — the `account`-client
composite contribution is now excluded exactly like any other foreign client's roles.

**OpenMRS-side end-to-end proof, disposable `openmrs-adr017f` (unchanged image/env from the Bug
#25 proof) against the fixed, reconciled `kc-ui009` realm, database `openmrs_adr017f`:**

- **Two fresh clinicians** (`bug23-clinician1`, `bug23-clinician2`), each a genuine first `/oauth2login`
  callback: `authenticated: true`, `currentProvider` non-null, exactly 9 privileges, roles exactly
  `[Daher Clinician Admission, Provider]` — exactly two entries, so neither `Organizational: Nurse`
  nor `Privilege Level: High` is present. The actual ID token OpenMRS
  itself consumed was decoded server-side (from `user.userProperties.oauth2IdToken`) and its `roles`
  claim was exactly `["Daher Clinician Admission", "Provider"]` — the fix reached the real
  authentication path, not just a standalone token probe.
- **Fresh Reception** (`bug23-reception`): `currentProvider: null`, roles `[Daher Reception]`,
  3 privileges.
- **Foreign-Provider negative control** (`bug23-foreign-provider`, `foreign-test-client → Provider`
  plus the minimum legitimate `openmrs → Daher Reception`): `authenticated: true`, roles
  `[Daher Reception]`, `currentProvider: null`. **Direct database query** (`users` joined to
  `provider` on `person_id`, `retired = 0`) found **zero** Provider rows for this identity — not
  inferred from the session response, counted directly. This is the load-bearing proof: the foreign
  `Provider` grant did not create a Provider.
- **Mixed identity** (`bug23-mixed` — genuine `Provider`+`Daher Clinician Admission` plus *only*
  foreign `Provider`; the pure-Keycloak `kc23-mixed` above additionally carried a foreign
  `arbitrary-role`, not repeated here since it added nothing OpenMRS-relevant to check):
  `currentProvider` resolved to exactly one Provider row (via the genuine grant), roles exactly the
  two genuine ones — the foreign `Provider` role was present in the user's overall Keycloak grants
  but absent from the `openmrs` claim and made no difference to the outcome.
- **Repeat logins:** `bug23-clinician1` logged in twice more after its first callback; all three
  logins kept the identical User UUID (`2c67d291-…`) and Provider UUID (`f959edaf-…`) — Bug #25's
  first-login fix is not regressed by this change.
- **Canonical admission contract**, disposable synthetic patient (`1bdc7ccb-…`, identifier
  `20000A8`, registered via a genuine `Daher Reception` session): Reception's own attempt to create
  a `Daher Maternity Admission` visit was refused (`400`, the same `ConversionException`-during-
  deserialization shape ADR-017C/D already characterized — unchanged by this fix); `bug23-clinician1`
  then created the Visit (`201`) and a linked `Admission` Encounter (`201`) — read-back confirmed
  `encounter.visit.uuid` matched, `encounterProviders: []` unmodified, and both `auditInfo.creator.uuid`
  equalled the clinician's own OpenMRS user UUID, not admin or daemon.

**Composite- and realm-role behavior (§11/§12), settled by direct observation, not assumption.** No
`openmrs`-client role in this realm is itself composite; the only composite in play is the realm's
built-in `default-roles-daher`, which composes in the `account` client's own default roles — that is
exactly the pre-fix leak, and the fix's client-scoping correctly suppresses it (the mapper still only
looks at direct `openmrs`-client role grants, and Keycloak's realm-composite-to-client-role expansion
resolves those `account` roles as belonging to `account`, not `openmrs`). Realm-level roles
(`clinician`, `lab-tech`, `sys-admin`) were never the leak vector and remain irrelevant to this
claim — a realm-role-only identity produced no `roles` claim at all, both before and after the fix,
because the mapper has only ever read client-role grants.

**OpenELIS boundary.** `git diff` against session start shows exactly 3 changed files, none touching
`keycloak/reconcile-sso.sh`'s `openelis`-client block or `keycloak/mappers/openelis-role-list-mapper.json`.
No live OpenELIS SAML flow was exercised — none was needed: the change is scoped to one config key on
one OIDC client's protocol mapper, with no code path by which it could reach a separate SAML client's
configuration.

**New automated guard.** `.github/workflows/ci.yml` gained a static-guard step ("Bug #23") asserting,
on committed config only (this job cannot boot the real stack, matching every other guard in this
file): the inline `daher-realm.json` mapper and the external `openmrs-client-roles-mapper.json` stay
byte-identical; both carry `usermodel.clientRoleMapping.clientId == "openmrs"`, `claim.name ==
"roles"`, `multivalued == "true"`, and `protocolMapper == "oidc-usermodel-client-role-mapper"`;
exactly one mapper on the `openmrs` client emits the `"roles"` claim (a second, competing mapper —
ADR-017E's own previously-recorded "undefined precedence" failure mode — would otherwise pass
silently); and `reconcile-sso.sh` still calls `reconcile_mapper` for the `"client roles"` mapper (the
external file being correct proves nothing for an already-existing realm if reconciliation stopped
applying it). **Control-tested, not merely asserted to work:** seven deliberate regressions were
applied to in-memory copies of the two files and `reconcile-sso.sh`'s text (never the committed
files) and the guard logic re-run against each — (1) the pristine pre-fix mapper (key removed from
both) failed on both files' `clientId` check; (2) the key pointed at `openelis` instead of `openmrs`,
failed the same check; (3) only the external file fixed, the inline copy left pre-fix (simulating a
partial fix), failed both the byte-identical-drift check and the inline file's `clientId` check;
(4) `claim.name` renamed, failed the drift check and the `claim.name` check; (5) `protocolMapper`
retyped to a realm-role mapper, failed the type check; (6) a second mapper added to the `openmrs`
client with `claim.name: "roles"`, failed the exactly-one-`roles`-mapper check (found 2); (7) the
`reconcile_mapper "$openmrs_id" "client roles"` line stripped from `reconcile-sso.sh`, failed the
reconciliation-coverage check. Each failed for its specific asserted reason, none for an unrelated
one.

**Application test baseline.** No adapter/provisioning/seed/form-safety/UI source is in this pass's
diff (`git diff --stat`: two Keycloak JSON configs and one new CI step only), and a grep for any
Python/JS/Java source referencing `daher-realm.json`, the mapper files, or `reconcile-sso.sh` found
none — so the 857-test application baseline, the 10/10 oauth2login `ProviderRoleDerivationTest`
suite, and the 3/3 `FirstLoginAuthenticationTest` suite were not re-run: there is no source in the
diff those suites read, verified by grep rather than assumed, matching this repo's own established
precedent for a config-only pass (e.g. this file's own ADR-017F boundary-observation section).
`docker compose config` validated cleanly; both JSON files parsed; the new CI step's YAML parsed;
`git diff --check` reported no whitespace issues.

**Remaining risk, disclosed rather than silently accepted.** `fullScopeAllowed: true` remains set
(Keycloak's import default; absent from committed config, not something this ticket changed) on the
`openmrs` client. Foreign roles and foreign clients therefore still reach `resource_access` and
`aud` in the token — harmless here only because `openmrs.mapping.user.roles=roles`
(`config/oauth2.properties.template`) reads the one claim this fix scoped, and oauth2login was
observed reading no other claim for role/Provider derivation. Setting `fullScopeAllowed: false` would
change that client's whole token surface and is out of this ticket's scope — not done, flagged here
for a future ticket if a narrower token is ever wanted.

**Disposable leftovers, this pass, disclosed.** New containers: `mariadb-bug23`, `keycloak-bug23`,
`keycloak-bug23-fresh` (left running), and `openmrs-bug23` (stopped, not removed, after this pass —
it never reached a usable state: a fresh `/openmrs/data` volume attached to the already-migrated
`openmrs_adr017f` schema left it mid-first-boot, so it never authenticated anyone or wrote anything;
it shared that database with `openmrs-adr017f`, but because it could only have added rows had it
worked, and every Provider UUID observed in this pass's evidence matches the UUID each login response
itself returned, its presence does not affect the "zero Provider rows" negative-control claim).
Restarted from prior sessions and left running: `mariadb-ui009`, `kc-ui009`, `openmrs-adr017f`. New
Keycloak realm content on `kc-ui009`: a `foreign-test-client` (disposable, roles
`Provider`/`arbitrary-role`), five new synthetic users
(`bug23-clinician1`, `bug23-clinician2`, `bug23-reception`, `bug23-foreign-provider`, `bug23-mixed`),
and the `openmrs` client's `redirectUris` gained `http://openmrs-gateway:8080/*` (disposable-test-only,
matching the same addition a prior session made and this session's reconciliation run reset, then
re-added). Database `openmrs_adr017f` gained five new Person/User rows, three new Provider rows, one
synthetic patient (`1bdc7ccb-…`, identifier `20000A8`), one Visit, one Encounter. No production/
facility state touched; every write above was against disposable containers/realms.

**Decision/result:** Bug #23 is closed at source and disposable proof level. The smallest correct fix
is the missing Keycloak-native scoping key, applied identically on both provisioning paths; no
oauth2login change, no weakening of ADR-017E2's exact-match logic, no broader mapper redesign was
needed or made. **READY TO COMMIT: Yes**, subject to Liban's review. Per ticket instruction, no
commit or push was made.

---

## ADR-030 — ACCEPTED / IMPLEMENTED (2026-08-28, UI-015F closure pass): a narrow Daher-owned OpenMRS module endpoint for ending a maternity admission without broadening clinician privileges

**Date raised:** 2026-08-27 · **Status:** SELECTED (Option D, by Liban, 2026-08-27). The original
proposed mechanism (Daemon-context `saveVisit()`, no outer transaction of its own) was live-disproven
on two of its three required properties — see "Selection and falsification (2026-08-27)" below. A
**revised mechanism** (a module-owned outer transaction, still calling the existing `saveVisit()`
rather than bypassing it) was then built as a second disposable spike and **live re-validated on both
previously-failed properties** — see "Mechanism revised and re-validated (UI-015D, 2026-08-27/28)"
further below. **Still NOT IMPLEMENTED** — no production module exists. The original entry (unedited
below) remains the accurate record of what was first proposed and why; it is superseded, not deleted,
by the two sections that follow it.
**Human Decision Required:** Yes — not whether the mechanism works (it now does, with live evidence),
but whether to proceed to the full UI-015 production implementation on this revised mechanism
**Supersedes nothing. Does not reopen ADR-027.** Recorded because the candidate sits close enough to
ADR-027's own subject (the Daher UI's identity/trust boundary) that leaving it undocumented risked a
future reader mistaking it for something ADR-027 already ruled on either way. It does not.

**Context.** BLOCKER-10 (UI-015/UI-015A/UI-015B/UI-015C, `PROJECT_STATE.md`) found that ending an
OpenMRS Visit through the only real mechanism (`POST /ws/rest/v1/visit/{uuid}
{"stopDatetime":…}`) requires, on the pinned stack, at minimum four privileges beyond the nine
`Daher Clinician Admission` already holds — `Assign Beds`, `Edit Admission Locations`, `Get Queue
Entries`, and `Edit Visits` — two of which (`Assign Beds`, `Edit Admission Locations`) are
bytecode-confirmed globally unscoped (`assignPatientToBed(Patient, Encounter, String)` takes no
location/type parameter at all) and grant facility-wide bed-assignment/ward-configuration authority
with no relationship to closing one maternity admission. `Edit Visits` is separately
bytecode-confirmed (`VisitServiceImpl.saveVisit():176`) to be required, unconditionally, for editing
**any** already-persisted Visit of **any** type at **any** location — OpenMRS has no per-visit-type
or per-location edit privilege anywhere in the system (exhaustively enumerated, `PROJECT_STATE.md`).
Patching only the two module hooks (bedmanagement, queue) — an earlier pass's recommendation,
corrected in the same `PROJECT_STATE.md` entry — would still leave the canonical clinician unable
to end the Visit, because the third gate is unpatched OpenMRS core, not a module.

**The candidate.** A small, Daher-owned OpenMRS module exposing exactly one server-side operation —
"end the canonical Daher Maternity Admission Visit" — reachable only by the already-authenticated
real clinician's own existing browser session (no new login, no new client, no service-account
credential introduced anywhere). Internally, after authenticating the caller and independently
re-verifying (server-side, never trusting the client) that the target Visit's `visitType.uuid`,
`location.uuid`, and `stopDatetime == null` all match the canonical contract, the operation elevates
to OpenMRS's own `Daemon.runInDaemonThread()` execution context — the same system-context mechanism
this codebase already relies on elsewhere (`Daemon.createUser`, first-login auto-provisioning,
characterized in the Bug #24/#25 record) — to perform the actual `stopDatetime` write plus whatever
bedmanagement/queue cleanup applies, all inside one transaction opened by Daher's own code, so the
browser session itself is never granted `Assign Beds`, `Edit Admission Locations`, `Get Queue
Entries`, or `Edit Visits`. Full design (authorization checks, transaction boundary, audit-provenance
mechanism, negative-case list) recorded in `PROJECT_STATE.md`'s BLOCKER-10 "UI-015C follow-up".

**Why this is not simply "the BFF ADR-027 rejected."** ADR-027's decision was specifically about a
**separate service holding its own credential**, standing in for the browser's authentication to
OpenMRS — "no service account, anywhere, ever stands in for a staff user for a write," rejected
because it replaces the user's own authorization with "whatever the [intermediary]'s account can
see." This candidate does not introduce a second authenticating identity, a second network hop, or a
second stored credential: the browser still authenticates to OpenMRS exactly as it does for every
other call in this app (the clinician's own OIDC session), and the elevation happens **inside the
same OpenMRS process**, for one narrow, server-validated operation, closer in kind to how OpenMRS's
own core already uses `Daemon` for specific system operations than to a proxying BFF. **This
distinction is a judgment call, not a settled fact** — the candidate does mean the system performs a
mutation the caller's own privileges don't directly authorize, which is philosophically adjacent to
the audit-identity concern ADR-027 raised, and is exactly why this is recorded as its own OPEN entry
rather than asserted as automatically ADR-027-compliant.

**What is NOT yet proven, disclosed rather than assumed favorable:** whether OpenMRS's own
`changedBy`/`dateChanged` audit fields, if explicitly pre-set on the Visit object before handing it
to the Daemon-context save, survive being overwritten by the Daemon user — this could not be proven
without writing and deploying prototype module code, outside this evidence-only ticket's own scope.
The recorded design does not depend on it either way: it specifies a durable, explicit initiator
record (a dedicated, non-clinical `VisitAttributeType`, since `Get Visit Attribute Types` is already
part of the approved nine privileges) as the primary audit mechanism, with native `changedBy`
attribution as a secondary check to verify empirically once a prototype exists, not the sole record.

**Not selected. Not built.** No module, patch, role, or Keycloak config was created by this entry.
Options A–D and the technical recommendation are in `PROJECT_STATE.md`'s BLOCKER-10 record.

**Consequence.** If Liban selects this option, implementing it is its own ticket (an actual OpenMRS
module, a CI-tested build, a real live proof against the disposable stack) — this entry only records
that the candidate exists, was evaluated against ADR-027, and needs its own decision, not a decision
of a decision this repo has not yet made.

---

### Selection and falsification (2026-08-27)

Liban selected Option D and approved the narrow "Terminer l'admission" semantics (ends `stopDatetime`
only — see BLOCKER-10's own restated §14 in `PROJECT_STATE.md`, unaffected by this update). The
implementation ticket's own requirement #29 instructed: *"If the design cannot preserve atomicity or
audit attribution safely, STOP rather than weakening the design."* Both properties this entry names
as the design's job — attribution (line "What is NOT yet proven" above) and atomicity (the "all
inside one transaction opened by Daher's own code" claim above) — were exactly the two things that
had not actually been built and tested yet. Before writing the full production module, a minimal
spike was built and live-deployed into the real disposable OpenMRS stack (`openmrs-adr017f`) to test
them first. Full evidence, the exact spike architecture, and the before/after DB-state table are in
`PROJECT_STATE.md`'s BLOCKER-10 "UI-015/ADR-030 implementation attempt (2026-08-27)" subsection.
Summary:

- **Audit attribution: disproven as a passive property.** Pre-setting `visit.changedBy` to the
  initiating clinician before the Daemon-context `saveVisit()` call has no effect — OpenMRS's own
  `RequiredDataAdvice` save-handler chain overwrites `changedBy`/`dateChanged` from
  `Context.getAuthenticatedUser()` at save time, which inside a daemon thread is OpenMRS's own fixed
  daemon user (`Daemon.DAEMON_USER_UUID`). This confirms — does not merely leave open — that this
  entry's own fallback (a dedicated `VisitAttributeType` audit record) is required, not optional.
  That fallback's own durability under a daemon-context save has not yet been tested.
- **Atomicity: disproven, not merely unverified.** This entry's claim that the operation would run
  "all inside one transaction opened by Daher's own code" was not, in fact, what the spike built or
  what `Daemon.runInDaemonThreadAndWait()` wrapping the existing `VisitService.saveVisit()` call
  actually provides. A live induced-failure test (a genuine, non-privilege exception — the same
  `QueueEntry.sortWeight` not-null defect UI-015B already characterized — thrown partway through the
  module-hook cascade) left `visit.date_stopped` and the bed release durably committed while the
  queue entry was left corrupted. Wrapping the call in `Daemon` removes the specific *privilege-throw*
  trigger UI-015A/B observed, but not the underlying cause: `VisitService.saveVisit()`'s
  AOP-attached module hooks (bedmanagement's, queue's `SaveHandler`/`Validator` beans) do not commit
  as a single transaction with the core Visit save, regardless of caller or thread.

**What this means.** Option D as a general architecture (a narrow, Daher-owned module endpoint,
elevating via the browser's own already-authenticated session, no new credential or hop) is not
itself disproven — the ADR-027 compatibility judgment above still stands on its own terms. What is
disproven is this entry's specific proposed *mechanism*: delegate the actual mutation to the existing
`VisitService.saveVisit()` call, merely wrapped in `Daemon`. A viable alternative likely exists —
the module constructing its own explicit transaction and deliberately orchestrating the Visit/bed/
queue writes itself, rather than delegating to `saveVisit()`'s hook chain — but building and proving
that is a **scope change from what was approved** (the approved read was "wrap the existing
mechanism," not "replace it with a new one"), so per requirement #29 this was not designed or built
without stopping here first. No production module, UI code, or test files exist for UI-015/ADR-030 as
of this entry. `PROJECT_STATE.md`'s BLOCKER-10 stays OPEN; UI-015 has not been started.

---

### Mechanism revised and re-validated (UI-015D, 2026-08-27/28)

The falsification above left one specific, narrow question open, and a follow-up ticket (UI-015D)
was scoped exactly to it: does wrapping the *existing* `VisitService.saveVisit()` call inside a
**module-owned outer transaction** — rather than bypassing it with hand-rolled DAO orchestration —
close the atomicity and audit gaps? It does, live-proven, without needing the DAO-bypass alternative
the previous section speculated about.

**Root cause, proven from source.** `applicationContext-service.xml` (pinned openmrs-core 2.8.7)
wires every core service — and bedmanagement wires its own `bedManagementService` identically — as a
`TransactionProxyFactoryBean` with `preInterceptors: serviceInterceptors`, a list including
`RequiredDataAdvice`. Spring applies `preInterceptors` *before* the `TransactionInterceptor` — outside
the transaction the proxy itself opens. `RequiredDataAdvice.before()` dispatches all registered
`SaveHandler<Visit>` beans (bedmanagement's, queue's) before the target service's own transaction has
even started; bedmanagement's handler calls its own separately-transactional
`unAssignBedsInEndedVisit()`, which — no ambient transaction yet existing — opens and commits its own.
`Context.openSession()`/`closeSession()` bind one Hibernate session to the whole "unit of work"
(Context.java's own documented contract), so an in-memory `stopDatetime` mutation made before calling
`saveVisit()` gets auto-flushed as a side effect of bedmanagement's unrelated commit — durably
persisting the Visit's `stopDatetime` before `VisitServiceImpl.saveVisit()`'s own body ever runs.
Queue's handler then fails in its own, later, independent transaction, rolling back only itself. This
is why the first spike's state partially committed regardless of Daemon; Daemon only bypasses
privilege checks, it does not affect Spring transaction boundaries.

**The fix, live-proven.** Register a module-owned service, wired with the *same*
`TransactionProxyFactoryBean` pattern core and bedmanagement already use (shared `transactionManager`/
`transactionAttributeSource`/`serviceInterceptors` beans), and call it — not `saveVisit()` directly —
as the outermost entry point on the daemon thread. Spring proxying confirmed empirically
(`TransactionSynchronizationManager.isActualTransactionActive()` was `true` on entry and remained the
same active transaction through the `saveVisit()` call). Because a transaction is now already ambient
when bedmanagement's/queue's own nested `@Transactional` calls execute, Spring's default REQUIRED
propagation makes them join it instead of opening independent ones. **No DAO-level bypass of
`saveVisit()`'s hook chain was built or needed** — bedmanagement's and queue's own SaveHandlers/
Validators still run unchanged, just inside one shared transaction boundary.

**Live rollback negative control** (exact UI-015B worst case: bed-assigned, queue-present Visit, same
`QueueEntry.sortWeight`-null failure trigger): before/after state — `visit.date_stopped`,
`bed.status`, `bed_patient_assignment_map.date_stopped`, and `queue_entry.sort_weight` (including the
deliberate-corruption SQL itself) **all unchanged** after the induced exception. Zero partial state —
stronger than the minimum bar, since even the raw `executeSQL()` call used to induce the failure
rolled back with everything else.

**Live success control**, run twice (bed+queue present; no-bed/no-queue): `Visit.stopDatetime` set
correctly, bed released, queue entry closed, linked Encounter untouched in both cases. **Idempotence**,
live-confirmed: a second call on an already-ended Visit returns the same result with zero additional
writes.

**Audit design, solved and live-proven**, not merely designed: a dedicated, non-clinical
`VisitAttributeType` technical record (`operationType=END_DAHER_MATERNITY_ADMISSION;
initiatingUserUuid=…;timestamp=…;result=SUCCESS`), added to the Visit in the same service method
before `saveVisit()`, commits together with the mutation on success — holding the **true initiating
clinician's UUID**, not the daemon user's — and does not exist at all after the rollback above,
satisfying the "never record success when clinical state rolled back" requirement without any
compensating logic. Native `Visit.changedBy` is still overwritten by the daemon user under this
mechanism too (unchanged from the first spike) — this was always the anticipated fallback position in
this entry's original "What is NOT yet proven" paragraph (dedicated attribute as the primary record,
native `changedBy` as a secondary check only), now empirically confirmed rather than assumed.

**Split-context execution (preserving native `changedBy` by alternating the authenticated user
mid-transaction) was considered and rejected as an approach**: OpenMRS has no supported API for
"become another user for one call, then become the original user again" within a single thread, and
manually swapping `UserContext` objects mid-method would be exactly the unsafe `Context` impersonation
this repo's own standing caution warns against. Since the `VisitAttributeType` record above already
solves attribution without touching authentication state, there is no remaining reason to pursue it.

**Not yet built or tested**: the security boundary (this spike has no `Provider`+`Daher Clinician
Admission` role check at all — Daemon bypasses privilege checks universally, so Reception could call
it too as built; the production module must add its own independent check, exactly as this entry
always specified); true concurrent-request race behavior (only sequential double-invocation was
tested); the idempotency-key/ambiguous-response client design; and everything belonging to the actual
production module and UI (the 25 specified tests, the French UI, live OIDC proof of the finished
feature). None of these are open feasibility questions — they are ordinary remaining implementation
work now that the architecture question is answered.

**Consequence.** Option D is now **VIABLE**, with live evidence rather than a proposed design, on the
specific dimensions that were previously disproven. Building the production module, UI, and full test
suite is its own ticket. Full transaction-map proof, evidence tables, and the disposable-environment
notes (including an unrelated operational finding — two OpenMRS modules must never share a
`<servlet-name>`, which broke Spring context refresh for the entire instance until corrected) are in
`PROJECT_STATE.md`'s BLOCKER-10 "UI-015D follow-up" subsection.

---

### Production module built and live-proven; UI/tests not yet started (UI-015E, 2026-08-28)

The production module (`openmrs-config/modules/daherendadmission/`) has been built on the proven
UI-015D mechanism (module-owned `TransactionProxyFactoryBean` service, called as the outermost
transactional entry point on the daemon thread) and live-proven, end to end, against the disposable
stack — first as a `docker cp` deployment onto the existing image, then re-proven against a rebuilt
image with the module and the CSRF fix below baked in at build time. **Still NOT `IMPLEMENTED`** — no
UI, no automated test suite, no documentation-complete BLOCKER-10 closure. Status label is
deliberately `SELECTED DIRECTION — MECHANISM PROVEN, IMPLEMENTATION IN PROGRESS`, not `IMPLEMENTED`,
per this ticket's own gating (§26–§28 — UI, tests, full verification suite — are unstarted).

**Two genuine bugs found and fixed this pass, both load-bearing, neither present in the UI-015D
spike's own (narrower) test surface:**

1. **`req.getPathInfo()` under OpenMRS's module-servlet dispatcher includes the `/{moduleId}/
   {servletName}` prefix, not just the trailing path.** OpenMRS's single Tomcat servlet mapped at
   `/moduleServlet/*` resolves `moduleId`/`servletName` itself and invokes the target servlet's
   `service()` method directly — it does not forward/include in a way that rewrites `getPathInfo()`
   to be relative to the target servlet's own mapping. A regex written assuming `getPathInfo()` would
   be `/{visitUuid}/end` (relative to `EndMaternityAdmissionServlet` itself) instead needed to match
   `/daherendadmission/daherEndMaternityAdmission/{visitUuid}/end` — found by adding a temporary
   diagnostic response body (`debugPathInfo`/`debugRequestURI`/`debugServletPath`, reverted before
   this entry was written, never committed) after every real request failed `400 MALFORMED_REQUEST`.
   The fixed pattern is in `EndMaternityAdmissionServlet.PATH_INFO_PATTERN`'s own javadoc, with an
   explicit note that it must stay in sync with `pom.xml`'s artifactId and `config.xml`'s
   `<servlet-name>`.
2. **Authorization cannot be checked in the servlet's un-elevated request thread.** The missing-
   Provider fail-closed check (`Daher Clinician Admission` role but no `Provider` record) requires
   `ProviderService.getProvidersByPerson()`, which requires the `Get Providers` privilege — deliberately
   **not** one of the canonical clinician's nine (this ticket's own instruction: "Do NOT go back to
   broad clinician privileges"). Checking it in the servlet before elevating therefore always threw
   `APIAuthenticationException: Privileges required: Get Providers`, failing every call, including the
   correctly-authorized clinician's. **Fix:** the full authorization check
   (`DaherMaternityAdmissionServiceImpl.isAuthorizedInitiator`) now runs *inside* the daemon-elevated
   transactional service call, as the very first thing it does (before the pessimistic-lock visit
   lookup) — not in the servlet. This is not a weakening: identity is trusted only via
   `initiatingUserUuid`, captured by the servlet from `Context.getAuthenticatedUser()` **before** any
   elevation and passed as a plain string; the service method must never substitute
   `Context.getAuthenticatedUser()` for it, since inside a daemon thread that resolves to the daemon
   user, not the caller. A new `UNAUTHORIZED` outcome (→ HTTP 403, body `{"status":"FORBIDDEN"}`, no
   detail) was added so an authorization failure is never conflated with `OPERATION_FAILED` (500).

**A methodology trap in constructing the missing-Provider negative control, worth recording so it is
not rediscovered as a surprise.** The obvious way to build this control — pick an existing OpenMRS
user who holds `Daher Clinician Admission` but has no `provider` row in the database — is **wrong**
and produces a false pass. ADR-017E2 activates (creates, if absent) a `Provider` record **at login
time** for any Keycloak identity holding the exact `Provider` client role, via
`oauth2login`'s patched `isProviderRoleGranted`/`activateProviderAccount`. Every leftover synthetic
test user from earlier tickets (`bug25-*`) that had no `provider` row simply hadn't logged in yet in
this environment — the very act of calling `oidc_login()` in the test driver created one on the spot
(confirmed live: `provider.date_created` matched the endpoint call's own `stopDatetime` to the
second, `provider.creator = daemon`, `provider.identifier` = the Keycloak user id). **The only valid
way to build this control is a Keycloak identity that holds `Daher Clinician Admission` but explicitly
does *not* hold the `Provider` client role** (`ui015e-missing-provider`, created for this purpose,
`bug23pass`) — confirmed after login: OpenMRS role `Daher Clinician Admission` present, `provider_id`
`NULL`, endpoint call → `403 FORBIDDEN`, and `provider_id` still `NULL` afterward (no Provider
fabricated by this endpoint or by the failed call itself).

**Rollback and concurrency, proven against the production module without touching production code
to induce failure.** Both controls were built to avoid the trap the very first spike's own evidence
warned about (a test-only failure hook baked into the code under test, requiring a separate
rebuild/un-rebuild cycle and leaving every other control proven against a different binary):
- **Rollback**: a `BEFORE UPDATE` trigger on `queue_entry` (`SIGNAL SQLSTATE '45000'` when
  `ended_at` transitions from `NULL`), installed and removed by plain SQL, no Java changed. Against a
  fixture with bed + queue attached: before/after snapshot across `visit.date_stopped`, `bed.status`,
  `bed_patient_assignment_map.date_stopped`, `queue_entry.ended_at`, and the `visit_attribute` row
  count for that visit — **byte-identical**, HTTP `500 OPERATION_FAILED`. The trigger was then
  dropped and the identical fixture retried successfully (`200 ENDED`), confirming the 500 was
  genuinely the induced failure, not an unrelated defect.
- **Concurrency**: two distinct authorized clinicians (`bug23-clinician1`, `bug23-clinician2`), each
  fully OIDC-authenticated *before* either POST fires, released simultaneously via
  `threading.Barrier(2)` so the pessimistic lock (`DaherEndAdmissionDaoImpl.getVisitByUuidForUpdate`,
  `LockMode.PESSIMISTIC_WRITE`) is genuinely contended rather than one request finishing before the
  second's login completes. Result: exactly one `200 ENDED`, one `200 ALREADY_ENDED`, identical
  `stopDatetime`, and **exactly one** `visit_attribute` audit row (holding the winning clinician's own
  UUID) — the row count is the actual proof the lock serialized the two transactions, not just the
  HTTP response shape.
- **Not directly re-instrumented in the production binary**: UI-015D's
  `TransactionSynchronizationManager.isActualTransactionActive()` logging lived only in the spike. The
  rollback control above is strong indirect evidence of the same property (a broken transaction
  boundary would have left the Visit/bed mutation committed despite the queue-side failure, which it
  did not), but the direct instrumentation was not re-run against this production class. Recorded as a
  disclosed gap, not silently assumed equivalent.

**The CSRFGuard fix is a new class of patch for this repo, recorded on its own terms.** OWASP
CSRFGuard (bundled with OpenMRS core, `WEB-INF/csrfguard.properties`, baked into `openmrs.war` itself)
rejects any POST outside its allowlist by default; only `%servletContext%/ws/*`, `/initialsetup`, and
`/csrfguard` were covered, not arbitrary module servlet paths. No config-only route exists: the file
is a Java properties file *inside the WAR*, not reachable from `openmrs-config/openmrs_config/` the
way Initializer-driven config is, and CSRFGuard's own "config overlay" mechanism
(`org.owasp.csrfguard.configOverlay.hierarchy`) is commented out by default and, if enabled, still
resolves from the classpath (i.e. still inside the WAR) — not from an externally-mountable location.
The fix patches the WAR at Docker build time: `openmrs-config/Dockerfile`'s `base` stage now extracts
`WEB-INF/csrfguard.properties` from `/openmrs/distribution/openmrs_core/openmrs.war` (not
`/usr/local/tomcat/webapps/openmrs.war`, which does not exist at build time — confirmed by inspecting
the base image directly; `startup.sh:59`'s `cp -r "${OMRS_DISTRO_CORE}/." "${TOMCAT_WEBAPPS_DIR}"`
re-copies the distribution tree into `webapps/` on **every** container boot, which is also why a
live `sed` patch of the exploded `webapps/openmrs/WEB-INF/csrfguard.properties` was live-tested and
confirmed **not** to survive a restart before this Dockerfile fix was written), appends one
`org.owasp.csrfguard.unprotected.DaherEndAdmission = %servletContext%/moduleServlet/daherendadmission/*`
line, and re-inserts it with the JDK's own `jar` tool (already on `PATH` in the base image — no new
third-party dependency). **This measurably narrows CSRFGuard's protection surface** — POSTs to
`/moduleServlet/daherendadmission/*` no longer carry a CSRF-token check from this mechanism. What
still gates the endpoint: its own authorization (role + Provider, proven above), same-origin cookie
auth (no cross-site credential exists to forge), and the app's existing
`X-Daher-App-Request` header convention on the UI's own mutating fetch calls (ADR-027/UI-011) —
CSRFGuard was never the only defense in depth for this app's mutating endpoints, but this is the
first time a Daher change has deliberately narrowed its coverage, and that fact belongs here rather
than as a build-step footnote.

**Rebuilt-image re-proof, and what it does and does not cover.** All of the above was first proven
against a `docker cp` deployment of the module onto the existing image, then **entirely re-run**
(success with/without bed+queue, idempotence, malformed UUID, Reception denial, missing-Provider
denial, generic-mutation denial, rollback, concurrency) against an image built from the edited
`Dockerfile` (`docker build --target off`), with the module compiled from committed source in a new
`daherendadmission-build` stage (mirroring `oauth2login-build`'s reproducible-from-pinned-source
pattern) and the CSRF patch applied at build time — all passed identically. Two things this re-proof
does **not** cover, disclosed rather than glossed over:
- The rebuilt image was run against the **already-migrated** `openmrs_adr017f` database, not a
  from-scratch one. A fresh-DB boot was started (`OMRS_CONFIG_CREATE_TABLES=true` against a new
  schema) and abandoned after ~15 minutes of Liquibase changesets (the `stockmanagement` module alone
  runs dozens of ~50-second-each foreign-key changesets) — unrelated to this module, and not
  reproduced further on time grounds. Fresh-DB bootstrap correctness for the platform generally is a
  pre-existing property, not something this ticket touched.
- The container was started with a bespoke `docker run` (replicating the disposable stack's existing
  env vars/mounts/network by hand, plus the new `attributetypes` mount), not `docker compose up`. The
  `docker-compose.yml` mount line was verified for *equivalence* (same host path, same container
  target path as what was hand-mounted; Initializer found the existing `VisitAttributeType` by UUID
  and did not duplicate it) rather than by actually invoking compose. `docker compose config` in the
  verification suite checks the file's syntax, not this runtime behavior.

**Status.** ADR-030's mechanism is now proven end-to-end at the module/backend level, against a
reproducible build, including the two properties (atomicity, audit) originally disproven and the two
UI-015D flagged as unbuilt (authorization, true concurrency). See `PROJECT_STATE.md`'s BLOCKER-10
"UI-015E follow-up" subsection for the full evidence table.

---

### Frontend, module tests, and full verification (UI-015E continued, 2026-08-28)

**Frontend.** `ui/src/features/admission/EndAdmissionPanel.tsx` + `useEndAdmission.ts`, wired into
`AdmissionPanel.tsx`'s existing `COMPLETE` branch (`ui/src/api/client.ts` gained `endAdmission()`).
Mirrors this codebase's own established conventions exactly — `useAdmission`'s in-flight ref guard,
`ApiError`/`ErrorCode` vocabulary (no new error code needed; the endpoint's 401/403/404/4xx/5xx map
onto the existing six), `ErrorState` for rendering, unconditional post-mutation reconciliation via the
caller's own `refresh()` (never a locally-invented "ended" display state — once the Visit is actually
ended, the next authoritative read no longer classifies it as an active admission and `AdmissionPanel`
stops rendering this branch on its own). French copy: button "Terminer l'admission"; confirmation
`role="alertdialog"` "Terminer cette admission en maternité ?" / Confirmer / Annuler. States actually
implemented: idle, Provider-missing (button disabled, explanatory text — same fail-closed pattern as
`AdmissionPanel`'s own admit gate), confirming, pending, error (by `ErrorCode`, via `ErrorState`), and
the parent-driven disappearance once ended. **The original ticket's "8 required UI states" could not
be recovered verbatim after context compaction this session — the count above is what was actually
built and reasoned through fresh, not a reproduction of a list this session could no longer read; it
has not been checked against the original enumeration.** UI-014 (`useMaternityBoard`) needed no
integration code: it already refetches authoritatively on every screen mount (no shared cache), so a
patient whose admission was ended stops appearing on the board the next time it is opened, with no new
invalidation mechanism.

**Tests.** 18 new UI tests (`useEndAdmission.test.ts` ×7, `EndAdmissionPanel.test.tsx` ×9, two new
`permanentGuards.test.ts` static-source guards — no request body ever sent on the end-admission call;
discharge/transfer/diagnosis never referenced) plus one `AdmissionPanel.test.tsx` update. Full suite:
**213/213 passing** (195 pre-existing + 18 new), `tsc -b` clean, `oxlint` clean, `vite build` clean,
the bundle secret scan clean. Module-side: 18 new plain-JUnit tests
(`EndMaternityAdmissionServletTest`, no `BaseModuleContextSensitiveTest`, no OpenMRS `Context`) —
deliberately scoped to what a context-free unit test can actually guard (the path-parsing regex that
was the session's first real bug, and the complete, closed outcome→HTTP-status/JSON-shape mapping),
because neither of the two real bugs this module shipped would have been caught by an H2
context-sensitive suite: both needed the real module-servlet dispatcher or the real privilege graph.
Transaction, bed, queue, and concurrency behavior remain covered by the live proof above, not by
JUnit — recorded as a deliberate scope boundary, not an oversight. `mvn -f
openmrs-config/modules/daherendadmission/pom.xml test`: **18/18 passing.** This is a manual
verification step; the module's own Docker build stage still runs `-Dmaven.test.skip=true`
(matching `oauth2login-build`'s existing, pre-existing behavior) and does not execute tests as part of
the image build.

**One real defect fixed, one recurring toolchain characteristic disclosed, not silently
worked around.** `git diff --check` first found one genuine, one-time defect: a line of
`moduleApplicationContext.xml` mixed spaces and tabs in its indentation. Fixed by rewriting that one
line to plain spaces. Separately — and this is the part that must not be read as fixed —
`maven-parent-openmrs-module`'s inherited `license:format`/`spotless:apply` plugins (run automatically
on every `mvn package`/`mvn test` against this module) write a single tab character on every blank
line inside a Java class body, which `git diff --check` reports as trailing whitespace. This is not a
defect in this module's own code; every OpenMRS module built through this same parent POM carries the
identical pattern. The working tree was stripped clean of it as the **final** step of this pass (after
every Maven invocation this pass performed, including the module rebuild and the Docker image rebuild
that bundles it), and `git diff --check` passes as of that final state — but **any future `mvn
test`/`mvn package` run against this module will reintroduce it**. Whoever next builds this module
needs to either strip trailing whitespace again after their last build, or decide the pattern is
acceptable as-is (it is exactly what upstream OpenMRS modules already carry, unmodified). This is
recorded here specifically so the next person who runs Maven and then sees `git diff --check` fail
does not mistake a known toolchain behavior for something they broke.

**Precisely what was live-tested against which build, stated plainly rather than implied equivalent.**
No Java logic changed after the `httpStatusFor`/`toResultJson`/`toStatusJson` extraction refactor —
every build after it differs only by whitespace. Against that refactored build: success (with/without
bed+queue), idempotence, Reception denial, and missing-Provider denial were all re-run and passed.
Rollback and concurrency were **not** re-run against that exact build — their last live confirmation
was against the build immediately before the refactor (identical outcome-mapping logic, reorganized
into named helper methods, nothing behavioral). After the whitespace investigation and the two
`sed` strips described above, one further rebuild was made and live-tested for a single case (success/
`ENDED`) to confirm the module still loads and responds correctly; the other cases were not repeated a
further time. The working tree as it now stands (after the final strip) was never itself built into an
image — it differs from the last-built image only by the same cosmetic whitespace, not by any Java
change.

**The `--`-in-XML-comment CI guard, actually verified against these files, not just written.** Its
`git ls-files "openmrs-config/**/*.xml"` pathspec was confirmed (via `git ls-files --others
--exclude-standard` against the same glob, since the module's files are still untracked) to correctly
match all 5 of the module's XML files including the nested `moduleApplicationContext.xml`/`config.xml`
— git's `**` pathspec glob does match recursively, unlike a naive shell glob. The guard will fire
correctly once these files are committed; it has not literally run inside a CI job this session (no
GitHub Actions run was triggered), only its exact Python logic, run locally against the real file set.

**`docker compose config`**: clean; the new `attributetypes` bind mount renders with the correct
source/target paths.

**Still not done**: no GitHub Actions CI run was triggered (all `static`-job-equivalent checks above
were run manually, matching the job's own commands); no UI browser smoke test was performed — this
environment has no browser automation tool, so the extensive React Testing Library coverage above
(real DOM, real fired click/keyboard events, real focus assertions) is what stands in for it, not a
substitute claimed as equivalent. `CURRENT_STATE.md` has not been updated. ADR-030 remains gated on
Liban's own review before status can move to `IMPLEMENTED`.

---

### Closure pass (UI-015F, 2026-08-28) — the eight required UI states, and re-verification against a live, unchanged backend

**No architecture change.** This ticket was scoped as closure only: resolve the disclosed proof
gaps from the previous pass, re-run the load-bearing safety evidence, and determine whether
BLOCKER-10/ADR-030/UI-015 can close. Nothing in the module (`openmrs-config/modules/daherendadmission/`)
changed this pass — confirmed via `git diff --stat` showing zero delta for that tree — so every live
proof below re-runs against the exact same production-built image (`daher-health/openmrs-backend:ui015e-rebuild`)
already in place, not a new build.

**The eight required UI states, reconstructed and implemented exactly.** The previous pass could not
recover the ticket's own literal 8-state list after context compaction and built a smaller,
functionally-adjacent set instead. This pass received the canonical list — ACTIVE, CONFIRMATION,
SUBMITTING, ENDED, ALREADY_ENDED, AMBIGUOUS, UNAVAILABLE, ERROR — and rebuilt `useEndAdmission.ts`
and `EndAdmissionPanel.tsx` as a closed state-union matching it exactly (`EndAdmissionState` in
`useEndAdmission.ts`). The mapping from `ApiError` codes to these states:

| Server outcome | UI state |
|---|---|
| `NOT_AUTHORIZED` (403 `FORBIDDEN` from the endpoint's own authorization check) | `UNAVAILABLE` |
| `AMBIGUOUS_WRITE` (dropped connection, unparsable 2xx, or a well-formed 2xx missing the documented fields) | resolved via reconciliation (see below), landing on `ENDED`, `ERROR`, or staying `AMBIGUOUS` |
| `VALIDATION_ERROR` / `NOT_FOUND` / `SERVER_ERROR` / `CONFLICT` (a definite, synchronously-answered rejection, or a 500 that ADR-030's own proven atomicity guarantees changed nothing) | `ERROR` |
| 200 `ENDED` | `ENDED` |
| 200 `ALREADY_ENDED` | `ALREADY_ENDED` (deliberately distinct — a zero-write idempotent close, not conflated with a real mutation) |

**The AMBIGUOUS resolution is the load-bearing new logic** (§13's own requirement: "after possible
commit: authoritative reconciliation; if Visit stopped: ENDED; if Visit definitely active:
ERROR/retryable; if authoritative state cannot be obtained: AMBIGUOUS; never blind retry"). Implemented
by widening `useAdmission`'s `refresh()` to *return* the resolved `AdmissionReadState` rather than only
setting it, so `useEndAdmission`'s `resolveAmbiguous()` can inspect what an authoritative re-read
actually found: Visit no longer `COMPLETE` → `ENDED` (the response was lost, not the commit); Visit
still `COMPLETE` → `ERROR` with code `AMBIGUOUS_WRITE` (a known, retryable failure — the mutation did
not go through); the reconciliation read itself failing → stays `AMBIGUOUS`. The mutation
(`endAdmission()`) is never resent as part of this resolution — only `getActiveVisits` is, via the
existing `reconcile` callback. `ERROR`'s own retry action, by contrast, **does** resubmit directly
(an explicit human click, not a "blind" retry) — this asymmetry is deliberate and is the single most
important behavioral distinction in the redesigned hook.

**`ENDED`/`ALREADY_ENDED` reconciliation is deferred to an explicit "Fermer" dismissal**, not fired
automatically on success. An earlier draft of this fix considered auto-firing the parent's
reconciliation the instant a 200 arrived, reasoned as satisfying "successful authoritative
reconciliation" fastest — rejected during design because it races the terminal message's own
visibility: the parent's `refresh()` would flip `read.status` away from `COMPLETE` and unmount
`EndAdmissionPanel` before a human (or a test) could observe "Admission terminée." Deferring
reconciliation to the user's own "Fermer" click makes both the state and its test deterministic, and
still satisfies §12's own reconciliation requirement — just user-triggered, matching the existing
`AdmissionPanel` `AMBIGUOUS` state's "Vérifier à nouveau" convention rather than inventing a new one.

**Tests**: 15 new `useEndAdmission.test.ts` (state-machine transitions, the AMBIGUOUS resolution's
three branches, the ERROR-vs-AMBIGUOUS retry asymmetry, the in-flight double-submit guard), 13
rewritten `EndAdmissionPanel.test.tsx` (one explicit test per required state, named for it), and 12
new `client.test.ts` `endAdmission` tests — this last group is §13's own fault-injection requirement,
done at the actual `fetch` boundary (mocked timeouts/rejections/unparsable bodies/every documented
HTTP status), not merely against an already-classified `ApiError`, since a live container cannot
cleanly simulate "the server committed but the response was lost in transit." Total UI suite:
**236/236 passing** (up from 213), `tsc -b`/`oxlint`/`vite build`/bundle-secret-scan all clean. No
module Java changed, so the 18 module JUnit tests were not re-run via Maven this pass (deliberately —
re-running would re-dirty the working tree via the known spotless/license whitespace behavior for no
verification gain); their source (`grep -c "@Test"`) was confirmed still 18, matching the last actually-
executed run.

**Live re-verification against the unchanged production image**, with exact before/after values
recorded, not inferred from exception text:

- **§4 authorization, enumerated not name-checked.** `SELECT rp.privilege FROM role_privilege WHERE
  role=...` (not a role-name check) confirmed exactly 9 privileges for `Daher Clinician Admission`
  (`Add Encounters, Add Visits, Get Admission Locations, Get Beds, Get Encounters, Get Patients, Get
  Visit Attribute Types, Get Visit Types, Get Visits` — alphabetical, no `Assign Beds`/`Edit
  Visits`/`Edit Admission Locations`/`Manage Queue Entries`/`Get Providers`) and exactly 3 for `Daher
  Reception`. `git diff --stat -- openmrs-config/initializer/roles/` is empty — zero privilege-roster
  changes this entire effort. Live: clinician → `200 ENDED`; Reception → `403 FORBIDDEN`;
  `ui015e-missing-provider` (role held, `Provider` client role withheld, per the methodology already
  recorded above) → `403 FORBIDDEN`; clinician against generic `POST /visit/{uuid}` (`Edit Visits`) →
  `403`; generic `POST /queue` → `403`; generic `GET /bed` (a legitimate, retained read) → `200`; a
  direct `POST /beds/1` bed-reassignment attempt → `403 Privileges required: Assign Beds` (a new
  control this pass, isolating the exact privilege denied rather than only observing a generic 403).
- **§5 rollback**, a fresh fixture (bed + queue attached), the same external `BEFORE UPDATE` trigger
  on `queue_entry` (no code change): before/after recorded across `visit.date_stopped` (`NULL` →
  `NULL`), `bed.status` (`OCCUPIED` → `OCCUPIED`), `bed_assign.date_stopped` (`NULL` → `NULL`),
  `queue.ended_at` (`NULL` → `NULL`), `encounter.date_voided` (`NULL` → `NULL`),
  `encounter_provider_count` (`0` → `0`), `audit_rows` (`0` → `0`) — byte-identical, `500
  OPERATION_FAILED`.
- **§6 success, three cases, exact values.** Case A (no bed, no queue): `visit.date_stopped` set,
  `visit.uuid`/`visit_type_id`(6)/`location_id`(62) unchanged, `encounter.date_voided` unchanged,
  exactly 1 audit row (`initiatingUserUuid=2c67d291-...`, the real clinician). Case B (bed, no queue):
  same, plus `bed.status` OCCUPIED→AVAILABLE, `bed_assign.date_stopped` set,
  `encounter_provider_count` 0→0. Case C (bed + queue): same, plus `queue.ended_at` set. All three:
  exactly one audit row, correct clinician UUID, `Encounter`/`encounterProviders` untouched.
- **§7 concurrency**, real result not just unit-test behavior: two distinct clinicians
  (`bug23-clinician1`, `bug23-clinician2`) behind a `threading.Barrier(2)` against the same active
  Visit → `CALL0 200 ALREADY_ENDED`, `CALL1 200 ENDED`, identical `stopDatetime`, and querying the DB
  directly: exactly one `visit_attribute` row, holding `bug23-clinician2`'s own UUID (the winner).
  This original concurrency fixture had **no bed and no queue attached** — it proved exactly-once
  closure and single-winner audit attribution under contention, but left bed/queue cleanup under
  contention unproven (bed/queue cleanup had only been proven separately, under a *single* request,
  in Case C above).

  **§7.B — bed+queue-under-contention closure (UI-015 micro-closure, 2026-08-28), the gap above
  resolved with a real proof.** A fresh disposable admission (patient 133, `visit_id=57`,
  `encounter_id=51`) was built with a genuine assigned bed (`bed_id=2`, status `OCCUPIED`,
  `bed_patient_assignment_map_id=13`, `date_stopped=NULL`) and a genuine open queue entry
  (`queue_entry_id=14`, `visit_id=57`, `ended_at=NULL`), zero ADR-030 audit rows initially. Two
  distinct clinicians (`bug23-clinician1`, `bug23-clinician2`) behind a `threading.Barrier(2)` closed
  it simultaneously against the final production-built image: `CALL0 200 ENDED`, `CALL1 200
  ALREADY_ENDED`, identical `stopDatetime` (`2026-08-28T05:05:02.000+0000`). Authoritative DB read
  after: `visit.date_stopped` set once, `visit.uuid`/`visit_type_id`(6)/`location_id`(62) unchanged,
  `bed.status` OCCUPIED→AVAILABLE, exactly one `bed_patient_assignment_map` row for that bed with
  `date_stopped` set once (no duplicate cleanup row), exactly one `queue_entry` row for that visit
  with `ended_at` set once (no duplicate), `encounter.date_voided` still NULL,
  `encounter_provider_count` still 0, and exactly **one** `visit_attribute` audit row
  (`visit_attribute_id=22`) holding `initiatingUserUuid=2c67d291-...` — `bug23-clinician1`'s own UUID,
  the actual winner (`CALL0`/`ENDED`). A further sequential call afterward returned `200
  ALREADY_ENDED` with the same `stopDatetime`, and the audit/bed/queue row counts were unchanged
  (still exactly 1/1/1) — zero new writes. Bed+queue cleanup racing two simultaneous closers is no
  longer untested; it is proven, with exact before/after values, not inferred from the two separate
  proofs above.
- **§8 idempotence**: a further call on the already-ended Case C visit → `200 ALREADY_ENDED`, same
  `stopDatetime`, `audit_rows` still exactly 1, `Encounter` unchanged.
- **§9 audit**, all items reconfirmed with exact values: native `visit.changed_by` = user_id 2 =
  `daemon` for every successful case (proven, not assumed); the dedicated type
  (`visit_attribute_type_id=8`, name `Daher End-Admission Audit`, UUID
  `a66602e1-a519-4bea-a460-f4993fec5afb`, datatype `FreeTextDatatype`, `min_occurs=0`/`max_occurs=1`,
  not retired) correctly holds the real initiating clinician's UUID in every case; commits only on
  success (audit_rows=1 in every §6/§7/§8 case) and rolls back with everything else on failure
  (audit_rows=0 in §5); the stored value (`operationType=...;initiatingUserUuid=...;timestamp=...`) is
  a plain technical string — no token, session id, or credential of any kind; the clinical UI never
  requests or renders `VisitAttribute` data at all (structural — `ActiveVisit`'s own type has no
  `attributes` field, and no query in `client.ts` asks for one).
- **§10 VisitAttributeType provisioning, existing-DB idempotence.** A full container restart against
  the same already-migrated database (re-running Initializer's `attributetypes` CSV pass): the row
  count for that UUID stayed exactly 1, same `visit_attribute_type_id=8` — a second provisioning pass
  did not duplicate it. The genuinely-fresh-database attempt is recorded separately below.
- **§11 CSRF/security boundary.** The one new `csrfguard.properties` line
  (`org.owasp.csrfguard.unprotected.DaherEndAdmission = %servletContext%/moduleServlet/daherendadmission/*`)
  is still the only change, confirmed live in the running image. A **new control this pass**: `POST
  /openmrs/moduleServlet/tasks/formDownload` (a different, pre-existing module's own moduleServlet
  path, chosen because it is the only other module in this image with one) — still returns the
  "Invalid or Missing CSRF Token" page, proving the exemption is scoped exactly to
  `daherendadmission/*` and did not weaken CSRFGuard for any other module servlet. An unauthenticated
  POST to the narrow endpoint itself → `302` to the OIDC login (OpenMRS's own SSO filter denies it
  before CSRFGuard or the servlet is ever reached — still a denial, just via redirect). A **new
  payload-injection control**: a POST carrying a JSON body with `startDatetime`, `stopDatetime`,
  `visitType`, `location`, `patient`, `bed`, and `queue` fields, all attacker-supplied and wrong —
  the endpoint ignored all of them (it never reads the request body at all); the resulting Visit's
  `visit_type_id`/`location_id`/`patient_id` and the actual `stopDatetime` written were exactly the
  server's own real values, confirmed by direct DB read, not the injected ones.
- **§14 UI-014 removal proof, via the real server query** (`GET /visit?location=...&visitType=...`,
  the exact query `getMaternityBoardVisits()` issues — not local card state): before ending a fresh
  fixture, the board query included the patient (`True`); after, it did not (`False`).

**§10.C — fresh-database boot, with a genuine control, deferred to DH-017 as an unrelated
environmental race, not a module defect.** Two containers were booted in parallel against two
independently fresh, empty MariaDB databases: the **treatment** (`daher-health/openmrs-backend:ui015e-rebuild`,
this module baked in, the `attributetypes` mount present) and the **control**
(`daher-health/openmrs-backend:bug25-final`, a real image built the day before ADR-030's first line of
code, no module, no `attributetypes` mount). Both were driven by identical Liquibase migration content
from the moment they started: the same changeset IDs, in the same order, at points several minutes in
diverging by no more than single-digit milliseconds. At the specific point this was stopped, both had
independently executed `liquibase.xml::stockmanagement-1758855877841-128::dev` — a changeset belonging
to the pre-existing `stockmanagement` module, nothing to do with ADR-030 — completing in **the
identical 53117ms**, confirmed via each container's own log (`grep -c` showed 128 stockmanagement
changesets processed on each side at the moment of comparison, matching line for line). This is not a
timing coincidence read charitably; it is two independent processes hitting the same real bottleneck
(most plausibly I/O contention on the shared `mariadb-ui009` container, worsened by running three
OpenMRS boots against it simultaneously) at a rate of roughly one changeset per 50+ seconds, well
before either process reaches the point where this module's own Initializer-driven `attributetypes`
CSV would even be read. **Since this module carries zero Liquibase changesets of its own** — its only
schema-adjacent artifact is the Initializer CSV, which runs after all Liquibase migrations, not during
them — there is no mechanism by which ADR-030 could be contributing to this specific slowdown, and the
controlled pair above demonstrates it does not, empirically rather than by that structural argument
alone. Neither boot was carried to full readiness (both were stopped by the same budgeted-single-attempt
constraint this ticket set, after confirming the identical-changeset/identical-duration match, rather
than waiting out what full-clone tests would put at another 15–20+ minutes of the same stockmanagement
phase alone). **The specific verification this leaves undone** — proving the audit `VisitAttributeType`
is provisioned exactly once by Initializer on a truly from-scratch database, end to end — is deferred
as evidence debt under **DH-017**, consistent with the precedent ADR-029 already established for an
unrelated first-install/module-migration race. This is explicitly NOT a module-specific bootstrap
failure (the pinned closure criterion for treating it as blocking): the identical image family without
this module hits the identical wall.

**Closure verdict.** Every mandatory gate this ticket set has a passing, live-evidenced result against
the current, unchanged production build: the eight required UI states exist and are individually
tested; the authorization boundary is enumerated (not name-checked) at exactly 9/3 privileges with
zero roster changes; rollback, all three success cases, concurrency, and idempotence are re-proven
with exact recorded values; the audit mechanism's type, attribution, and rollback behavior are
confirmed; generic Visit/bed/queue authority remains denied by direct, isolated controls; the CSRF
boundary is confirmed both present and narrowly scoped via a real sibling-module control; arbitrary
payload injection is confirmed inert; UI-014 removal is confirmed via the real server query; the full
UI test suite (236/236), lint, typecheck, build, and secret scan are green; `git diff --check` and
`docker compose config` are clean. The one item not fully closed — a truly from-scratch database boot
— is deferred to DH-017 with a genuine controlled-pair proof that the blocking factor is a pre-existing
platform characteristic, not this module. **ADR-030 moves to `ACCEPTED / IMPLEMENTED`.** This is
Liban's own review to confirm, not this pass's to declare launched — "pilot-ready", never
"launched" or "in production" (AGENTS.md's own standing rule), and nothing here has touched a real
patient, real staff account, or the real facility.

## ADR-031 — SELECTED / BUILT + LIVE-VERIFIED, NOT COMMITTED (2026-08-29, DH-011P): Design B — a
## standalone `daher_analysis_correlation` table for per-added-test DH-025 correlation

**Date raised:** 2026-08-29 (DH-011O characterized the blocker and two candidate designs; this
ticket is the same day's follow-on). **Status:** Liban selected **Design B** — recorded verbatim in
the ticket text as "LIBAN DECISION: SELECT DESIGN B." Implemented and live-verified end to end this
same pass; **not yet committed or pushed**, pending Liban's review of the full handoff (this ticket's
own standing instruction: "Do NOT commit or push until the full ticket passes... Then STOP").

**The decision, restated precisely.** DH-011O established that `Sample.clinical_order_id`, a single
scalar column, is pivoted through by `searchDaherCorrelationNatively`'s existing native lookup in a
way that fails closed on either zero or more than one linked `Sample` — reusing the existing
`electronic_order`/`clinical_order_id` mechanism for a second, per-added-test correlation on the same
accession cannot be made to work without breaking one of the two orders' own probes. Two designs were
evaluated: **(A)** a new nullable column directly on `clinlims.analysis`, requiring a first-ever patch
to upstream `Analysis.java` itself; **(B)** a new standalone correlation table
(`daher_analysis_correlation`), mapped by a new JPA entity, requiring zero patches to any existing
upstream `.java` or `.hbm.xml` file. Liban selected B.

**Why B, as DH-011O's own writeup argued and this pass's implementation confirmed in practice.**
`Analysis.java` is a core clinical valueholder no Daher patch had touched in this repository's
history before this decision; a column-plus-accessor patch to it would have grown both the committed
patch surface and the upgrade/rebase burden every other DH-011 ticket has deliberately kept small.
Design B instead follows the same shape already established for order-level correlation
(`DaherOrderCorrelation.java`/`DaherOrderCorrelationTransaction.java`: new files, not patches to
existing upstream classes) and matches `hibernate.cfg.xml`'s own stated preference for
annotation-based JPA mappings over further `.hbm.xml` patching. Built this pass: a table with a
**unique** constraint on `correlation_value` only (deliberately **not** on `analysis_id` — a
canceled-then-revived analysis can legitimately carry more than one correlation across its
lifecycle, a real clinical possibility this design leaves open rather than closes by an
over-tight constraint); a JPA entity/DAO/service triple; a `@Component`-wrapped
`@Transactional` orchestration class (`DaherAddedTestTransaction`) covering the native
`SampleEditService.editSample` write and the correlation insert in one transaction; and a
dual-namespace extension to `searchDaherCorrelationNatively` that fails closed
(`InternalErrorException`) if a correlation value is ever found in both the
`electronic_order.external_id` and `daher_analysis_correlation.correlation_value` namespaces at
once — DH-011O's first flagged open question, now answered and live-proven, not merely stated.
DH-011O's second flagged question (whether the new namespace's `Bundle.total` should read `1`) is
answered the same way: it does, for consistency with the existing namespace's convention.

**What this decision did not settle, and correctly left to implementation, not to itself.** Whether
`SampleEditService.editSample`'s return type needed to change (it did — from `void`, via a shared
singleton-bean instance field, to `List<String>`, eliminating a live concurrency hazard, not just
routing around it) was an implementation detail DH-011O's design comparison could not have
anticipated from source reading alone; it surfaced only once design B's transactional orchestration
was actually built and needed a race-safe way to learn which `Analysis` id `editSample` had just
written. Full implementation account, the one real bug found by live testing (a second, spurious
`Analysis` per add-test event, root-caused to `SampleAddService`'s parallel sample-XML-driven
creation path and fixed by submitting an empty `<samples/>` document), the full 10-item live
verification matrix, and a known representation gap (the added-test response echoes the *original*
order's correlation, not the add-test event's own, in its `source-event` identifier — safe for the
one real consumer, the adapter, which never trusts this field, but recorded rather than silently
accepted): `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011P section.

**ADR-031 status: SELECTED and BUILT, live-verified against fully synthetic disposable
infrastructure, real facility never touched. Not `ACCEPTED / IMPLEMENTED`** in the sense ADR-030
uses that phrase — that status is reserved for Liban's own review of a passing handoff, which this
ADR is recording the evidence for, not asserting on his behalf. Nothing has been committed or
pushed. DH-011 overall remains NOT COMPLETE: DH-011G/H/I/J/K/L are all still open and unaffected by
this decision.

**Closure pass (DH-011P2, 2026-08-29) — the design itself is unchanged; three verification gaps an
independent review found in DH-011P's own handoff are now closed with evidence.** ADR-031 selected
design B; it did not change here. What changed: (1) the added-test response's echoed correlation
identifier — recorded above as a known gap, "Liban's call whether it matters" — is fixed, not left
open. `FhirTransformServiceImpl`'s shared transform now looks up
`daher_analysis_correlation.getByAnalysisId` before falling back to the initial-order identifier; an
Analysis with more than one added-test correlation (the canceled-then-revived case this ADR's own
unique-constraint reasoning above already anticipated) emits all of them as repeated identifiers,
live-confirmed via an actual accidental collision during this pass's own test setup, not merely
reasoned about. (2) True DH-025 crash-after-target-commit recovery — this ADR's text above states
design B's transactional seam and rollback proof but is explicit that crash-recovery *through the
adapter* was never attempted — is now demonstrated with a test-only integration harness assembling
the real `OpenElisClient`, `RecoveryCoordinator`, and `LedgerRepository` (no production entrypoint
for this exists yet; DH-011E's poller remains unwired) against a real disposable Postgres and OpenELIS
stack: a controlled fault after a genuine target commit and before ledger completion, then a fresh
coordinator/ledger pair correctly reconciles to the existing write with zero second writes. Committed
as `adapter/tests/integration/test_dh011p2_crash_recovery.py`, gated behind live-infra env vars so it
never runs in CI. (3) `updateServiceRequest` (PUT) reachability, left as "unreachable via the
adapter's transport" by DH-011P, is now stated precisely: reachable by the same HTTP Basic credential
class the Daher integration would use (live-confirmed — authenticated, routed, fails only on payload
validation), but not by any code path the adapter's own `OpenElisClient` can construct (it defines no
PUT-capable method at all) — contained by a new CI static guard, not merely documented. Full account
of all three, plus a §4 patch-surface review this pass performed (a third `editSample` caller,
`SampleEditController`, found and confirmed behaviorally unaffected — Java discards its unused return
value) and the corrected DH-011P2 verification matrix: PROVENANCE.md's DH-011P2 section. Still not
committed or pushed; DH-011 overall still NOT COMPLETE.

**Final closure pass (DH-011P3, 2026-08-29) — DH-011P2's own tolerated-multiplicity design is
REVOKED, not extended.** DH-011P2 above records, as a deliberate, reviewed choice: `analysis_id`
left non-unique so a cancelled-then-revived Analysis could legitimately carry two added-test
correlations across its lifecycle, with the representation layer emitting all of them as repeated
identifiers. This ticket surfaced that tradeoff to Liban directly — enforcing strict one-Analysis
-one-correlation uniqueness would make a Daher re-add after native cancellation fail permanently,
since `editSample`'s revival path retargets the same Analysis id under a new correlation. Liban
chose the strict invariant over the accommodation: **`UNIQUE(analysis_id)`, added via a new
migration (`005-daher-analysis-correlation-unique-analysis.xml`, not an edit to the already-applied
004), enforced at the database and backstopped in the FHIR transform (which now fails closed —
`InternalErrorException` — rather than emit multiple identifiers if it ever finds more than one row,
a state the DB constraint should make unreachable in normal operation).** Live-verified, one
disposable stack: fresh-DB migration succeeds; migration re-run against an existing DB with
non-duplicate data succeeds; migration re-run against an existing DB with a genuine duplicate
`analysis_id` fails via Postgres's own atomic `CREATE UNIQUE INDEX` — Liquibase's transactional
wrapping rolled back the whole changeset including the earlier `DROP INDEX`, zero rows touched,
zero data destroyed, the changelog correctly shows the migration never applied. A direct concurrent
race — two different correlation values simultaneously attempting to claim the same Analysis — was
also run: one succeeded, one failed with a real `UniqueViolation` under genuine contention, exactly
one correlation row survived. The DH-011P2 real-adapter crash-recovery integration test was
re-run unmodified against this ticket's rebuilt image with no regression. The cross-namespace
collision control (a separate mechanism from this new constraint) was re-confirmed unaffected.
Full account, including the exact application-level fail-closed check and its own control-tested CI
guard (one real false-negative caught mid-pass — an early guard matched the phrase "CREATE UNIQUE
INDEX" in the migration's prose comment rather than its actual SQL, fixed and re-verified):
PROVENANCE.md's DH-011P3 section. **READY TO COMMIT: Yes**, per this ticket's own gate — this is
the final review item before the DH-011P/P2/P3 implementation commits and pushes. DH-011 overall
remains NOT COMPLETE: DH-011G/H/I/J/K/L are all still open and unaffected by this decision.

## ADR-032 — SELECTED / BUILT + LIVE-VERIFIED (2026-08-30, DH-011G2): Option D — the OpenMRS
## Patient UUID as Daher V1's source-system patient identity, linked via a standalone native
## OpenELIS table

**Date raised:** 2026-08-30 (DH-011G's investigation produced a decision matrix and a technical
recommendation — Option B; Liban rejected that recommendation and selected Option D instead, in
the same message that authorized implementation — DH-011G2). **Status:** Implemented and
live-verified end to end this same pass, against fully synthetic disposable infrastructure.

**The decision, restated precisely.** DH-011G's own investigation proved two structural facts that
narrow every option: (1) OpenELIS's FHIR Patient ingestion (`FhirTransformServiceImpl
.createOePatientManagementInfo`) recognizes exactly four hardcoded identifier systems
(`pat_nationalId`, `pat_subjectNumber`, `pat_stNumber`, `pat_guid`) and silently drops anything
else; (2) `Patient.id`/`pat_guid`/`pat_uuid` are always server-generated on create — a
client-supplied value is discarded, live-confirmed both by DH-011G's own investigation and again
this ticket. DH-011G's technical recommendation was **Option B** — route OpenMRS's own
idgen-generated patient identifier into OpenELIS's `pat_nationalId` bucket — on the grounds that it
needed zero OpenELIS patch. **Liban rejected Option B**, for a reason DH-011G's own evidence did not
weigh: `pat_nationalId` semantically represents a national identifier, and using it as a Daher/
OpenMRS machine-linkage bucket would conflate two different identities and could conflict with a
real national identifier a facility enters later. Liban also rejected Option C (a new Daher-owned
identifier written back into OpenMRS) — the adapter has no OpenMRS write capability today, and a
new identifier origination/seeding scheme adds a mechanism this ticket's own evidence gave no
reason to need.

**Option D, selected:** use the existing, already-stable OpenMRS Patient UUID as the *source*
identity (never re-derived, never re-generated — the same value OpenMRS itself assigned at patient
creation, proved this ticket to be identical to OpenMRS's own FHIR `Patient.id`, live, on the
pinned reference-application build), and persist the OpenMRS-UUID ↔ OpenELIS-Patient relationship
in a **Daher-owned native OpenELIS table** (`daher_patient_identity`), never in any of the four
existing `pat_*` buckets. The linkage value is explicitly **not** a national identifier, an MRN, a
civil identity, or a cross-facility patient identity — it is an opaque machine-linkage key, scoped
to one facility's OpenMRS↔OpenELIS pair (see the facility-scope paragraph below).

**Canonical contract:**

```
system: urn:daher-health:openmrs-patient-uuid
value:  the real OpenMRS Patient UUID (server-generated, stable, proved this ticket)
```

Persisted in `daher_patient_identity` (`patient_id` → `clinlims.patient(id)`, `identifier_system`,
`identifier_value`, `created_at`), with `UNIQUE(identifier_system, identifier_value)` (one OpenMRS
UUID can never link to two OpenELIS Patients) and `UNIQUE(patient_id, identifier_system)` (one
OpenELIS Patient can never carry two OpenMRS UUIDs under this system) — the second constraint was
the ticket's own "evaluate/enforce" choice, decided **ENFORCE**, because the alternative is exactly
the mechanism that would let two distinct OpenMRS patients accidentally collapse onto one lab
identity.

**Why this needed a new OpenELIS patch (`PatientProvider.java.patch`, `006-daher-patient-identity
.xml`, and five new Java files) where Option B claimed zero.** Accepting that cost was the explicit
tradeoff Liban's decision made: Option D requires (a) a create-time interception that extracts the
Daher-system identifier from the incoming FHIR Patient and persists it atomically alongside the
native Patient row (`DaherPatientIdentityTransaction`, mirroring `DaherOrderCorrelationTransaction`'s
already-proven pattern and its own documented reason a plain `@Transactional` on `PatientProvider`
itself would break HAPI's provider registration), and (b) rerouting `@Search`'s handling of this one
identifier system to native `daher_patient_identity` state instead of the standalone secondary FHIR
store — closing DH-011G's own split-store hazard for the Daher identity system specifically (see
below). Every other identifier system (`pat_nationalId`, `pat_guid`, accession, ...) is completely
unchanged by this patch.

**DH-011G's split-store hazard, closed for this system, not in general.** DH-011G found that
`PatientProvider.createPatient`'s native persist (`patientService.persistPatientData`) and its sync
to the standalone `openelis-fhir` store (`fhirTransformService.transformPersistPatient`) are
non-atomic — the sync is try/caught, log-and-continue — so a native Patient could exist while a
search against that external store found nothing. This patch's native routing for the Daher system
means create and search both consult the same authoritative native state (`daher_patient_identity`
+ `clinlims.patient`), so **the answer to "can native Patient creation succeed while the
authoritative Daher identity search returns zero?" is NO for this system** — live-proven this
ticket via a real crash-after-commit recovery test (below). The underlying non-atomicity between
native persist and the standalone store's sync is **still true** for `pat_nationalId`/`pat_guid`/
etc. — this patch does not touch that, by design (§7 of the ticket: "Other OpenELIS identifier
systems should retain their existing behavior unless independently justified").

**Facility scope, not re-litigated.** `docs/ARCHITECTURE.md`'s existing ADRs (§3/§14/§16,
ADR-020/ADR-021) already establish one isolated clinical stack per facility, never a shared
multi-tenant database — DH-011G's own audit confirmed this and this ticket inherits it unchanged.
`urn:daher-health:openmrs-patient-uuid` is authoritative **inside one facility's paired
OpenMRS/OpenELIS deployment only** — it is not, and this ticket does not build, a national or
cross-facility master patient index. If cross-facility patient continuity is ever needed, the wider
identity tuple (facility identity + source system + patient UUID) is future work, explicitly not
attempted here.

**Live-verified, one disposable stack (patched `openelis-webapp` image + Postgres; a separate
disposable OpenMRS instance for the source-side proof), fully synthetic:**

- **OpenMRS `Patient.uuid` == FHIR `Patient.id`, live, on the pinned reference-application build**
  (not merely inferred from FHIR2's own translation convention): created a synthetic patient via
  the core REST API (the same contract UI-012 uses), read it back via `GET /ws/fhir2/R4/Patient/
  <uuid>` — identical value. Server-generated, stable across a real demographic edit (name/DOB
  changed, UUID unchanged) and across a real container restart. Three synthetic patients (a mother
  and two "twins" sharing DOB and family name, no national ID/phone) each received distinct UUIDs —
  demographics never enter identity assignment.
- **Cardinality matrix (§8):** ZERO for a never-created UUID; ONE, with the Daher identifier
  correctly echoed on the response (§15), for a created Patient. 2+ is made structurally impossible
  by `UNIQUE(identifier_system, identifier_value)` — proven at the DB level (a direct duplicate
  insert fails with a real constraint violation) rather than through the application, since the
  application itself cannot construct that state; the adapter-side AMBIGUOUS-on-many branch is
  covered by its own passing unit test. Database-unreachable was not live-exercised (stopping the
  disposable DB risked destabilizing the rest of the pass for marginal new evidence) — covered by
  adapter-side unit tests and the same exception-propagation pattern already proven for
  `ServiceRequestProvider`'s identical native-search design.
- **Corruption controls (§14), at the DB level:** a duplicate `identifier_value` under the same
  system fails (`daher_patient_identity_system_value_uk`); a second `identifier_value` for the same
  `patient_id` under the same system fails (`daher_patient_identity_patient_system_uk`); a
  malformed (non-UUID-shaped) `identifier_value` fails the `CHECK` constraint.
- **Atomicity/rollback (§9):** a create with a name that fails OpenELIS's own (pre-existing, unrelated)
  Person validator leaves zero trace — no native Patient, no linkage row — confirmed both by DB
  query and by a subsequent native search returning ZERO. A resubmission of the same OpenMRS UUID
  with completely different demographics is rejected by the transaction's own pre-check and never
  creates a second Patient; the adapter's `create_openelis_patient` correctly reconciles the
  rejected attempt to the one Patient that actually exists (the same "duplicate conflict reconciles
  to the winner" shape already proven for `deliver_service_request`), never raising past a
  legitimate SUCCESS.
- **Crash-after-commit (§11):** a real Patient + linkage commit, a simulated process crash before
  the ledger's own completion, then a fresh `DaherPatientIdentityCoordinator`/`LedgerRepository`
  (simulating a restart) reconciling to the already-created Patient with the writer called zero
  times — proven against the real adapter's `RecoveryCoordinator`-shaped safety components, not a
  unit fake.
- **Concurrency (§12/§13):** two independent `OpenElisClient` instances racing
  `ensure_openelis_patient` for the identical OpenMRS UUID converge on exactly one native Patient
  (DB uniqueness authoritative); two instances racing two *different* UUIDs produce two fully
  isolated Patients with no cross-link.
- **§17 (demographics don't change identity):** a real `PUT` changing name/gender/birthdate leaves
  the Daher linkage, and the native search resolving to it, unchanged.
- **§18 (no auto-linking of pre-existing patients):** not live-tested — no code path in this patch
  performs implicit linking by demographic similarity at all; this is a source-guaranteed absence,
  not a behavior to demonstrate.
- **§24, answered:** "Can native Patient creation succeed while the authoritative Daher identity
  search returns zero?" — **NO**, for the Daher identity system, live-proven by the crash-recovery
  test above. Still **YES**, unproven either way and out of scope, for every other identifier
  system OpenELIS's secondary-store sync covers.

**A pre-existing defect this ticket's own new code inherits, not introduces, and does not fix.**
`PatientProvider.createPatient`'s outer exception handler (pristine, unmodified by this patch) never
calls `outcome.setCreated(false)` on any exception path, including this patch's own new
already-in-use pre-check rejection — so a *definite* rejection can still surface as an HTTP 201 with
a null body. Live-reproduced this ticket for exactly that rejection path. This does not weaken
data safety: the adapter's `create_openelis_patient` never trusts the POST's status regardless,
always reconciling via the native read-back this patch's own routing makes authoritative. Fixing
the general false-201-on-exception shape was judged out of scope (it predates this ticket, affects
every `PatientProvider` exception path, and the adapter's existing discipline already makes it
non-load-bearing for correctness) — recorded here so it is not mistaken for something this ADR
claims to have closed.

**Test counts, adapter (measured, not extrapolated):** 161 → **176** unit tests (15 new:
`patient_identity_probe`/`create_openelis_patient`/`ensure_openelis_patient` cardinality, injection,
rejection, and reconciliation cases) + **9** new gated live-integration tests (skipped by default
and in CI, mirroring `test_dh011j_cancellation_crash_recovery.py`'s own opt-in shape exactly),
9/9 passing against the disposable stack this pass built. OpenELIS: the patched reactor compiles
to a produced `OpenELIS-Global.war` (no dedicated JUnit suite added — successful compilation of the
patched reactor plus this ADR's live behavioral proof is what §14/§23's precedent already treats as
sufficient for this patch family, matching DH-011D's own compile-only CI companion job). CI static
guards extended (`.github/workflows/ci.yml`'s existing DH-011D/P/P2/J patch-content-guard script)
with a DH-011G2 section; control-tested by deliberately breaking each assertion in a scratch copy
and confirming it failed for the stated reason — one genuine guard bug was found and fixed this way
(a CI-compiles-every-patch check was reading the whole workflow file, which trivially contains its
own check text as a Python string literal, so it could never fail; fixed by scoping the read to only
the compile job's own body).

**Real facility:** never started, stopped, or connected to. Full teardown of every disposable
resource (Docker containers/volumes/networks/images, the throwaway OpenMRS instance, all scratch
files) confirmed and independently re-verified before this commit — see PROJECT_STATE.md/
CURRENT_STATE.md's DH-011G2 entries. **READY TO COMMIT: Yes.**

---

## ADR-033 — Dead-letter retry: operator-authorized, source-preflighted, never a second recovery
## algorithm

**Date:** 2026-08-31 · **Status:** ACCEPTED · **Decided by:** Liban (DH-011E4, closure pass
DH-011E4B)

**Context.** DH-011K found, and DH-011E3 investigated without implementing, a real operational gap:
`RecoveryCoordinator.reconcile` correctly refuses to auto-recover a `dead_letter` row
(`DeadLetterRecoveryBlocked`) — proven fail-closed, live-qualified — but no path existed for an
authorized operator to repair the underlying cause and explicitly allow that same source event to
be attempted again. One permanent bad event would halt that resource/direction's polling stream
forever. DH-011E3's own evidence pass (PROVENANCE.md) characterized every dead-letter creation
path, the persisted schema (including two always-dead `resolved_at`/`resolution_note` columns
since migration `0001_safety_ledger`), and five candidate designs, without implementing any of
them or deciding the two policy questions it could not answer from source alone.

**Decision — DH-011E3's recommended A+B design is approved, exactly as investigated:**

1. **DEAD-LETTER RETRY: allowed only through explicit operator authorization after correction.**
   `LedgerRepository.authorize_dead_letter_retry` (new) is the only production path that can ever
   move a `ProcessedMessage` from `dead_letter` back to `processing`. It is row-locked exactly like
   `mark_completed`/`move_to_dead_letter` already are, refuses unless the current state is
   `dead_letter`, and refuses unless the `DeadLetter` attempt examined by the preflight is still
   the message's current one (guards the race between an unlocked network preflight and this
   locked transaction).

2. **SOURCE EVENT: immutable.** `DeadLetter.payload`, once written, is never updated. A retried
   event that fails again gets a new `DeadLetter` row (`sequence` 2, 3, …) — the previous unique
   constraint on `dead_letters.processed_message_id` (one row per message) is relaxed to a
   composite `(processed_message_id, sequence)` uniqueness (migration `0003_dead_letter_resolutions`).

3. **RETRY PAYLOAD: the original stored payload belonging to the exact `MessageIdentity`.** The
   retry never substitutes today's source content for what was captured at dead-letter time. What
   changes source-side is detected, not incorporated.

4. **SOURCE PREFLIGHT: mandatory before every retry authorization.**
   `openmrs.check_source_supersession` re-reads the original resource by id (a FHIR `read`, never a
   `search` — `?status=` and `?identifier=` are both confirmed-unsupported search parameters on
   this server) and compares its current `MessageIdentity` and `status` against what was stored.
   Returns one of `CURRENT` / `SUPERSEDED` / `AMBIGUOUS` / `UNREACHABLE`. Only `CURRENT` authorizes
   the ledger transition; every other outcome still writes an auditable
   `DeadLetterResolution` row (`resulting_state="refused_*"`) but never moves the ledger.

   **Live-verified, not merely designed** (disposable `dh011e4` OpenMRS-only stack, 2026-08-31): a
   real `DISCONTINUE` (`POST /ws/rest/v1/order`, `action: DISCONTINUE` + `previousOrder`) flips the
   original `ServiceRequest.status` from `active` to `completed` **while `meta.versionId` and
   `meta.lastUpdated` both stay byte-for-byte unchanged** — confirming WATERMARK-DESIGN.md §6's
   2026-08-13 finding still holds on this server. This is the reason the preflight cannot rely on
   version/timestamp comparison alone and must compare `status`. `GET
   .../ServiceRequest?replaces=ServiceRequest/{id}` was tested live and returns HTTP 400
   ("not-supported") — confirmed, not assumed, so the retry preflight has exactly one working
   signal (status/identity re-read), not two. The real, unmodified `check_source_supersession`
   function was run against both a real untouched order (`CURRENT`) and a really-discontinued one
   (`SUPERSEDED`, detail: `"source status changed from 'active' to 'completed'..."`) and classified
   both correctly.

   **Completeness proven, not just observed once (DH-011E4B, 2026-08-31 independent review).**
   Independent review of the first pass found this rested on a single observed DISCONTINUE example
   without ruling out other order actions. Audited the exact pinned `openmrs-core` (2.8.7) and
   `fhir2` (4.2.0) source at those tags: `OrderServiceImpl.saveOrderInternal` routes both
   `Action.REVISE` and `Action.DISCONTINUE` — the only two actions that can supersede an existing
   order — through the identical `stopOrder` method on the previous order, which sets
   `dateStopped` and refuses (`CannotStopInactiveOrderException`) unless that order is currently
   active; there is no code path where either action succeeds without this mutation. fhir2's
   `ServiceRequestTranslatorImpl.determineServiceRequestStatus` derives the original's FHIR
   `status` purely from `dateStopped`/`autoExpireDate`, never from `Action` — so the status check
   is action-agnostic by construction. `Action.RENEW` is not a counter-example: it is never
   referenced in `OrderServiceImpl`'s save/validation logic at all (zero uses outside the enum
   declaration, zero test coverage anywhere in `openmrs-core`'s own suite), so it cannot supersede
   an order in this version regardless of what the preflight checks. `Action.NEW` is not a
   successor action. REVISE was then live-proven on a second disposable OpenMRS-only stack the
   same day: `status` flipped `active` → `completed`, `meta.versionId`/`meta.lastUpdated`
   unchanged — identical to DISCONTINUE — and the real, unmodified `check_source_supersession`
   classified it `SUPERSEDED`. **Status-based detection is therefore complete for every V1
   OpenMRS order action capable of superseding an existing order, not merely sufficient for the
   one case originally tested.** Full source citations: `openmrs.py`'s
   `check_source_supersession` docstring and `tests/test_openmrs.py`'s
   `test_supersession_superseded_on_status_change_with_version_unchanged`.

5. **SUPERSEDED/CANCELED UPSTREAM: automatic retry refused, always.** A dead-letter retry never
   delivers an order the source has already superseded. No automatic mark-completed, no watermark
   advance, no target write. The event stays `dead_letter`; the operator sees a plain, non-PHI
   explanation and manual clinical review is implied, never automated.

6. **SKIP/DISCARD/FORCE-COMPLETE: not supported in Daher V1.** Each would permanently suppress a
   clinical order (or mark one `completed` with no evidence a target write occurred) — squarely
   `AGENTS.md`'s "anything changing what patient data leaves the system" rule. **Human Decision
   Required if ever wanted**; not implemented, and `tests/test_cli.py`'s
   `test_no_destructive_commands_are_registered` inspects the actual argparse command registry
   (not source-tree prose) to guard this, control-tested against a deliberately-registered fake
   `skip` command to prove the guard itself fires.

7. **Operator surface: a local facility-host CLI (`daher_adapter.cli`), not a web endpoint.**
   `adapter/src/daher_adapter/app.py` has no authentication subsystem at all; a new privileged web
   endpoint would be a disproportionate new subsystem for a V1 mechanism. A mandatory `--reason`
   accompanies every retry.

   **Operator attribution redesigned (DH-011E4B, 2026-08-31 independent review).** The first pass
   accepted a typed `--actor` flag as a peer alternative to a host-wrapper-supplied value, both
   equally trusted. Independent review found this indistinguishable in practice from claiming
   attribution the adapter cannot back up. **`retry` now has no `--actor` argument at all.** The
   only identity source it accepts is the pair `DAHER_ADAPTER_ACTOR_NAME`/`DAHER_ADAPTER_ACTOR_UID`,
   both required, set only by the new `scripts/daher_adapter_admin.py` — a host-side wrapper that
   derives them from `pwd.getpwuid(os.getuid())` on the facility host *before* `docker compose
   exec` would otherwise destroy that identity, and forwards them as `-e` flags via
   `subprocess.run` with a list argv (`shell=False` always — a `--reason` containing quotes, `;`,
   a newline, `$(...)`, or backticks reaches the CLI's own `argparse` as one literal string,
   proven directly in `tests/test_daher_adapter_admin.py` against nine hostile inputs). If neither
   env var is present, the CLI refuses with a clear error naming the wrapper; it does not fall back
   to any caller-suppliable value.

   **The precise trust claim, stated once:** operator attribution is derived from the
   authenticated local OS account at the facility-host trust boundary. A host root/Docker
   administrator can ultimately tamper with the system; V1 does not claim tamper-proof attribution
   against a compromised host administrator. It is not cryptographic non-repudiation, and nothing
   in this codebase describes it as such. `DeadLetterResolution` records `actor` (the OS account
   name), `actor_uid` (its numeric UID, more durable than a name that can be recycled), and
   `actor_source` (always `"host_os_wrapper"` in V1 — kept as an explicit column, not a hardcoded
   constant, so the audit trail is self-describing if a second surface is ever added).

**What this does NOT do.** No new recovery algorithm: once the ledger transition commits, the
event is indistinguishable from an ordinary stale `processing` claim, and the completely unmodified
`RecoveryCoordinator.reconcile`/DH-025 probe-before-write machinery (live-qualified by DH-011K,
rewired payload-aware by DH-011E2) handles everything downstream — proven at the unit level for
target FOUND (zero writes, completes), ABSENT (exactly one write, completes), and AMBIGUOUS (zero
writes, stays blocked, watermark stays behind). No new authentication subsystem. **Does not
activate DH-011E** — no scheduler, no recurring task, nothing in `app.py` starts a poll cycle; the
CLI's own job stops at the ledger transition.

**Two items explicitly deferred, not decided here, per DH-011E3's own flag:**

- **Source-version drift policy**: whether a retry of a dead-lettered event should ever be refused
  or redirected when the *source*, not just the *event itself*, has moved to a materially different
  version carrying different clinical content, versus the narrower supersession/cancellation check
  this ADR does implement. Out of scope for this pass; the implemented preflight only detects
  identity drift and the one documented status-mutation shape.
- **Cancellation-while-blocked ordering policy**: whether resolving a dead-lettered original event
  should ever automatically account for a later, still-unclaimed cancellation event queued behind
  it, rather than leaving that queued event to reach the poller naturally once the original resolves.
  Not decided; not implemented.

**Independent review, DH-011E4B (2026-08-31): three acceptance gaps found and closed, none
requiring a redesign of items 1–6 above.** (1) Supersession completeness — closed by the source
audit and REVISE live-proof recorded under item 4. (2) Operator attribution trustworthiness —
closed by the host-OS-wrapper redesign recorded under item 7. (3) The critical PostgreSQL
concurrency/repeat-failure proof was already a permanent gated file
(`test_dh011e4_dead_letter_retry.py`, not a one-shot script); a fourth test
(`test_no_duplicate_active_retry_sequential_reattempt_with_same_stale_id_refused`) was added to it
for completeness and re-run live.

**Test counts, adapter (measured):** 230 unit tests passing (from 181 at DH-011E2; 218 after the
first DH-011E4 pass; 12 more from DH-011E4B's REVISE completeness tests, the wrapper's own tests,
and the CLI's redesigned-identity tests), plus 4 `test_dh011e4_dead_letter_retry.py` live-gated
Postgres tests (real row-level locking concurrency — 20+ concurrent-race trials across two test
shapes, all correct; real repeated-dead-letter lifecycle; real sequential-reattempt refusal) and
two disposable OpenMRS-only live proofs of the source preflight, DISCONTINUE and REVISE (not
permanent gated test files — one-shot manual verifications of real server behavior, recorded here
and in PROVENANCE.md's DH-011E4B section). `pip check`/`ruff check`/`ruff format --check` all
clean, including the new `scripts/` directory.

**Real facility:** never started, stopped, or connected to. All disposable stacks (`dh011e4`
Postgres-only ×2, `dh011e4`/`dh011e4b` OpenMRS-only ×2) were independently torn down and
re-verified empty across all four `docker ps -a`/`images`/`volume ls`/`network ls` filters before
this record was written. **READY TO COMMIT:** see DH-011E4B's own final handoff — not committed in
this pass, per that ticket's own explicit "Do NOT commit or push... Then STOP for review"
instruction.

---

## ADR-034 — Clinician `Add Orders` approved; `Get Concepts` is a separate blocked privilege

**Date:** 2026-08-31 · **Status:** PARTIALLY IMPLEMENTED / BLOCKED · **Decided by:** Liban
(DH-011H1)

**Decision.** Add the existing OpenMRS `Add Orders` privilege, and only that privilege, to the
existing `Daher Clinician Admission` role. Clinicians may place laboratory orders; reception and
lab-tech may not. No real staff names are recorded: the only synthetic requester name approved for
technical work is `Pilot Test Clinician`. If another privilege proves technically required, stop
and return to Liban instead of broadening the role.

**Applied configuration.** `roles-daher.csv` now adds exactly `Add Orders` to the prior nine-item
clinician set. `Daher Reception` remains its exact three-item registration set, no Daher role
inherits another, no lab-tech OpenMRS role is provisioned, and static/unit guards require the sole
Daher-configured `Add Orders` holder to be `Daher Clinician Admission`.

**Required stop condition found live.** On a fresh disposable OpenMRS database, a synthetic
clinician holding exactly that ten-item set authenticated successfully but `POST /ws/rest/v1/order`
was rejected before any order row was written: resolving the referenced Test concept calls the
privilege-protected concept read and returned `Privileges required: Get Concepts`. Reception and a
synthetic zero-role lab-tech were also denied before write (`Get Encounters` was their first missing
payload-resolution privilege). The database contained zero fixture orders after both negatives and
after the clinician attempt.

`Get Concepts` was **not** added. It is a second authorization grant and therefore falls under
Liban's explicit stop rule even though the live evidence shows it is a technical prerequisite of
this REST order shape. The committed-role guard was negative-control tested on a throwaway CSV:
removing `Add Orders` failed the exact-set and sole-holder tests. A clinician-positive order proof
and a runtime isolation proof that reaches the `Add Orders` gate remain blocked until Liban decides
whether `Get Concepts` is approved. No broader built-in role was granted as a workaround.

---

## ADR-035 — `Get Concepts` approved for clinician order resolution; `Get Care Settings` blocked

**Date:** 2026-08-31 · **Status:** PARTIALLY IMPLEMENTED / BLOCKED · **Decided by:** Liban
(DH-011H1 follow-up)

**Decision.** Liban explicitly approved the `Get Concepts` privilege identified by ADR-034's
mandatory stop. Add that read privilege only to `Daher Clinician Admission`, alongside the already
approved `Add Orders`; do not add it to reception or any lab-tech identity. This approval does not
authorize another privilege discovered later in the same REST conversion path.

**Applied configuration.** The self-contained clinician role now has exactly eleven privileges:
ADR-017D's original nine plus `Get Concepts` and `Add Orders`. `Daher Reception` remains its exact
three-item registration role, the synthetic lab-tech has no OpenMRS role, and permanent guards
require both new privileges to be held by the clinician role alone.

**New mandatory stop found live.** A fresh disposable OpenMRS database loaded that exact role and
authenticated the four synthetic callers used by the proof. A real Test Order POST by the
clinician advanced beyond concept resolution, then returned `400`: resolving the explicitly
supplied `careSetting` requires the distinct `Get Care Settings` privilege. A disposable role with
the exact clinician set minus `Add Orders` reached the same earlier conversion gate; reception and
the zero-role lab-tech still stopped at `Get Encounters`. Native `orders` remained exactly zero for
every caller.

`Get Care Settings` was **not** granted or temporarily layered onto the caller. Liban's original
rule applies again: stop and report each additional privilege instead of discovering the eventual
set by progressive authorization expansion. Consequently, clinician-positive order creation and
the runtime control isolating `Add Orders` remain blocked. No order edit/delete/purge or broad
administrative privilege was added.

---

## ADR-036 — `Get Care Settings` approved; `Get Providers` is the fourth privilege stop

**Date:** 2026-08-31 · **Status:** PARTIALLY IMPLEMENTED / BLOCKED · **Decided by:** Liban
(DH-011H2)

**Decision.** Add `Get Care Settings` to `Daher Clinician Admission`, alongside the separately and
chronologically approved `Add Orders` (ADR-034) and `Get Concepts` (ADR-035). Reception remains
unchanged and lab-tech remains unable to originate OpenMRS orders. No fourth privilege is approved;
if one appears, stop immediately.

**Applied configuration.** The self-contained clinician role now has exactly twelve privileges:
ADR-017D's original nine plus the three explicitly approved order-path additions (`Add Orders`,
`Get Concepts`, `Get Care Settings`). Permanent guards require all three additions to be held by
the clinician role alone and absent from reception; lab-tech still has no OpenMRS role grant.

**Fourth privilege found live.** A fresh disposable OpenMRS database loaded that exact set. The
real synthetic Test Order POST passed encounter, concept, and care-setting resolution, then failed
while converting its `orderer` reference: `Privileges required: Get Providers` (`HTTP 400`). This
is a read privilege: OpenMRS resolves the supplied Provider UUID before reaching the order write.
Native `orders` count remained zero.

The removal controls were partial by design at the mandatory stop: removing only `Get Concepts`
failed exactly at `Get Concepts`; removing only `Get Care Settings` failed exactly at
`Get Care Settings`; removing only `Add Orders` could not reach the write check because it and the
fully approved caller both stopped first at unapproved `Get Providers`. Reception and zero-role
lab-tech stopped at `Get Encounters`; every synthetic caller wrote zero native orders.

`Get Providers` was **not** granted, including temporarily. Clinician-positive creation and the
`Add Orders` load-bearing runtime control remain blocked. No practitioner implementation followed
this stop.

---

## ADR-037 — Native OpenMRS Provider UUID → OpenELIS provider identity approved

**Date:** 2026-08-31 · **Status:** ACCEPTED DESIGN / NOT IMPLEMENTED · **Decided by:** Liban
(DH-011H2)

**Decision.** The canonical requester identity is the facility-local pair
`(urn:daher-health:openmrs-provider-uuid, <exact OpenMRS Provider UUID>)`. It is not a national
identifier, name, email, initials, role, demographics, or the OpenELIS-generated Provider UUID.
Names have zero identity authority and fuzzy matching is forbidden.

Implement a Daher-owned native OpenELIS mapping, conceptually `daher_practitioner_identity`, with
a foreign key to `clinlims.provider`, uniqueness on `(identifier_system, identifier_value)`, and a
second uniqueness rule preventing more than one OpenMRS Provider UUID identity for the same native
provider. Exact lookup is `0 = ABSENT`, `1 = FOUND`, `2+ = AMBIGUOUS/fail closed`. First native
person/provider creation and mapping insertion must be one transaction, with no orphan on failure;
replay reuses the mapped provider. Do not overload `provider.external_id` absent source proof that
its semantics match this contract, and do not depend on the secondary FHIR search store.

The Daher delivery composition must reject missing, malformed, unresolved, or ambiguous requester
identity and mapping-store unavailability before any OpenELIS order write, making
`sample_human.provider_id=NULL` unreachable through Daher even though upstream OpenELIS permits it.
Display-name update policy remains **OPEN**: changed display data on replay must not silently
overwrite the native profile without a later approved policy.

**Implementation status.** Not started in DH-011H2 because ADR-036's privilege proof found the
unapproved fourth privilege `Get Providers`, whose ticket instruction required an immediate stop
before the practitioner implementation phase. This ADR records Liban's architecture decision so it
does not need to be re-litigated after the privilege boundary is resolved.

---

## ADR-038 — `Get Providers` approved; pinned source audit finds later order-create privileges

**Date:** 2026-08-31 · **Status:** ACCEPTED / NOT APPLIED / BLOCKED · **Decided by:** Liban
(DH-011H2 continuation 2)

**Decision and chronology.** Liban explicitly approved the fourth chronological order-path
addition, `Get Providers`, after the live stop in ADR-036. The approvals remain separate and in
this order: (1) `Add Orders` (ADR-034), (2) `Get Concepts` (ADR-035), (3)
`Get Care Settings` (ADR-036), and (4) `Get Providers` (this ADR). `Get Providers` is a read
privilege needed to resolve the submitted `orderer` Provider UUID. Reception remains unchanged and
lab-tech remains unable to originate OpenMRS orders.

**Mandatory source-first stop.** This continuation required a complete audit of the exact pinned
OpenMRS CREATE path before changing the role, and prohibited any role modification if source proved
another unapproved privilege. The exact OpenMRS core 2.8.7 and `webservices.rest` 3.5.0 sources
prove that the required `orderType` UUID is resolved through
`OrderService.getOrderTypeByUuid()`, annotated `Get Order Types`. This is the deterministic next
runtime gate, after Provider resolution and before save.

The source audit also proves a later cold-path read: this supported payload omits `orderNumber`, so
`OrderServiceImpl.getOrderNumberGenerator()` reads `order.orderNumberGeneratorBeanId` through
`AdministrationService.getGlobalProperty()` when the static generator cache is empty. The actual
2.8.7 method annotation requires `Get Global Properties`. A warmed cache can conceal that check and
therefore cannot prove a reliable minimum role.

Neither `Get Order Types` nor `Get Global Properties` is approved or granted. In accordance with
the stop rule, the already-approved `Get Providers` is recorded here but was **not yet applied** to
`roles-daher.csv`; the role remains ADR-017D's original nine plus only the three previously applied
additions. No new runtime proof or practitioner implementation was attempted. The complete source
table and exact method chain are recorded in
`docs/DH-011H2-OPENMRS-ORDER-PRIVILEGE-AUDIT.md`.

---

## ADR-039 — `Get Providers` and `Get Order Types` approved; broad configuration/order reads remain blocked

**Date:** 2026-08-31 · **Status:** PARTIALLY IMPLEMENTED / BLOCKED · **Decided by:** Liban
(DH-011H2A)

**Decision and chronology.** Liban approved `Get Providers` after ADR-036's live Provider-resolution
stop and approved `Get Order Types` after ADR-038's pinned-source audit. Both are read privileges
on the exact supported Test Order CREATE conversion path. Apply both, and only those two new
increments, to `Daher Clinician Admission`. `Get Global Properties` is explicitly **not approved**
for that role: its cold-path use is an internal order-number-generator configuration read and its
general grant exposes unrelated configuration. Reception and lab-tech remain unchanged.

The resulting Daher role is ADR-017D's original nine privileges plus the five separately approved
order additions, in chronological order: `Add Orders`, `Get Concepts`, `Get Care Settings`,
`Get Providers`, and `Get Order Types`. Permanent guards require that exact set and ensure all five
additions appear on no other Daher-configured role. No `Get Global Properties`, `Get Orders`, edit,
delete, purge, or administrative privilege was added.

**Upstream baseline makes the requested exclusion technically inseparable today.** Pinned core
2.8.7 `UserContext.hasPrivilege()` automatically considers the built-in `Authenticated` role for
every logged-in user. The fresh Daher-derived database's `Authenticated` role already holds
`Get Global Properties`. Thus the clinician does not receive it from the Daher role but does hold
it effectively, and a direct clinician system-setting GET returned the property's value. Removing
that shared upstream grant would change every authenticated user's authorization and is a new
scope-wide decision; it was not inferred or applied here.

A disposable-only negative control removed that baseline row and restarted. The true cold
clinician order then returned `403` specifically from
`OrderServiceImpl.getOrderNumberGenerator()` →
`AdministrationService.getGlobalProperty()` and wrote zero rows. An administrator warm-up made the
next clinician save commit one native row, proving the check is cache-miss-only, but the response
returned `500`: generic Order representation conversion separately requires the unapproved
`Get Orders` privilege after save. Restart restored the cold 403/zero-row behavior. `Get Orders`
is a broad clinical-data read and was not added or proxied.

**Rejected technical shortcuts.** Client-assigned numbers are unsafe: the permission flag has its
own cold global-property lookup, the native index is non-unique, duplicate and empty numbers
commit, and five concurrent identical requests created five identical rows. Cache prewarming is
also unsafe because restart and supported generator-property notifications reset/reintroduce the
cold path and each process/replica has independent history.

**Architecture decision required.** The preferred candidate preserves server numbering and
standard validation while (1) removing the broad grant from `Authenticated`, (2) proxying
`Get Global Properties` only around the exact internal generator-bean property read with mandatory
`finally` removal, and (3) returning a narrow create result without the generic Order-read
representation. This needs new reviewed code and changes a shared built-in role, so it is not
approved or implemented by this ADR. The endpoint must not expose arbitrary global properties or
orders, must retain the clinician as audit actor, and must not become a generic privileged proxy.

Full evidence and the option table are in
`docs/DH-011H2A-ORDER-NUMBER-BOUNDARY.md`. ADR-037's practitioner mapping remains accepted and
unimplemented; DH-011I remains open and unchanged.

---

## ADR-040 — Narrow Daher TestOrder endpoint approved; shared global-property removal blocks patient search

**Date:** 2026-08-31 · **Status:** PARTIALLY IMPLEMENTED / BLOCKED · **Decided by:** Liban
(DH-011H2B)

**Decision and chronology.** The generic OpenMRS REST `/order` create surface is not the Daher V1
order-entry boundary. Liban approved a Daher-owned endpoint limited to new laboratory
`TestOrder` creation. Its request contains only `encounterUuid`, `conceptUuid`, and
`careSettingUuid`; patient, authenticated-clinician Provider, fixed Test Order type, native order
number, and audit creator are server-derived. The endpoint must retain `Add Orders` as a direct
caller privilege, may proxy only the exact internal read privileges required by those trusted
lookups and the native save, must remove every proxy privilege in `finally`, and must return a
small Daher response rather than generic Order serialization. Clinicians are not to receive direct
`Get Orders` or `Get Global Properties` access. The intended final direct clinician set is
ADR-017D's original nine plus only `Add Orders`.

The uncommitted implementation follows that approved boundary and includes permanent parser,
authorization, Provider-cardinality, encounter-binding, fixed-order-type, minimal-response, proxy
cleanup, exception, and concurrency tests. The pinned Maven reactor passed 32 tests, the role suite
passed 10, the production OpenMRS image built, and two performed negative controls made the
specific role and proxy-cleanup tests fail. These are implementation facts, not adoption approval;
the required disposable existing-flow matrix had not yet passed.

**Mandatory stop found in the fresh disposable matrix.** Startup hardening removed
`Get Global Properties` from the built-in `Authenticated` role. Clinician, reception, lab-tech,
and sys-admin logins all returned `200`. Direct system-setting reads were denied `403 Get Global
Properties` for the three ordinary users and remained `200` for sys-admin, proving the intended
configuration-read boundary. However, the existing Reception patient search
`GET /ws/rest/v1/patient?q=...` also returned `403 Get Global Properties` before returning any
patient result. The pinned runtime stack trace places the protected read inside
`HibernatePatientDAO.findPatients()`; the required registration/search workflow therefore really
depends on the inherited privilege even though it does not itself expose a system-setting API.
No order endpoint was invoked and native `orders` remained at the cold baseline of zero.

Part I explicitly requires a stop if shared-role removal breaks a required non-admin flow that
cannot safely be replaced within this ticket. Re-granting the privilege to Reception or
clinicians would defeat the approved ordinary-user denial. Adding a second custom patient-search
seam, or changing upstream patient-search authorization, is a new cross-workflow security surface
and is not inferred here. The shared-role removal and narrow endpoint are therefore **not adopted
as production-ready** in this pass; all work remains uncommitted.

**Human Decision Required: Yes.** Decide the authorization architecture for required patient
search while ordinary users remain unable to read arbitrary global properties. Candidate directions
include explicitly retaining upstream inherited access, approving a narrowly reviewed search seam,
or approving another bounded upstream/module change; none is selected by this ADR. Until that
decision and a complete fresh cold/warm/restart and regression matrix, DH-011H2B is BLOCKED and
not ready to commit. ADR-037 remains accepted and unimplemented; DH-011I remains unchanged.

---

## ADR-041 — Preserve internal `Get Global Properties`; contain external configuration at HTTP

**Date:** 2026-08-31 · **Status:** ACCEPTED / IMPLEMENTED + LIVE-VERIFIED, UNCOMMITTED ·
**Decided by:** Liban (DH-011H2C)

**Decision.** Retain `Get Global Properties` on OpenMRS's built-in `Authenticated` role. Pinned
OpenMRS 2.8.7 itself needs that internal authorization contract during normal patient search, and
the native order-number generator also reads its selector through `AdministrationService`. Do not
build a second Daher patient-search endpoint for this compatibility issue. Instead, prevent
ordinary users from retrieving configuration through externally reachable HTTP resources. The
new `Daher View System Configuration` privilege is the external boundary and is granted directly
only to the existing `Organizational: System Administrator` role.

This is not approval for clinicians, reception, or lab-tech to browse configuration. Internal Java
authorization compatibility and externally reachable configuration visibility are distinct
boundaries. A post-authentication `MappedInterceptor` now covers every pinned configuration
resource found by source audit: `systemsetting` collection/query/single-resource/all
representations, locale/theme configuration, visit configuration, name/address templates, system
information, and the legacy webservices REST settings form. Ordinary authenticated requests
receive fixed `403` JSON before upstream content is materialized; sys-admin receives normal
upstream behavior. Internal service calls are unchanged.

**Role lifecycle.** The privilege is module-declared and an Initializer loader runs after the roles
domain. The first restart proof was correctly rejected: upstream wildcard roles absorbed the new
privilege on second boot, raising direct holders from one to three. The loader now removes this
one new privilege from every non-allowlisted direct role before idempotently granting sys-admin.
Two corrected consecutive boots each converged to exactly one direct holder, while Authenticated
retained `Get Global Properties`. No other privilege was needed or granted.

**Narrow order boundary.** Continue with the approved Daher new-TestOrder endpoint rather than
generic REST `/order`. Its request is exactly `encounterUuid`, `conceptUuid`, and
`careSettingUuid`; no approved deterministic care-setting rule exists, so that field remains
caller-supplied. The server derives patient, the authenticated user's sole active Provider, fixed
Test Order type, native number, action, and creator. The caller must directly own `Add Orders`.
Only `Get Providers`, `Get Concepts`, `Get Care Settings`, and `Get Order Types` are temporarily
proxied around individual trusted reads and removed in `finally`. `Get Orders` is unnecessary
because the endpoint returns a minimal Daher response. The clinician role therefore returns to
ADR-017D's original nine direct privileges plus only `Add Orders`.

**Evidence.** Disposable proof passed configuration containment, clinician/reception patient
search, reception registration, maternity Visit+Encounter creation, cold/warm/post-restart and
generator-change/reset orders, native TestOrder/type/patient/orderer/number/creator checks,
injection rejection, generic-create denial, reception/lab-tech/missing-Add-Orders/Provider
cardinality denial, concurrent clinician identity isolation, database-outage rollback/recovery,
and the existing end-admission endpoint. Full non-secret audit and evidence are in
`docs/DH-011H2C-EXTERNAL-CONFIGURATION-BOUNDARY.md`.

ADR-037 remains accepted and unimplemented; this decision does not implement OpenELIS
practitioner mapping. DH-011I remains open with zero exact one-to-one LOINC mappings, DH-011L did
not run, and no poller/scheduler or real-facility flow was activated.

## ADR-042 — Product pivot: DHealth Lab Exchange MVP; Option C hybrid data boundary; central
Exchange may hold minimum transaction-scoped clinical order/result data

**Date:** 2026-09-08 · **Status:** ACCEPTED (product direction) / ACCEPTED (architecture), NOT YET
IMPLEMENTED beyond DH-EX-001's schema, DH-EX-002's organization/authentication/tenant-isolation
boundary, DH-EX-003's lab directory/catalog/offering/pricing/whole-order-eligibility backend,
DH-EX-004's DHealth-native Provider lab-order creation/read API, DH-EX-005's secure patient
order access + atomic patient lab selection, and DH-EX-006's selected-lab assigned-order workflow
(`SELECTED`→`ACCEPTED`/`REJECTED`, `ACCEPTED`→`IN_PROGRESS`) (see PROJECT_STATE.md's DH-EX-002
through DH-EX-006 FINAL CLOSURE entries, 2026-09-09/10) — every result/follow-up-facing workflow
beyond lab acceptance and
start-of-processing remains unimplemented ·
**Decided by:** Liban (DH-PIVOT-001 audit, DH-EX-001 ticket)

**Decision.** DHealth's MVP is no longer primarily a maternity/birth-centre information system
with an attached laboratory. It is a digital lab order + result exchange connecting doctors/
clinics/hospitals, patients, and independent laboratories — reachable either through DHealth's own
Provider/Lab portals or through an existing EMR/LIS via a connector. OpenMRS and OpenELIS become
the first two *reference connectors*, not the definition of the product. The prior maternity-first
architecture recorded elsewhere in this file (ADR-001 through ADR-041) was correct and valid work
for the scope that existed when each was written; none of it is retracted, deleted, or judged
wrong in hindsight. This entry records a change of product direction, not a correction of prior
engineering.

**Recommended and approved architecture: Option C, hybrid.** A central DHealth Exchange core
(organization identity, routing, order/result status machine, correlation, idempotency, audit,
dead-letter — connector-agnostic) sits beneath two kinds of participant backend: DHealth-native
Portal backends (which *do* persist transaction-scoped clinical payloads, because for a
portal-only organization DHealth **is** the endpoint system — there is nowhere else for an order's
test selection or a lab's result to live) and Connector adapters (OpenMRS/OpenELIS today, which
keep clinical payloads in the endpoint system and give the Exchange only routing metadata and a
correlation identifier, exactly as the existing adapter already does). Pure "central exchange
stores only routing metadata" (Option B) was evaluated and rejected as unachievable given the
Provider/Lab portal requirement; pure "central exchange stores full normalized clinical records"
(Option A) was rejected as an unbounded EMR risk. ADR-020/ADR-021's "never a shared multi-tenant
clinical database" rule is unaffected by this decision — it is scoped to per-facility *clinical*
stores (ADR-001), not to a routing/audit-scoped Exchange core, and remains in force for any
future connector-backed clinical stack.

**Central data boundary, explicit.** The Exchange core is permitted to persist: organization
identity, patient/practitioner references (source-system + source-identity only, never a national
ID, never fuzzy-matched), the requested test catalog entries on an order, sender and selected-lab
organization identity, a frozen price snapshot at selection time, order status, the lab result
value, result-delivery state, and the doctor's follow-up disposition. The Exchange core is **not**
a longitudinal medical record: no free-text clinical notes, no medication list, no diagnosis
history, no general encounter data, and no field is added "because it might be useful" beyond what
the golden journey requires (DH-EX-001's data-minimization rule).

**Patient result-release policy for MVP.** The ordering doctor receives the full lab result. The
patient does not automatically receive detailed numeric/clinical values — only order status, which
lab was selected, that a result is ready, that the doctor has reviewed it, and the doctor's
NO_FOLLOW_UP_REQUIRED / FOLLOW_UP_REQUIRED disposition. Detailed patient-facing result content
remains explicitly deferred pending a separate clinical-policy decision; DH-EX-001 does not
implement it.

**WhatsApp/SMS patient notification is now more critical to the MVP, not less.** Under the prior
maternity-first scope it was a secondary notification channel (pinned P0 #2: stays `BLOCKED`, not
`DONE`, until real credentials run the flow — ADR-003, unchanged and still binding). Under the
Exchange MVP it is also the mechanism that delivers the patient's lab-selection link and the
result-ready/follow-up notice, i.e. core golden-path infrastructure. This does not relax ADR-003's
real-credential gate; it raises the priority of clearing it.

**What leaves the MVP critical path, not the codebase.** Deeper maternity workflows, bed
management/seeding (ADR-007), the partograph (ADR-006), prenatal-panel expansion, and
maternity-specific SSO activation (BLOCKER-5) are deprioritized from the *new* MVP's critical path.
None of this code is deleted, and none of the underlying decisions (ADR-006, ADR-007) are
superseded — a maternity/OpenMRS-connector pilot remains buildable later on the existing
foundation. This is recorded here because AGENTS.md reserves cutting a pinned P0 item as an
explicit human decision: pinned P0 #3 (bed seeding additive-only) and #4 (partograph data-entry
only) leave the critical path by this same decision; pinned P0 #1 (backup + performed restore
drill) and #2 (WhatsApp real credentials) remain pinned and, per the paragraph above, #2 is now
more load-bearing than before.

**Connector-track work preserved, not discarded.** DH-011H (OpenMRS TestOrder idempotency/
privilege-boundary chain, ADR-034 through ADR-041), DH-011I (LOINC/test-mapping inventory, zero
exact matches), and ADR-037 (OpenMRS Provider UUID → OpenELIS practitioner mapping, accepted,
unimplemented) all remain valid, are not blocked, and are not blocking. They become
OpenMRS/OpenELIS-connector-track work: needed only if/when a real OpenMRS- or OpenELIS-based
participant joins a pilot, not for the first Exchange demo. The specific lesson from DH-011H (no
idempotency-key mechanism exists on the native OpenMRS TestOrder-creation path) is the direct
motivation for `LabOrder.idempotency_key` in DH-EX-001's schema — the generic fix now exists at
the Exchange boundary and should be wired to whichever concrete order-creation surface a pilot
actually uses, including the OpenMRS connector if it is ever activated.

**Canonical internal model.** The Exchange core's own domain/database model (DH-EX-001) is
DHealth-owned, not FHIR resources — FHIR remains a connector/interoperability format where a
connector needs it (as the adapter already uses it for OpenMRS/OpenELIS), not the internal
representation of Organization/LabOrder/LabSelection/LabResult and the rest of the Exchange
domain. Forcing marketplace, lab-selection, and price concepts into upstream FHIR resource shapes
was evaluated and rejected as unnecessary complexity for a domain FHIR was never designed to
represent.

**Correction, append-only.** PROJECT_STATE.md's DH-011H2C entry states "no commit or push was
performed." That is stale: `git show --stat 4dfaeef26f624e09a1869fddd1565b4106d4612e` confirms
`CreateTestOrderServlet.java`, `DaherExternalConfigurationInterceptor.java`,
`DaherExternalConfigurationPrivilegeLoader.java`, and `DaherExternalConfigurationPrivilegeProvisioner
.java` were in fact committed and pushed to `origin/master` as part of that commit ("uncomplete but
pushing", 2026-09-01). The work itself is unaffected; only the "not committed" claim was wrong.

**Full audit and evidence:** DH-PIVOT-001 architecture audit (delivered in-session, 2026-09-08) —
component reuse table, gap analysis, and proposed ticket sequence DH-EX-001 through DH-EX-010.
DH-EX-001 (this same date) implements the Exchange core domain/persistence foundation described
above; see its own entry in PROJECT_STATE.md for build/test status.

---

## ADR-043 — DHealth MVP 1.0: interoperable diagnostic network with native applications and
connector support

**Date:** 2026-09-12 · **Status:** ACCEPTED (product direction), NOT YET IMPLEMENTED beyond what
ADR-042 and DH-EX-001 through DH-EX-007 already built · **Decided by:** Liban (DH-MVP-001 ticket)

**Relationship to ADR-042.** ADR-042 (2026-09-08) is not superseded or retracted. It made the
load-bearing architecture call — Option C hybrid data boundary, a connector-agnostic Exchange core,
OpenMRS/OpenELIS as the first reference connector track — and that call stands unchanged. This
entry extends it on exactly the points that were genuinely still open or under-specified when
ADR-042 was written: what DHealth's product *family* is named and shaped, what the MVP explicitly
does and does not require on day one, and four product laws that constrain every future
implementation ticket. Everything ADR-042 already decided (the data boundary, the patient
result-release policy, the WhatsApp/SMS priority note, the "connector-track work preserved, not
discarded" statement) is incorporated by reference, not restated or altered here.

**Full elaboration:** [`docs/MVP-1.0.md`](docs/MVP-1.0.md). This ADR entry is the decision
record; that document is the long-form specification meant to be read on its own by an engineer,
a future coding agent, a clinical stakeholder, or a pilot facility without requiring them to
reconstruct this history first. Where the two differ in emphasis, this entry is authoritative on
the decision; the document is authoritative on the detail.

### The decision

**DHealth MVP 1.0 is an interoperable diagnostic network, not a single application.** It consists
of five product surfaces: **DHealth Exchange** (the shared network backbone — organization
boundaries, orders, assignments, result exchange, audit, workflow state; not a longitudinal
national EMR), **DHealth Clinic** (native provider software for facilities without an adequate
existing system), **DHealth Lab** (native laboratory software for labs without an adequate LIS),
**DHealth Patient** (the patient interaction layer, designed assisted-digital from day one), and
**DHealth Connect** (the integration layer for organizations that already run an EMR/LIS). Full
scope and boundaries for each surface are in `docs/MVP-1.0.md` §3.

**Naming status.** DHealth Clinic, DHealth Lab, DHealth Patient, and DHealth Connect are product
names introduced by this ADR. None of the four exists today as a named, separately-built
application. What exists is the DHealth Exchange backend (DH-EX-001 through DH-EX-007) — a shared
API surface that already implements most of the *backend* behavior DHealth Clinic's and DHealth
Lab's MVP purpose require (order creation/read, work queue, accept/reject/start-processing, result
submission, completion, review), reached today only by direct API call, with no dedicated frontend,
no branded product, and no OpenMRS/OpenELIS-independent UI anywhere in this repository. OpenMRS and
OpenELIS (ADR-042) remain the first reference *connector* track under DHealth Connect's model, not
an instance of DHealth Clinic/Lab.

**The MVP golden journey** (provider identifies/registers patient → clinician creates lab order →
lab is assigned/selected → lab receives and accepts the order → sample/specimen workflow →
processing → result entered → result validated/released → ordering provider receives result →
clinician reviews → patient receives an appropriate status/follow-up communication) is the
product's definition of success, not a feature count. Full diagram: `docs/MVP-1.0.md` §4.

### Four non-negotiable product laws

These bind every future DHealth implementation ticket the way AGENTS.md's clinical-content rules
already do. A ticket that would violate one of these needs a superseding ADR, not a quiet
exception.

1. **No forced marketplace.** Day-one DHealth is a connected diagnostic workflow, not a
   price-shopping product. Price comparison, cheapest-price lab ranking, and broader
   marketplace/discovery are not required patient-facing features for launch. Doctor/facility-
   assigned lab workflows are valid; patient lab choice may still exist where appropriate.
2. **No double entry.** A DHealth production workflow must not require staff to manually maintain
   the same clinical transaction in DHealth and another production clinical system. Native model:
   the facility has no adequate system, so DHealth Clinic/Lab is the authoritative workflow system.
   Integrated model: the facility has an existing EMR/LIS, so DHealth Connect keeps staff in that
   system as much as practicable. A transitional pilot exception — DHealth handling a clearly
   isolated workflow the incumbent system does not already own, while integration is not yet built
   — is permitted, but must never be documented as the intended production architecture.
3. **Faster than what it replaces.** DHealth cannot win by merely being digital. High-frequency
   tasks must be extremely low-friction. Initial design *targets* (not proven performance): a
   common provider lab order in approximately under 20 seconds, common lab order acceptance in
   approximately under 10 seconds, minimal navigation for routine actions, remembered defaults,
   order sets/favorites, no duplicate demographic requests, and no interoperability jargon in
   normal clinical UX. These are targets to validate with real users, not claims about current
   software.
4. **No digital-literacy requirement for care.** A patient must never need a smartphone, app
   installation, WhatsApp navigation skill, strong literacy, or familiarity with digital interfaces
   to complete a DHealth-supported care journey. DHealth Patient is designed assisted-digital:
   simple mobile web, large obvious actions, minimal text, print, QR/order reference, clinic/lab
   reception assistance, SMS, WhatsApp, and — in a later validated phase — audio/voice, are all
   different access methods to **one** canonical order (`docs/MVP-1.0.md` §7's "one order, many
   doors" invariant), never separate clinical records. Human-assisted workflow (a receptionist
   printing an order, a lab tech scanning it, staff verifying identity) is a designed feature, not
   a failure mode to engineer away.

### Existing-contract conflict recorded, not resolved

**DH-EX-005's patient lab-selection contract requires the patient's exact current-price consent
(`expected_price_ids`) and freezes the selected price at selection time** — confirmed present in
`adapter/src/daher_adapter/exchange/selection.py` and `api.py`'s `PatientSelectionRequest`. Law 1
("no forced marketplace") must not be read as silently reinterpreting that existing, implemented,
tested API as "price-free selection." It is not reinterpreted here. **If MVP 1.0 launches with
patient-selected labs but without displaying/consenting to price, DH-EX-005's selection contract
must be deliberately redesigned in a later ticket** — that redesign is not performed by this ADR
and its shape is not decided here. Until that decision is made, the acceptable launch options are
(a) a provider/facility-assigned lab workflow that never routes through patient price-selection at
all, or (b) a patient-selection flow that continues to honestly satisfy the existing price-consent
contract as built. This is recorded as **OPEN DECISION**, not resolved.

### Other semantics explicitly left open by this ADR

- **`ResultDelivery.status` meaning.** The model (`PENDING`/`DELIVERED`/`FAILED`) exists and no code
  path advances it past `PENDING` today. Candidate meanings for `DELIVERED` — available to
  provider, sent to connector, received by connector, presented to a user, acknowledged by a user —
  are named in `docs/MVP-1.0.md` §10 but **none is chosen**. A future ticket must decide before any
  delivery mechanism is implemented.
- **Result validation/release.** DH-EX-007 implements `RESULT ENTERED` → automatic `COMPLETED`
  (every ordered item has a result). DHealth Lab's MVP direction distinguishes `RESULT ENTERED`
  from a separate `RESULT VALIDATED / RELEASED` step. **No such separate authorization step exists
  in the current implementation** — recorded as a product gap, not silently assumed to exist. Exact
  roles and semantics must come from real laboratory workflow input, not be invented here.
  **[Forward pointer, 2026-09-13, DH-FIELD-001 — this bullet is no longer current, kept verbatim for
  history rather than rewritten:** this gap was closed by DH-EX-012/ADR-044 (`LAB_RESULT_RELEASER`
  role, explicit `release-results` step). See ADR-044 above and ADR-046 below for what remains
  open.]
- **Patient result-communication content policy** for SMS/WhatsApp/lock-screen notifications/voice
  calls/shared family phones. ADR-042 already decided the patient does not automatically receive
  detailed numeric results; this ADR does not add or relax that boundary, and does not decide the
  sensitive-content policy for each channel — that is a separate future privacy/product decision.
- **Proxy/family authorization.** Not defined here. A future family/helper access workflow requires
  its own explicit security/privacy decision before any code exists for it.
- **Patient identity/matching policy for Djibouti**, beyond what ADR-042 and DH-EX-004 already
  established (source-system + source-identity reference only, never a national identifier, never
  fuzzy-matched into an automatic merge). Search may tolerate spelling/transliteration variance as
  a *discovery* aid; it must never silently merge or auto-identify a patient. The full national-
  identifier and matching-workflow question remains undecided.

### What leaves the MVP critical path, again — not the codebase

Consistent with ADR-042's own framing: nothing already built is deleted or judged wrong for having
been built. The existing Exchange pricing/marketplace implementation (DH-EX-003) is not removed —
it is implemented infrastructure whose *patient-facing* use is not required for launch and may
matter again in a later product phase. The maternity/OpenMRS/OpenELIS stack (ADR-001 through
ADR-041) remains a preserved clinical-stack implementation, reference connector infrastructure, and
reusable deployment/security/backup/interoperability evidence — it is no longer the definition of
the DHealth MVP, and historical documentation describing it as the product stays historical
evidence, not something rewritten to look as though the current vision always existed.

**Full audit, gap analysis, product-family detail, golden-journey diagram, pilot shape, success
metrics, and the complete out-of-scope/known-gaps/follow-up lists:** `docs/MVP-1.0.md`. Ticket and
follow-up-backlog record: `PROJECT_STATE.md`'s DH-MVP-001 entry.

### Dated clarification (2026-09-18, DH-LAB-001) — Native DHealth and DHealth Connect are parallel supported capabilities, not a fork to choose between

**Status:** clarification, not a revision — this ADR's original five-surface product definition
above (DHealth Exchange/Clinic/Lab/Patient/Connect) already named both the native and connector
paths from the start and is not changed by this entry. It is recorded because forward-looking
sections of `STATUS.md`/`PROJECT_STATE.md` had drifted toward framing that read as "decide the
delivery topology, then build toward it" — a fork this ADR never actually intended. DH-LAB-001's
own ticket text caught the drift before any code was affected and required this correction as part
of its own scope (§0).

1. **Native DHealth Lab and DHealth Connect are not mutually exclusive product directions.**
   Neither supersedes or is a fallback for the other. Both are permanent, supported capabilities of
   the same DHealth Exchange backbone.
2. **A complete native DHealth environment — Clinic and Lab together, exercising the full golden
   journey — is a product requirement in its own right**, not merely a reference implementation or
   a demo scaffold to be discarded once a connector exists. It is what makes DHealth usable by a
   facility or lab with no adequate existing system at all (Law 2, "no double entry" — the native
   side of that law's two branches).
3. **The native apps are a usable deployment product.** A facility whose systems are inadequate
   uses DHealth Clinic/Lab directly, in production, indefinitely if that remains the right fit for
   them — not only until a connector is available.
4. **DHealth Connect exists for facilities that already run an adequate EMR/LIS**, so staff keep
   working in the system they already know rather than maintaining the same transaction twice (Law
   2's other branch).
5. **Hybrid deployment is first-class, not a compromise.** A single pilot may run, for example, a
   native DHealth Lab against a provider connected through DHealth Connect, or the reverse. Nothing
   in this ADR requires a facility and its counterpart lab to be on the same delivery model.
6. **Which model a given real facility uses is decided per facility, later, once one exists** — that
   is DH-PILOT-TOPOLOGY-001's question, and only that question: "which deployment model fits this
   specific customer," never "which product path DHealth permanently supports." Answering it does
   not retroactively make the other path unsupported.
7. **The native-demo roadmap must not be blocked on pilot-facility integration-API availability.**
   Building out native DHealth Clinic/Lab into a fully demoable, synthetic-data golden journey
   (DH-LAB-001 through DH-DEMO-001 and DH-DEMO-E2E-001 in `PROJECT_STATE.md`'s forward roadmap)
   proceeds independently of whether or when any specific pilot facility's existing system exposes
   an integration API for DHealth Connect to speak to.

**Implementation record:** `PROJECT_STATE.md`'s DH-LAB-001 entry (the first DHealth Lab frontend
surface, built against the existing Exchange lab-workflow backend) and its corrected forward
roadmap, which now runs DH-LAB-001 → DH-LAB-002 → DH-LAB-003 → DH-LAB-004 → DH-LAB-005 →
DH-DEMO-001 → DH-SEC-001 → DH-OPS-001 → DH-RECOVERY-001 → DH-DEMO-E2E-001 →
DH-CONNECT-CONTRACT-001 → DH-PILOT-TOPOLOGY-001, with DH-PILOT-TOPOLOGY-001 moved to the end,
gated on a real target facility existing, not on native build order.

### Dated note (2026-09-18, DH-DEMO-001) — a native demo environment now exists, deployment topology only

Not a new decision, and not a revision of anything above — point 2's "complete native DHealth
environment ... is a product requirement in its own right" now has a concrete implementation:
`docker-compose.native-demo.yml` (`extends:` the real `docker-compose.yml` service definitions —
the demo runs the real code, never a parallel mock) boots the native Clinic → Exchange → Lab loop
without waiting on OpenMRS/OpenELIS, which is preserved untouched. This is a deployment-topology
choice, not a product-boundary one, so it does not warrant its own ADR — recorded here only so a
future reader does not need to infer that this repo now has two independent Compose projects and
why. Full detail: `docs/ARCHITECTURE.md` §18, `PROJECT_STATE.md`'s DH-DEMO-001 entry.

---

### 2026-09-20 clarification (DH-PATIENT-001) — appended to ADR-043 (Laws 1 and 4, §7's assisted-digital strategy) and ADR-048 (versioned review), deciding nothing new

**No new ADR is created for DH-PATIENT-001, deliberately.** The patient-access security model was
already decided (DH-EX-005: one order-scoped opaque 256-bit grant, only its SHA-256 digest
persisted, no patient `ExchangeIdentity`, no OIDC, generic 401 on every failure mode); the
versioned-review model was already decided (ADR-048/DH-EX-014A/014B: one `DoctorReview` per released
version, never reset, never reassigned); and the product laws this surface exists to satisfy were
already decided (ADR-043 Law 1 "no forced marketplace", Law 4 "no digital-literacy requirement for
care"). This ticket **implements** those and records the consequences below. Nothing here supersedes
or reinterprets any of them.

**1. Law 4 now has an implementation, not only a constraint.** A patient reaches their order status
through simple mobile web, entered by QR code or link, with a printable clinician-produced sheet as
the non-smartphone path. No app install, no account, no password, no Keycloak, no technical
identifier, and no reading of a technical status name is required. `docs/MVP-1.0.md` §7's previous
"`NOT BUILT`: none of these channels exist yet" line is corrected in place there.

**2. Law 1 is satisfied by absence, not by configuration.** The launch patient surface has no
marketplace: `PatientAccessClient` exposes one method, and DH-EX-005's `/patient/order/lab-options`
and `/patient/order/selection` endpoints — which remain served and remain tested — are unreachable
from it. The ADR's own recorded "acceptable launch options" (a) provider/facility-assigned lab
workflow is what this surface assumes; option (b)'s price-consent flow is neither used nor removed,
and the **OPEN DECISION** recorded above about redesigning DH-EX-005's price-consent contract is
untouched and still open.

**3. The patient sees status, never content — and that boundary is now enforced at the response,
not only in the UI.** `PatientOrderResponse` gains exactly one field, `result_status`, and still
serializes no numeric value, text value, reference range, source code, observation, release
manifest, or reviewer identity. This is asserted against the **serialized JSON string** rather than
a field list, so a carelessly nested future addition fails the regression. This preserves ADR-043
§13's "records and routes, does not interpret" boundary: `NO_FOLLOW_UP_REQUIRED` is rendered as "no
further follow-up was indicated for this order", never as "everything is normal" — a clinician's
workflow disposition is not a clinical statement about the result, and conflating the two would be
interpretation.

**4. The one genuinely load-bearing consequence of ADR-048's model for a patient-facing surface.**
`result_status` resolves from the order's **latest released version** and that version's own
`DoctorReview` — never from "the most recent terminal review". ADR-048 already requires that an
amendment release create a fresh `PENDING` review bound to the new version and never mutate the
prior version's terminal row; read together with a patient-facing projection, that means a
version-2 release must **revert** the patient's view to awaiting-review. Showing version 1's
disposition after version 2 exists would present a clinician's judgement about superseded content as
though it applied to the current result. An amendment merely *in preparation* has released no
version and correctly changes nothing the patient sees.

**5. A grandfathered order is still never given a fabricated release.** ADR-044's grandfather rule
holds unchanged: a `COMPLETED`-without-`LabResultRelease` order has no release version, none is
invented for it, and its one honest unbound `DoctorReview` row is used verbatim.

**6. `ResultDelivery` remains undecided and untouched.** A patient viewing a status page does **not**
mean `DELIVERED`. The candidate meanings named in `docs/MVP-1.0.md` §10 are still unchosen, and this
ticket deliberately declines to choose one by implication — which is exactly what silently advancing
that state on a page view would have done.

**7. Patient read access remains unaudited, by the same reasoning that refused a fake
`ExchangeIdentity`.** Issuance and revocation are audited with real identity actors. A patient read
is not, because `ExchangeSecurityAuditEvent.actor_identity_id` is a real foreign key into
`exchange_identities` and a patient has none — populating it would record a lie in an authoritative
security table. Adding patient-read auditing requires an actor model designed for it; this ticket
documents the gap rather than inventing one.

**8. The raw bearer secret remains unrecoverable after issuance, and the UX now depends on that
rather than working around it.** No endpoint returns an existing grant's secret. The new metadata
read (`GET .../orders/{order_id}/patient-access`) reports `{active, expires_at, access_mode}` only —
never the token, its digest, or the grant id — and an expired grant reports inactive with a null
expiry. A clinician who no longer holds the link must revoke and re-issue; the Clinic UI says
exactly that instead of implying the QR can be rebuilt, and every ambiguous mutation re-reads
authoritative metadata rather than retrying blindly.

**9. Transport is still `NOT BUILT`, on purpose.** No WhatsApp, SMS, email, or voice channel was
added. ADR-043's own open per-channel sensitive-content policy is therefore still open and still
unanswered by this ticket; a future transport can carry this same access path rather than needing a
second one. Any such transport remains a "what patient data leaves the system" change, i.e. a human
decision under `AGENTS.md`, not an agent's call.

No schema change was needed for this clarification or its implementation — Alembic head stays
`0025_native_result_binding`. See `docs/ARCHITECTURE.md` §19 and `PROJECT_STATE.md`'s
DH-PATIENT-001 entry for full evidence, including the 18 negative controls actually executed.

---

## ADR-044 — OPEN: the result-entry → release contract (who may release, and whether the same
human may enter and release)

**Date raised:** 2026-09-13 · **Status:** OPEN — blocks the next result-workflow implementation
ticket · **Human Decision Required:** Yes (the two questions below specifically)

**Relationship to ADR-042/ADR-043.** Not a new architecture decision. ADR-043 already recorded, and
this entry does not reopen, that "result validation/release" is an unresolved product gap distinct
from result entry, and that "exact roles and semantics must come from real laboratory workflow
input, not be invented here." DH-EX-012D (`PROJECT_STATE.md`) is the full audit and decision packet
this ADR summarizes; that entry is authoritative on evidence and reasoning, this entry is the
durable pointer and the explicit question set.

**Recommended by DH-EX-012D's audit — pending Liban's confirmation, not yet accepted** (see
DH-EX-012D for the full reasoning behind each): release should be named `RELEASED`, never
"validated" (`docs/MVP-1.0.md` §13 already forbids DHealth claiming to validate/interpret); release
should be order-level, matching `ResultDelivery`, the provider result API, and `DoctorReview`'s
existing order-level shape; no new `LabOrderStatus` is needed — readiness is exactly the
`missing_count == 0` aggregate `results.py` already computes; a separate immutable
`LabResultRelease` row (mirroring DH-EX-011's `LabSampleCollection`) fits the existing model
cleanly; a pre-DH-EX-012 `COMPLETED` order should be grandfathered with no fabricated release row,
exactly matching DH-EX-011's own no-retroactive-fabrication rule; `LabResult.completed_at` should
keep its name (an API-contract field on two response models already) with its "entered," not
"released," meaning documented precisely. None of these six is binding until Liban says so —
recorded here as the audit's recommendation, not as a decision this ADR makes on its own.

**Question 1 — who may release a lab order's results?** Options on record (full operational
tradeoffs in DH-EX-012D): (A) the existing `LAB_TECH` role may enter and release; (B) a new
dedicated release role (e.g. `LAB_RESULT_RELEASER`), with the same human permitted to hold both
roles where a facility allows it; (C) `ORG_ADMIN` releases — evaluated and cautioned against absent
a stated operational reason; (D) facility-configurable policy — evaluated as likely premature before
any real pilot has run.

**Question 2 — may the same human who entered a result also release it?** (A) always permitted; (B)
strict second-person release; (C) facility policy determines it. **Why this is not a coin flip:** a
strict second-person rule in a small lab with one `LAB_TECH` on shift means results already entered
sit unreleased until a second qualified person is present — a real patient-care-timeliness cost a
software preference cannot weigh on its own.

**Flagged risk, not a question requiring a vote:** V1 has no result amendment/correction path
(`DuplicateResult` — one immutable result per item). Adding release does not create this gap, but it
sharpens it: today "entered wrong" and "visible wrong" are the same instant; after release they
become separable, which is the point of adding it, but there is still no way to correct a result
already found wrong. Recorded here so it is not rediscovered as a surprise later.

**Not decided here, not implementation-blocking on their own:** the exact `LabResultRelease` schema
and the `lab_result_released` audit event are evaluated, not built — implementation follows once
Questions 1 and 2 are answered, per DH-EX-012D's own "decision/audit ticket only" scope.

**Full audit, evidence, and reasoning:** `PROJECT_STATE.md`'s DH-EX-012D entry.

---

**Decision accepted by Liban — 2026-09-13.** Both questions above are resolved. This section is an
append, not a revision — the OPEN packet above (questions, options, flagged risk) stands unchanged
as the record of what was evaluated and why; this is the record of what was chosen.

**Question 1 answered: Option B** — a new, dedicated `LAB_RESULT_RELEASER` organization role
(LAB-only, additive alongside `LAB_TECH`, never replacing it). `ORG_ADMIN` and platform-admin do not
gain release authority by virtue of that role alone — release requires the explicit
`LAB_RESULT_RELEASER` grant, checked independently of every other role.

**Question 2 answered: Option A** — always permitted. The same human may hold both `LAB_TECH` and
`LAB_RESULT_RELEASER` simultaneously and use both on the same order. No second-person requirement,
no `released_by != entered_by` check, no facility-policy toggle exists anywhere in the
implementation — a single qualified person may enter and release without waiting on anyone else.

**Implemented exactly as DH-EX-012D's audit recommended, now binding, not merely recommended:**

- Release terminology is `RELEASED`, never "validated" — no clinical interpretation is added or
  implied anywhere in the release path.
- Release is order-level, matching `ResultDelivery`, the provider result API, and `DoctorReview`.
- No new `LabOrderStatus` was added — readiness is the existing `missing_count == 0` aggregate,
  exposed as `ready_for_release` on result submission.
- One immutable, order-level `LabResultRelease` record (id, `lab_order_id` UNIQUE,
  `lab_organization_id`, `released_by_identity_id`, `released_at` — server time only): no revision,
  no un-release, no delete.
- A pre-DH-EX-012 order already `COMPLETED` is grandfathered with no fabricated release row —
  `LegacyCompletedOrderHasNoRelease` is raised instead, distinguishable from `ResultsNotReadyForRelease`.
  Provider visibility and clinician review for such an order are unaffected, exactly as recommended.
- `LabResult.completed_at` keeps its name and its "entered," not "released," meaning.
- The flagged amendment/correction gap remains open and unsolved by this ticket, recorded as a P0
  pre-real-patient-pilot follow-up in `PROJECT_STATE.md`'s DH-EX-012 entry — accepting this decision
  does not resolve it.

**Implementation record:** `PROJECT_STATE.md`'s DH-EX-012 entry (migration
`0015_exchange_result_release`, `exchange/results.py`'s `LabResultReleaseRepository`, the
`require_lab_result_releaser` authorization check, the release and lab-side inspection endpoints,
real-PostgreSQL qualification, and full test evidence).

**Status: this ADR is now RESOLVED**, not OPEN — recorded here rather than editing the header above,
so the original question/evidence packet remains legible as written at the time it was raised.

### Addendum (2026-09-18, DH-LAB-004): the release-inspection fingerprint closes an inspect-then-stale-release race

This ADR's release mechanism (`LabResultReleaseRepository.release`) is unchanged in shape — same
lock order, same `LabOrder FOR UPDATE`, same immutable `LabResultRelease` row. DH-LAB-004 adds one
new precondition inside that same lock, closing a race this ADR's original design left open: a
releaser inspects a result set, a technician then corrects one value (DH-EX-013), and the
releaser's now-stale "release" click would otherwise release an observation set they never
actually saw.

- **`compute_result_fingerprint`**: a SHA-256 digest over the canonical, sorted set of every
  effective `(lab_order_item_id, lab_observation_id)` pair — immutable observation *identities*,
  never raw patient values (two observations could coincidentally share a value with different
  provenance; a correction always mints a new observation id, so any correction between inspection
  and release necessarily changes the digest). One function, two callers: the lab-side inspection
  read (`LabResultInspectionResponse.result_fingerprint`) and `release`'s own re-check, so they can
  never independently drift.
- **The release endpoint now requires `expected_result_fingerprint`** — the exact value from a
  `GET .../results` call the releaser must have actually just made. Inside the existing
  `LabOrder`-locked transaction, `release` recomputes the current fingerprint and compares before
  creating any release row; a mismatch raises `StaleResultFingerprint` (409) — a controlled
  conflict, never a silent release of a set the releaser did not inspect. A replay of an *already
  completed* release is unconditionally safe regardless of the fingerprint passed (the release
  already happened; the check exists to gate the first, real commit, not to re-litigate history).
  This is a deliberate, justified breaking change to the release endpoint's request contract — not
  yet a live production endpoint at the time of this change.
- **The race is resolved by whichever operation's `LabOrder FOR UPDATE` lock is granted first**,
  identical serialization discipline to every other concurrent mutation in this module: if a
  correction wins the lock first, the fingerprint changes and a stale release attempt fails,
  forcing the releaser to re-inspect; if release wins first, it succeeds against the still-fresh
  set, and a concurrent correction attempt (now against a `COMPLETED` order) is refused by the
  pre-existing, unrelated `IN_PROGRESS`-only gate every entry/correction mutation already shares.
  Verified directly against real PostgreSQL (`test_dhlab004_postgres_qualification.py`'s
  `test_pgrel_correction_winning_the_lock_invalidates_a_concurrent_stale_release`, 20 repetitions,
  zero flakes) — both orderings produce an internally coherent, non-split-brain result.
- **No automatic retry after a stale-fingerprint conflict.** The releaser is shown "Les résultats
  ont changé depuis votre vérification. Veuillez les examiner à nouveau avant de les libérer." and
  the UI forces a fresh re-inspection before offering release again — never a blind resubmission of
  the same stale fingerprint.
- **`released`/`delivered`/`reviewed` remain three independent concepts, unchanged.** Release does
  not mark `ResultDelivery` delivered and does not touch `DoctorReview` beyond the existing
  version-binding this ADR already established.

---

## ADR-045 — OPEN: pre-release correction vs. post-release amendment of an entered lab result

**Date raised:** 2026-09-13 · **Status:** OPEN — blocks the next result-workflow implementation
ticket · **Human Decision Required:** Yes (the questions below)

**Relationship to ADR-044.** Not a reopening. ADR-044 resolved who may release and whether the same
human may enter and release; its own "flagged risk, not a question requiring a vote" section already
named that V1 has no result amendment/correction path and that release sharpens, without creating,
that gap. DH-EX-012A (`PROJECT_STATE.md`) is the full audit and decision packet this ADR summarizes;
that entry is authoritative on evidence and reasoning, this entry is the durable pointer and the
explicit question set. **This ADR treats pre-release correction and post-release amendment as two
separate problems, per DH-EX-012A's own central instruction** — a decision on one does not answer
the other.

**Confirmed by DH-EX-012A's audit, stated as fact, not opinion:** `LabResult` has no entering-actor
column (only the order-centric `lab_result_recorded` audit event records who acted, correlated to a
specific result only by timestamp proximity, never an exact join); no revision/version/supersedes
metadata exists on any result row; the provider result-read route re-queries `LabResult` live on
every request with no snapshot taken at release time — so a hypothetical in-place mutation of an
already-released result would silently and immediately change what a provider next sees, under the
same unchanged `LabResultRelease` record, with no trace that anything changed. This last fact is why
DH-EX-012A evaluates "may an already-released result ever be changed in place" as **no** — not
undecided, a reasoned conclusion from the mechanism itself, independent of what amendment mechanism
(if any) is chosen instead.

**Question 1 — may a pre-release (not yet released) entered result be corrected, and how?** Options
on record (full tradeoffs in DH-EX-012A): (A) no — abandon/recreate outside a supported workflow;
(B) yes, destructive overwrite of the existing row; (C) yes, via an immutable revision that
supersedes the original without deleting it. DH-EX-012A evaluates (C) as the architecturally
consistent choice — the same append-only-immutable-fact pattern this codebase already uses twice
(`LabSampleCollection`, `LabResultRelease`) — without adopting it.

**Question 2 — who may perform a pre-release correction?** (A) `LAB_TECH` (symmetric with entry
authority); (B) `LAB_RESULT_RELEASER` only; (C) a dedicated correction role (evaluated as likely
premature); (D) `LAB_TECH` before release, a stronger authority required after. Not decided.

**Question 3 — must a correction carry a reason, and in what form?** (A) free-text — flagged as in
direct tension with `docs/MVP-1.0.md` §13's existing, already-decided "no free-text clinical notes"
boundary, not merely undesirable in the abstract; (B) closed reason codes — the codes themselves are
explicitly **not** designed by DH-EX-012A, since they would be real laboratory-workflow content; (C)
no reason required, old/new value plus actor/time only. Not decided.

**Question 4 — if post-release amendment is ever built, what happens to an already-terminal
`DoctorReview` disposition?** (A) leave it unchanged — confirmed risk: nothing in today's API
response distinguishes "reviewed the current result" from "reviewed a now-superseded result"; (B)
reset to `PENDING` — a real, disclosed behavior change to an existing, already-shipped invariant
(`ReviewAlreadyFinalized` exists specifically to make a terminal disposition un-overwritable); (C)
review history/versioning, mirroring whichever schema shape Question 1 eventually chooses for
results — blocked today by `DoctorReview.lab_order_id` being `unique=True`, so this requires a
migration, not merely new application code; (D) post-release amendment unsupported for MVP, in
which case this question does not need resolving yet, but the real operational fallback must be
documented, not left implicit. Not decided.

**Question 5 — is post-release amendment required before DHealth's first real-patient pilot, or can
pre-release-correction-only ship first?** DH-EX-012A's own central finding: a real diagnostic
laboratory result can and does sometimes require correction *after* release in ordinary operation —
this is not an edge case invented to justify scope, and DHealth today has no supported path for it
once release occurs. Three real options, each with a real cost, none preferred by DH-EX-012A's
audit: (1) block the real-patient pilot until post-release amendment exists; (2) a documented
facility fallback entirely outside DHealth, accepting that DHealth's own record can become
known-wrong with no in-system trace; (3) implement post-release amendment before pilot. **This
question must not be answered by timeline pressure** — DH-EX-012A's own explicit instruction,
repeated here because it is the single highest-consequence question in this ADR.

**If post-release amendment is pursued (not yet decided whether it will be), these sub-questions are
open and not answered by Questions 1–5 above:** whether amendment produces a second, independent
release-like record or extends the existing one (the former preserves the original release's own
immutability completely; the latter reopens the exact mutation risk this ADR's
Question-1-independent "no" already rejects, see below); what a provider sees for an order with
both an original and an amended result (newest-only, a flagged newest-plus-history, or full separate
reports — no silent replacement of already-shown clinical content, in any shape); how the existing
one-row-per-order `ResultDelivery` model, whose own `DELIVERED` semantic ADR-043 already left
undecided, would represent a second delivery-equivalent event; and a hard structural fact, not a
design preference — `LabOrderStatus.COMPLETED` is terminal in today's state machine (no outbound
transition exists from it, `orders.py:59-68`), so no amendment model can move an already-`COMPLETED`
order back out of `COMPLETED` to re-run today's release path unmodified.

**Not decided here, not implementation-blocking on their own:** the exact result-revision schema
shape (a separate revision table vs. a supersedes-chain on the existing table — DH-EX-012A flags the
former as the closer structural analogy to this repository's own `LabPrice.superseded_at` pattern,
without adopting it); whether a future revision row should carry its own `entered_by_identity_id`
directly, in addition to the existing order-centric audit event.

**Full audit, evidence, and reasoning:** `PROJECT_STATE.md`'s DH-EX-012A entry.

---

**Decision accepted by Liban — 2026-09-13 (DH-EX-013).** Questions 1–3 are resolved, **for
pre-release correction only**. This section is an append, not a revision — the OPEN packet above
(questions, options, flagged risk) stands unchanged as the record of what was evaluated and why;
this is the record of what was chosen. Questions 4 and 5, and post-release amendment as a whole,
remain fully OPEN — nothing below narrows them, and post-release mutation of an already-released
result remains forbidden, exactly as this ADR's own "confirmed by DH-EX-012A's audit" paragraph
already established.

**Question 1 answered: Option C** — an immutable revision that supersedes the original observation
without ever deleting or overwriting it. Implemented as a self-referential supersession chain on
`LabObservation` (`supersedes_observation_id` → the corrected predecessor, `superseded_at` marking
when a row stopped being effective), matching the same append-only-immutable-fact pattern this
codebase already used for `LabPrice.superseded_at` and `LabResultRelease`. The **effective**
observation for any container is defined as `superseded_at IS NULL` — an explicit relational fact,
never inferred from timestamp ordering. All versions remain in the database permanently; the full
chain is reconstructable via a dedicated history read, never destroyed.

**Question 2 answered: Option A** — `LAB_TECH`, symmetric with entry authority. A correction does
not require `LAB_RESULT_RELEASER`; the same `LabOrder`-locked `IN_PROGRESS` gate that already backs
every other entry mutation is what makes correction structurally refuse once the order is released
(ADR-044's `LAB_RESULT_RELEASER` role is unaffected and untouched by this decision).

**Question 3 answered: Option C** — no reason required. A correction row carries only the new
value, the correcting actor (`recorded_by_identity_id`, populated on every new observation from
DH-EX-013 forward — both ordinary entries and corrections; pre-existing observations keep this
column `NULL` rather than a fabricated backfill), and the server timestamp, plus the supersession
link to the observation it replaces. No reason field exists anywhere in the request, model, or
audit event — no closed reason-code vocabulary was invented, matching Question 3's own flagged
tension with `docs/MVP-1.0.md` §13's "no free-text clinical notes" boundary.

**Implemented exactly as this ADR's own audit evidence recommended, now binding, not merely
recommended:**

- Correction is scoped strictly pre-release: permitted whether or not the item's own entry is
  already complete, refused outright (409) once the order reaches `COMPLETED`/is released — enforced
  by the database-locked order-status check, not an application-layer timestamp comparison alone.
- No destructive overwrite anywhere: a correction always inserts a new `LabObservation` row and
  marks its predecessor `superseded_at`, in one transaction; it never updates an existing
  observation's value columns in place.
- Chain integrity is enforced at the database, not only in application code: a full `UNIQUE`
  constraint on `supersedes_observation_id` prevents two successors from ever superseding the same
  predecessor (the concurrency backstop behind the existing `LabOrder FOR UPDATE` lock) — no partial
  index was needed, since PostgreSQL already treats `NULL` as distinct from every other `NULL` under
  `UNIQUE`, so the never-corrected majority of observations (`supersedes_observation_id IS NULL`)
  are left unconstrained by this same column — and a
  `CHECK` constraint forbids an observation from superseding itself.
- Release, provider visibility, and lab inspection all read the effective set only
  (`superseded_at IS NULL`); superseded versions never appear as current anywhere outside the new,
  lab-authorized-only correction-history endpoint. Lab inspection additionally surfaces a
  `has_correction_history` indicator without exposing the historical values inline.
- `LabResult.entry_completed_at` is never cleared or touched by a correction.
- `DoctorReview`, `ResultDelivery`, and every patient-facing surface are unaffected — none of them
  read `LabObservation` directly, so none of them required any change for this ticket.
- Real-PostgreSQL qualification (not merely SQLite) exercised: concurrent corrections of the same
  observation (exactly one winner), correction-vs-release race (release never ships a stale value),
  concurrent corrections on independent observations (both succeed independently), and
  correction-vs-ordinary-add (the post-release freeze applies identically to both).
- Downgrade of migration `0017_observation_revisions` fails closed if any correction history, actor
  provenance, or the new `lab_observation_corrected` audit vocabulary exists in the target database
  — verified against real PostgreSQL, not merely asserted.
- **Post-release amendment (Questions 4–5) is explicitly out of scope for DH-EX-013 and remains
  OPEN, to be picked up by DH-EX-014.** Nothing in this implementation moves `LabOrderStatus` out of
  `COMPLETED`, reopens a `DoctorReview` disposition, or creates any second release-like record —
  this ADR's own "hard structural fact" about `COMPLETED` being terminal is unchanged and unaffected.

**Implementation record:** `PROJECT_STATE.md`'s DH-EX-013 entry (migration
`0017_observation_revisions`, `exchange/results.py`'s `correct_observation`/
`get_correction_history`, the correction and correction-history endpoints, real-PostgreSQL
concurrency qualification, downgrade-guard qualification, and full test evidence).

**Status: Questions 1–3 are now RESOLVED for pre-release correction.** Questions 4–5 and
post-release amendment as a whole remain **OPEN**, recorded here rather than editing the header
above, so the original question/evidence packet remains legible as written at the time it was
raised.

**2026-09-18 dated addendum (DH-LAB-005).** Questions 4–5 (post-release amendment) and the general
amendment *architecture* were separately resolved by ADR-048 Model 3 / DH-EX-014B, independent of
this ticket. What DH-LAB-005 adds is narrower: a **Native controlled** counterpart to this ADR's
own pre-release correction (`correct_native_value`, both pre-release and inside a
DH-EX-014B amendment) for the specific case DH-LAB-004 deliberately left closed — a
template-bound observation, whose raw `correct_observation`/amendment-`correct_observation`
endpoints both now refuse outright (`TemplateBoundContainerRefusesUnstructuredWrite`, the amendment
side newly hardened this ticket to match the pre-release side DH-LAB-004 already hardened). The
Native path derives every structural field server-side from the predecessor's own pinned template
definition instead of accepting it from the browser — a stricter, not looser, version of this
ADR's own "immutable supersession chain, no reason field" decision. **No correction-reason field of
any kind was added anywhere in this ticket, on either path** — the decision this ADR already made
(Decision packet, "no reason field") is preserved unchanged, not merely left alone by omission. See
`PROJECT_STATE.md`'s DH-LAB-005 entry for full implementation record.

---

## ADR-046 — ACCEPTED (architectural requirement only): orderable diagnostic service and returned observation cardinality

**Date:** 2026-09-13 · **Status:** ACCEPTED for the single requirement stated below — **everything
else it touches remains OPEN**, resolved by neither this ADR nor DH-FIELD-001, the ticket that
raised it · **Decided by:** Liban, from direct review of two real Djibouti diagnostic workflow
artifacts (a clinic-issued lab order and a laboratory-generated result packet), reported as
generalized findings to DH-FIELD-001. **No real patient data from either artifact appears in this
entry, in `docs/FIELD-DIAGNOSTIC-WORKFLOW.md`, or anywhere else in this repository** — see that
document's own header for the exact handling discipline followed.

**Relationship to ADR-044/ADR-045.** Not a reopening of either. ADR-044 (result release
authorization) and ADR-045 (pre-release correction vs. post-release amendment) both already assumed,
implicitly, that "a result" means one `LabResult` row per `LabOrderItem` — the current Exchange
schema's actual shape. This ADR records field evidence that this assumption does not hold for real
panel-based laboratory workflows, without retracting or altering either prior ADR's own content.

### The accepted requirement

> DHealth's production result domain must support one ordered diagnostic service producing zero,
> one, or multiple returned observations. The ordering clinician is not required to order each
> underlying observation individually. The current `LabOrderItem → one LabResult` implementation is
> an interim Exchange representation and must not be treated as the final model for real
> panel-based workflows.

This is accepted as an architectural requirement — a constraint any future result-domain schema must
satisfy — not as a schema, migration, or implementation. Nothing in the current Exchange codebase
changes as a result of this ADR.

### Explicitly NOT decided by this ADR

- The exact result-domain schema shape (a new `Observation`-per-`LabResult` model, a nested
  structure, or any other representation) — DH-EX-012B's job, not this ADR's.
- Any specific panel's composition, analyte vocabulary, or fixed observation set — real laboratory-
  workflow/catalog evidence and clinical-lead sign-off are required before any such content is
  recorded anywhere in this repository, per `AGENTS.md`'s existing rule against inventing clinical
  content.
- Analyzer integration, middleware, or interface protocol of any kind — not demonstrated by the
  reviewed evidence (`docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Finding 15).
- The LIS interface type (API/FHIR/HL7/CSV or otherwise) for any future DHealth Connect integration.
- Report/PDF/document storage — a separate product and privacy decision.
- The post-release amendment architecture ADR-045 already left open — unaffected and unresolved by
  this ADR.

### Consequence for ADR-045

**ADR-045 remains OPEN, not resolved by this ADR.** But its own implementation is now explicitly
**paused**, for a reason distinct from ADR-045's own unresolved questions: building a correction/
revision mechanism on top of today's one-`LabResult`-per-item representation — which this ADR just
confirmed is not the final production shape — risks being redone once the result-domain cardinality
question is resolved. **DH-EX-012B is the next result-domain ticket**, responsible for resolving the
schema shape this ADR requires; ADR-045's own Questions 1–5 remain answerable independently of
DH-EX-012B's outcome, but their *implementation* should wait for a stable underlying schema.

**Full evidence, generalized field findings, and the full reconciliation this ADR triggered:**
`docs/FIELD-DIAGNOSTIC-WORKFLOW.md` and `PROJECT_STATE.md`'s DH-FIELD-001 entry.

---

## ADR-047 — RECOMMENDED, NOT YET ACCEPTED: result-domain schema to satisfy ADR-046 (`LabOrderItem`
→ container → `LabObservation[]`)

**Date raised:** 2026-09-13 · **Status:** RECOMMENDED (technical architecture) — **NOT ACCEPTED**;
one embedded question is **OPEN, Human Decision Required: Yes** · **Ticket:** DH-EX-012B, decision/
architecture only, per its own explicit instruction — no migration, no model change, no API change
implements any of this yet.

**Relationship to ADR-046/ADR-044/ADR-045.** ADR-046 accepted the requirement (one ordered
diagnostic service may produce zero, one, or many observations) without choosing a schema — this is
the schema recommendation ADR-046 named as DH-EX-012B's job. It does not reopen ADR-044 (release
stays order-level, unchanged) and does not resolve ADR-045 (pre-release correction / post-release
amendment remain OPEN and paused — see "ADR-045 compatibility" below for why this schema does not
make either harder). Mirrors ADR-044's own two-phase shape: this entry is the evaluated packet,
pending Liban's confirmation, not a decision this ticket makes on its own.

### Current model, audited precisely before recommending anything

`LabOrderItem.result` is a one-to-one relationship (`models.py:406`); `LabResult.lab_order_item_id`
carries a database-level `unique=True` (`models.py:510-514`), not merely an application check — the
one-result-per-item assumption is enforced by PostgreSQL itself. `LabResult` today is a single row
holding both identity (`lab_order_item_id`, `lab_organization_id`) and value (`result_type`,
`numeric_value`/`numeric_unit`/`reference_range_low`/`reference_range_high`, `text_value`,
`completed_at`) — container and content are the same row. The **one function that must change**,
found by tracing every one-result-per-item assumption package-wide (not `results.py` alone):
`_count_missing_results` (`results.py:726-737`) — a single `LabOrderItem OUTER JOIN LabResult ...
WHERE LabResult.id IS NULL` aggregate, shared verbatim by result submission's `ready_for_release`
computation and `LabResultReleaseRepository.release`'s own re-check (`results.py:420`, `results.py:
564`). This is the sole choke point where "every item has a result" is asked; nothing else
duplicates the query. Every other place inspected assumes the same 1:1 shape only *because* this
query enforces it upstream: `DuplicateResult` (`results.py:115`, raised at `results.py:381` and
`results.py:406` from the DB `IntegrityError` translation), `get_for_item`/`list_for_order`/
`get_for_lab` (`results.py:429-483`, each a plain `WHERE lab_order_item_id = ...` expecting ≤1 row),
`ProviderOrderResultsResponse`/`ProviderResultItemResponse` and `LabResultInspectionResponse`/
`LabResultInspectionItemResponse` (`api.py:832-893`, one flat item per ordered item, no nesting), and
the migration chain itself (`0010_exchange_results_workflow` created the unique FK; no later
migration through `0015` relaxes it). Checked directly, not assumed: every `LabResult`-referencing
test file in the package (`test_exchange_results_api.py`, `test_exchange_lab_workflow.py`,
`test_exchange_patient_access.py`, `test_dhex007_postgres_qualification.py`) — the only count-based
assertions found (`test_exchange_lab_workflow.py:345,392`, both `select(func.count()).select_from(
LabResult) == 0`, asserting no result exists yet at a given workflow stage) are consistent with 0-or-1
per item, not a wider count. The one-result-per-item shape is narrow and consistent, not scattered
speculation, which is exactly why introducing a child table is a bounded change: it needs to replace
exactly this one query's predicate and the four read paths listed above, not an unbounded search.

**Measured, not estimated, test-migration cost.** `adapter/tests/test_exchange_results_api.py` (47
test functions) contains 8 assertion sites indexing a flat `results[i]` item shape by position or
key-set (e.g. lines 355, 379–380, 1025: `inspection_body["results"][0]["numeric_value"]`,
`results_body["results"][0]`, `set(body["results"][0].keys())`). `adapter/tests/integration/
test_dhex007_postgres_qualification.py` makes zero such assertions. Any provider/lab-inspection API
reshape has a concrete, small, known cost — not an open-ended one.

### Options evaluated

**Option A — `LabOrderItem` has many `LabObservation` rows directly, no container.** Rejected.
`LabOrderItem` is today a frozen order-time snapshot — `catalog_code_snapshot`,
`catalog_display_name_snapshot`, `selected_price_amount`/`selected_price_currency` are all written
exactly once, at order/selection time, and never rewritten (`models.py:376-406`). The result-domain
needs a mutable, per-item *workflow* fact — "has this item's result entry been explicitly marked
complete" — and nothing about `LabOrderItem`'s existing role makes it the right place to bolt that
on; doing so would mix an immutable ordering snapshot with mutable result-workflow state on the same
row, the exact kind of overload this ticket's own Option C discussion warns against, just moved to a
different table. Option A also has no natural attachment point for per-item source provenance (a
source report id, a source-declared finality flag) without either repeating it on every observation
or, again, bolting it onto `LabOrderItem`.

**Option B — a distinct `LabResultSet` entity between item and observations.** Rejected, not
because the idea is wrong but because nothing distinguishes it from a container: the "grouping"
`LabResultSet` would exist in a strict one-to-one relationship with `LabOrderItem` doing exactly the
job a renamed `LabResult` already does. A distinct result-set identity earns its keep only once a
single reported artifact can span *multiple* ordered items (Finding 14's multi-page report, Finding 4's
source-owned report reference) — that is document/report modeling, explicitly out of this ticket's
scope and not required by ADR-046's own narrow requirement (one *item* → 0..N observations, not one
*report* → many items). Introducing the extra level now, before that need is real, is exactly the
"don't add an entity because the word sounds natural" trap this ticket warns against.

**Option C — keep `LabResult` as a container, add child `LabObservation` rows. RECOMMENDED**, with
its own open question ("would it become a container? would it overload one table with incompatible
meanings?") resolved as follows: **`LabResult` becomes purely a container — every scalar value
column (`result_type`, `numeric_value`, `numeric_unit`, `reference_range_low`,
`reference_range_high`, `text_value`) moves to the new `LabObservation` child table.** Nothing stays
behind to overload the container with two meanings; the container is *only* identity
(`lab_order_item_id`, still unique — one container per item, matching today's cardinality exactly)
plus the new explicit workflow fact below. This keeps the name "the result for this item" true under
its new, correct meaning ("this item's result entry, possibly containing several observations")
rather than its old one ("this item's one scalar value").

**Option D.** Not needed — Option C, resolved this way, already satisfies every required capability
without further alternatives.

### Recommended entity relationship

```text
LabOrder
  └─ LabOrderItem            (unchanged: frozen order-time snapshot)
       └─ LabResult          (container, renamed meaning: identity + workflow fact only)
            ├─ entry_completed_at: timestamp | NULL
            ├─ entry_completed_by: identity or source-declared, see below
            └─ LabObservation[]   (0..N; each row = one returned value)
                 ├─ result_type (NUMERIC | TEXT, same CHECK pattern as today)
                 ├─ numeric_value / numeric_unit / reference_range_low / reference_range_high
                 ├─ text_value
                 ├─ identity: hybrid namespace (see below)
                 └─ recorded_at
```

**Simple, one-observation service.** Exactly today's UX: one API call records one
`LabObservation` under the item's (lazily created, on first observation) `LabResult` container. No
extra step is *required* to reach `ready_for_release` beyond the existing explicit-completion signal
below — for a native DHealth Lab UI, that signal can default to "on" in the same screen/call for a
service the UI otherwise treats as simple, which is a UI default, not a schema requirement or a
DHealth-inferred fact.

**Multi-observation / panel service.** Each returned value is its own `LabObservation` row under the
same container, added by however many calls the native UI or a Connect import needs; the container's
explicit completion signal is set once entry is genuinely finished, independent of how many
observation rows exist.

### Observation identity — hybrid namespace (Option D from the ticket's own framing)

Every `LabObservation` carries a mandatory source/display identity — `source_code`,
`source_system`, `display_label_snapshot` — sufficient for a freeform DHealth Lab native entry or a
dynamically-arriving LIS-reported observation with no DHealth-side template at all. An **optional**
`catalog_observation_entry_id` may point at a future DHealth-owned observation-catalog table
(mirroring `TestCatalogEntry`'s existing versioned-catalog pattern) — not created by this ticket, and
never required. This is the identical shape this codebase already uses twice for the same class of
problem: `PatientReference`/`PractitionerReference` (mandatory source-identity binding, no DHealth
demographic layer required) and `TestCatalogEntry` vs. a lab's own `local_test_code`
(`LabOffering.local_test_code`, optional). No real observation code, unit, or vocabulary is invented
by naming this shape.

### Configured vs. dynamic observations

Not a schema fork — both populate the identical `LabObservation` table. A future, separately-decided
catalog-level concept ("this diagnostic service has N configured observation templates") would only
change what a native DHealth Lab UI pre-renders; a Connect import populates the same rows keyed by
whatever the source reports, matching or not matching any template. No panel composition, template
count, or template content is defined here.

### Completeness — the single most important question, answered once, not by counting

**One explicit `entry_complete` signal per `LabOrderItem`'s container, and only one mechanism, used
identically by both sources:** a human action in DHealth Lab's native UI, or a source system's own
declared finality flag on Connect import. **Never inferred by DHealth from how many `LabObservation`
rows exist.** This directly answers the ticket's own warning against letting "some observations
exist" imply clinical completeness: it does not — only an explicit signal does, matching the same
discipline `LabSampleCollection`/`LabResultRelease` already apply to physical-world/authorization
facts DHealth must record but never infer. `_count_missing_results`'s predicate becomes "does this
item have a container with `entry_completed_at IS NOT NULL`" in place of today's "does this item
have a `LabResult` row" — the identical shared-by-submission-and-release choke point, re-pointed at
one column instead of row existence.

### Order-ready-for-release / ADR-044 compatibility

**ADR-044's order-level release is unaffected and remains valid as recommended, not reopened.**
Release still requires every ordered item to satisfy the same kind of DB-grounded aggregate check it
already re-derives itself, never trusting a prior response — only the predicate's target column
changes (see above). `LabResultRelease` itself needs no structural change: it is already an
order-level, immutable, single-row-per-order fact, orthogonal to how many observations exist
underneath.

**Immutable release-state binding.** Observations remain insert-only, exactly like today's
`LabResult` rows are — release still binds to "whatever the current, unamended set of observation
rows is at release time," the identical binding today's release already has to "whatever the current
set of `LabResult` rows is." Nothing about adding a child table weakens this.

### ADR-045 (correction/amendment) compatibility — not resolved, not made harder

DH-EX-012A already evaluated an immutable-revision pattern (`supersedes_result_id`/`superseded_at`,
explicitly analogized there to `LabPrice.superseded_at`) as the architecturally consistent choice for
correction, without adopting it. This schema gives that exact pattern a clean, narrow attachment
point: a future revision mechanism can supersede individual `LabObservation` rows (add
`superseded_at`/`supersedes_observation_id` to that table alone) without touching the container or
`LabOrderItem` at all, and without needing a per-item "version number" concept. This is *more*
narrowly scoped than correcting a whole-item scalar `LabResult` row today, not less — a correction to
one observation in a five-observation panel need not touch the other four. **ADR-045's five
questions remain entirely unanswered by this ADR** — role, reason requirement, and post-release
`DoctorReview` handling are unaffected by which table an observation lives in.

### Source-time, external-reference, and reference-range compatibility

Not implemented now; each has a natural, non-forcing attachment point this shape does not block.
Nullable `source_resulted_at`/`source_validated_at`/`source_released_at` could land on the container
(one per-item source timeline) and/or per-observation, if a future ticket finds real evidence they
differ at that granularity — not decided here. Source-owned external references
(`source_report_id`/per-observation LIS codes, per Finding 4) attach most naturally at the
observation level, the finest-grained real-world identity a source system reports. A future
`reference_range_text` (nullable) column on `LabObservation`, alongside the existing numeric
low/high pair, is the natural extension for Finding 8's inequality-style/textual ranges — no
interpretation of any range is added by having a place to store it verbatim.

### Provider API / Lab inspection target shape

**`ProviderOrderResultsResponse` should nest, not stay flat** — the ticket's own instruction ("do
not preserve a flat scalar API merely for backward compatibility if doing so makes panel results
fundamentally wrong") applies directly once one ordered item can carry several observations; a flat
per-item response cannot represent that at all. Target shape: one entry per ordered item (item id,
catalog snapshot, `entry_completed_at`) containing a list of its observations (each with its own
identity/value/range). `LabResultInspectionResponse` takes the identical reshape, since it already
mirrors `ProviderOrderResultsResponse`'s per-item structure one-for-one today. **Compatibility
strategy:** this is a breaking response-shape change for exactly the 8 measured assertion sites in
`test_exchange_results_api.py` above — small, known, and worth paying once rather than carrying a
flat shape that cannot honestly represent a panel; not performed in this ticket.

### Native DHealth Lab and DHealth Connect compatibility

**Native:** one item, one container (created lazily on first observation), 1..N observation calls,
one explicit completion action — the same shape whether the service is simple or a panel, so the
native Lab screen sketch in this ticket's own prompt (single field vs. a list of fields) maps
directly onto "how many observation rows does this container have," decided by the UI/catalog, not
the schema.

**Connect:** an LIS-imported report's observations populate the same `LabObservation` rows under the
same container, keyed by the source's own codes (hybrid namespace above), with the source's own
finality flag driving the identical `entry_complete` signal native entry uses. No LIS protocol,
interface type, or analyzer-automation claim is made or required by this schema.

### Legacy migration strategy (not performed in this ticket)

Lossless, fabricates nothing: each existing `LabResult` row becomes one container row (reusing its
existing `lab_order_item_id`/`lab_organization_id`) plus exactly one child `LabObservation` row
carrying its existing `result_type`/`numeric_value`/`numeric_unit`/`reference_range_low`/
`reference_range_high`/`text_value` verbatim, with the observation's own timestamp set from the
existing `completed_at`. The container's `entry_completed_at` is backfilled to that same
`completed_at` — not a fabricated new fact, since under the old model that single observation was, in
fact, the entirety and totality of what would ever be entered for that item; re-expressing that as
"entry complete at the moment it was recorded" states nothing the old row did not already truthfully
imply. No actor, source, or additional timestamp is invented beyond what already exists in the row
being migrated.

### Human Decision Required — the one genuinely open question inside this recommendation

**May an ordered item ever reach `entry_complete` with zero `LabObservation` rows, and if the answer
is yes, what does a provider see in place of a value?** This is not academic: today,
`_count_missing_results` guarantees every released order's every item has *exactly one* recorded
result before `COMPLETED` is reachable at all (`results.py:726-737`) — a real, currently-true
guarantee. Allowing a zero-observation "complete" item would let an order reach `COMPLETED` and
become provider-visible (`ProviderOrderResultsResponse`, gated only on `order.status ==
'COMPLETED'`) with an ordered item that produced no value at all — a new reachable state that
requires *representing* something (`"not performed"`, `"specimen rejected"`, or similar) in place of
a value, and the exact content of that representation is real laboratory-workflow content this ADR
is not positioned to invent, per `AGENTS.md`'s "never invent clinical content" rule.

**Options, not adopted here:**
(a) **No** — every item must carry ≥1 observation before its container can be marked
`entry_complete`; a genuinely zero-result outcome is only ever the pre-entry state, never a
terminal one, in MVP. Narrowest option: preserves today's "every released item has a value" guarantee
exactly, unchanged. **Recommended as the MVP-safe default**, but not decided by this ADR.
(b) **Yes**, with a not-performed/no-result representation — the representation's real content (a
status code, its meaning, whether it requires a reason) is explicitly deferred to real laboratory
input, not designed here, matching Decision 3's identical deferral in ADR-045/DH-EX-012A for
correction-reason codes.
(c) **Defer entirely** — make zero-observation completion structurally unreachable for MVP (i.e.,
option (a) in practice) and revisit only if real pilot evidence shows it is needed.

**Human Decision Required: Yes**, specifically this question. Nothing else in this ADR requires a
human decision beyond ADR-045's own, already-recorded, unresolved question set — the schema shape
itself is recommended architecture, not a product-policy call.

### Explicitly out of scope (unchanged from this ticket's own instructions)

No migration, no model change, no API change is implemented by this ADR. Full FHIR `Observation`
modeling, LOINC/universal terminology mapping, an analyzer interface engine, lab QC, reagent
inventory, microbiology isolate/susceptibility modeling, pathology narratives, imaging, a universal
report/PDF repository, and a national terminology service are all explicitly not required by
anything recommended here, and none is proposed.

**Full evidence, dependency map, and options analysis:** `PROJECT_STATE.md`'s DH-EX-012B entry.

---

**Decision accepted by Liban — 2026-09-13.** This section is an append, not a revision — the
RECOMMENDED packet above (architecture, options, rejected alternatives, evidence) stands unchanged
as the record of what was evaluated and why; this is the record of what was chosen and implemented,
per DH-EX-012C.

**Recommended architecture accepted, exactly as evaluated above:** ``LabOrder → LabOrderItem →
LabResult (container) → LabObservation[]``, with ``LabResult`` reduced to identity plus the two
completion-provenance columns, every scalar value column moved to the new ``LabObservation`` child
table, and the hybrid-namespace observation identity design (mandatory ``source_code``/
``source_system``/``display_label_snapshot``, no optional catalog-link column added since no such
catalog table exists yet). Nothing about the recommended shape changed between recommendation and
implementation.

**The one open question is resolved: NO zero-observation completed result in MVP (Option (a)).** An
ordered item's container may exist with zero observations while its entry is incomplete — that is
the ordinary in-progress state, not an error. It may **never** transition to
``entry_completed_at`` being set while it has zero observations. Concretely:

```text
0 observations + incomplete  = valid
>=1 observation + incomplete = valid
>=1 observation + complete   = valid
0 observations + complete    = INVALID, structurally refused
```

`LabResultRepository.complete_entry` refuses with `EntryNotReadyForCompletion` if zero observations
exist at the moment completion is attempted. `LabResultReleaseRepository.release` independently
re-derives the identical completeness fact from the database on every call (never trusting the
completion endpoint alone) and additionally treats a container found with `entry_completed_at` set
but zero observations as a release-blocking `ResultsNotReadyForRelease` condition — real
defense-in-depth, not merely written: a real-PostgreSQL negative control manufactured exactly this
malformed state via a raw row insert and confirmed release refuses it (see PROJECT_STATE.md's
DH-EX-012C entry).

**Explicitly, deliberately NOT defined by this decision, exactly as flagged when the question was
raised:** no "not performed" / "unable to perform" / "cancelled test" / "insufficient sample" / "no
result obtainable" representation of any kind is introduced. If a real laboratory workflow later
requires representing that an ordered service produced no result, that requires its own future
explicit field/clinical-workflow decision — this decision does not anticipate, name, or reserve a
value for it.

**Implementation record:** `PROJECT_STATE.md`'s DH-EX-012C entry (migration
`0016_exchange_observations`, `exchange/models.py`'s `LabObservation`/redefined `LabResult`,
`exchange/results.py`'s `record_observation`/`complete_entry`/reshaped `record_numeric`/
`record_text`, the new `POST .../items/{item_id}/observations` and
`POST .../items/{item_id}/complete-result-entry` routes, nested provider/lab-inspection response
shapes, real-PostgreSQL qualification, and full negative-control evidence).

**Status: this ADR is now RESOLVED**, not RECOMMENDED — recorded here rather than editing the
header above, so the original evaluated packet remains legible as written at the time it was
proposed, exactly matching ADR-044's own append-don't-rewrite precedent.

**ADR-045 (pre-release correction vs. post-release amendment) remains OPEN and its implementation
remains paused**, unaffected and unresolved by this decision — nothing in DH-EX-012C's
implementation begins correction/amendment work.

---

## ADR-048 — RECOMMENDED / OPEN — Human Decision Required: post-release amendment and release-version architecture

**Date raised:** 2026-09-13 · **Status:** RECOMMENDED (technical architecture) — **NOT ACCEPTED**;
several embedded questions are OPEN, Human Decision Required: Yes · **Ticket:** DH-EX-014D,
audit/decision packet only, per its own explicit instruction — no migration, no model change, no
API change, no `DoctorReview`/`ResultDelivery` mutation implements any of this yet.

**Relationship to ADR-044/ADR-045/ADR-046/ADR-047.** Does not reopen ADR-044 (release stays
order-level; `LAB_RESULT_RELEASER`, additive to `LAB_TECH`, same human may hold both, no
second-person requirement — unaffected). Does not touch pre-release correction, resolved and
implemented by DH-EX-013 (ADR-045 Questions 1–3) — that mechanism is reused, not redesigned, as the
value-level primitive an amendment's own release step ultimately relies on. **This ADR is the
decision packet for ADR-045's Questions 4–5 and the amendment-model sub-questions ADR-045 itself
named but left unanswered** — ADR-045's own OPEN packet and its DH-EX-013 addendum are unedited by
this entry; this is a new, separate, linked decision, not a revision of either.

### 1 — Current-state evidence, audited fresh against commit `8328d2e400217837aae6cca8cf17fde7e54dea47`

- **Release** (`models.py:670-706`): `LabResultRelease` is one immutable row per `LabOrder`
  (`lab_order_id` UNIQUE) — `released_by_identity_id`, `released_at` only. No revision, no
  un-release, no delete method anywhere in `LabResultReleaseRepository` (`results.py:1084-1188`).
  `release()` re-checks `_count_missing_or_malformed_results` against live DB state, then inserts
  the release row and flips `LabOrder.status` to `COMPLETED` in the same transaction
  (`results.py:1171-1181`). `LabResultRelease` stores **no reference to which `LabObservation` rows
  were effective at that moment** — it is a pure authorization fact, not a value snapshot.
- **Provider read** (`api.py:3018-3071`, `get_lab_order_results`): gated solely on
  `order.status == COMPLETED`; on every call it re-queries `results.list_for_order(order_id)` live,
  which itself queries `LabObservation` filtered to `superseded_at IS NULL` (effective-only, per
  DH-EX-013). **There is no join, snapshot, or manifest binding a specific `LabResultRelease` row to
  the specific observation rows it authorized.** A provider re-reading the same order always sees
  today's effective set, not "the set that existed at release time."
- **Pre-release correction** (`results.py:589-745`, DH-EX-013, unchanged by this ticket): a
  correction inserts one new `LabObservation` superseding a predecessor, in the same transaction,
  reusing `_lock_order_and_item_for_entry`'s `LabOrder FOR UPDATE` + `IN_PROGRESS` gate — this is
  exactly what makes correction refuse once the order is `COMPLETED`. **Load-bearing constraint,
  verified directly, not assumed:** `uq_exchange_lab_observations_supersedes_observation_id`
  (`models.py:638-641`) is a full, unconditional `UNIQUE` constraint — **at most one successor may
  ever supersede a given observation, permanently, with no way to reset it.** This is the single
  most consequential existing constraint for this ADR's design space; see §3's risk analysis below.
- **Order state machine** (`orders.py:57-66`): `_ALLOWED_TRANSITIONS[LabOrderStatus.COMPLETED] ==
  frozenset()` — confirmed genuinely terminal; no outbound transition exists from `COMPLETED`
  through any code path.
- **`DoctorReview`** (`models.py:737-761`): one row per order, `lab_order_id` **`unique=True` at the
  database level** — not merely an application check. Created `PENDING` at order-creation time
  (`orders.py`). `_mark_in_session` (`results.py:1278-1314`) requires `order.status == COMPLETED`,
  locks the review row `FOR UPDATE`, and refuses (`ReviewAlreadyFinalized`) any second mutation once
  the status leaves `PENDING`. **No review-reset, review-revision, or review-history mechanism
  exists anywhere in this codebase.**
- **`ResultDelivery`** (`models.py:709-734`): one row per order, `PENDING`/`DELIVERED`/`FAILED`.
  Confirmed by grep of the full `exchange/` package: no code path anywhere calls
  `mark_delivered`/`mark_failed` as a side effect of anything; `DELIVERED`'s exact semantic remains
  an explicit, separate, still-undecided product question (ADR-043, unaffected and not reopened
  here).
- **Patient surface** (`api.py:657-667`, `PatientOrderResponse`): `extra="forbid"`, no result field
  of any kind. Confirmed unaffected by anything audited in this ticket.
- **Authorization primitives already exist, independently, today** (`api.py:1238-1287`):
  `_require_lab_tech`, `_require_lab_result_releaser`, and `_require_lab_tech_or_releaser` are three
  separate, already-shipped checks. A future "`LAB_TECH` prepares, `LAB_RESULT_RELEASER` releases"
  amendment flow requires **zero new authorization primitives** — it is a new call sequence over
  existing ones, exactly mirroring ADR-044's own entry/release split one level higher.
- **Security-audit vocabulary** (`security_audit.py:24-99`): `event_type` is a DB-enforced `CHECK`
  constraint, widened per migration with a downgrade guard keyed to existing rows using the new
  vocabulary (DH-EX-013's `0017_observation_revisions` did exactly this for
  `lab_observation_corrected`). `resource_type`, by contrast, does **not** need widening for an
  amendment event — `"lab_order"` is already the established convention for every per-item-but-
  order-scoped event (`security_audit.py`'s own "audit stays order-centric" comment, reused
  unmodified by every event type added since). This asymmetry makes a future migration's audit-
  vocabulary cost smaller than it might first appear: one `CHECK` widening plus matching downgrade
  guards, no new `resource_type` value.
- **Migration head confirmed:** `0017_observation_revisions` (`down_revision` chain verified:
  `0017` ← `0016_exchange_observations`, no forks).

### 2 — Risk statement: what goes wrong if DH-EX-013 correction were simply allowed after `COMPLETED`

Concrete sequence, using only mechanisms that exist today: an order releases with observation V2
effective (5.1) → `LabResultRelease` is inserted, `LabOrder.status = COMPLETED` → the provider reads
the order, sees 5.1, and the ordering clinician calls `mark_no_follow_up_required` — `DoctorReview`
becomes terminal, `NO_FOLLOW_UP_REQUIRED` → a lab tech later notices 5.1 was wrong and (hypothetically,
if DH-EX-013's `IN_PROGRESS` gate were removed) corrects it to 5.9, inserting V3 superseding V2 → the
**same, unchanged** `LabResultRelease` row still says "released," the **same, unchanged**
`DoctorReview` row still says `NO_FOLLOW_UP_REQUIRED`, and the very next provider read of
`GET .../results` (`api.py:3018`, unmodified, still a live query over the effective set) **silently
returns 5.9 instead of 5.1**, with nothing in any API response, audit event, or database row
distinguishing "this is what was originally released and reviewed" from "this is a later, unreviewed
value now appearing under the same release." No trace exists anywhere that a change occurred. This is
not a hypothetical failure mode — it is the literal, verified behavior of `get_lab_order_results`
and `DoctorReview` today, evaluated against a naive change that merely deletes DH-EX-013's own
`IN_PROGRESS` gate. **This is exactly what ADR-045's "may an already-released result be changed in
place — evaluated as NO" already forbids**, and it is the reason this ADR treats "bind release to
exact observation versions" as the load-bearing requirement, not an optional refinement.

### 3 — The one-successor-per-predecessor constraint's consequence for amendment preparation

`uq_exchange_lab_observations_supersedes_observation_id` guarantees at most one row may ever
supersede a given predecessor. This is exactly sufficient for DH-EX-013's own pre-release model,
where a correction is inserted, permanent, and immediately effective, mirroring the same
no-undo philosophy this codebase already applies to `LabPrice.superseded_at`/`LabSampleCollection`/
`LabResultRelease`. **It becomes a real constraint on amendment design if "prepare an amendment" is
allowed to insert a `LabObservation` successor row before the amendment is released, and a "discard
this draft, start over" operation is also wanted** — a discarded draft successor still permanently
occupies the one-and-only supersession slot for its predecessor (rows are never deleted, per this
codebase's own immutability discipline), permanently blocking any future correction of that same
predecessor. **Two structurally different resolutions exist, not silently chosen here:**

- **(a) Immediate-permanent insert, no draft/discard concept** — an amendment's observation
  correction is entered exactly like a pre-release correction: the moment it is entered, it is a
  real, permanent revision (mirroring ADR-045 Question 1's own no-undo philosophy exactly). An
  "amendment episode" groups one or more such corrections and its own **release** step is what
  first exposes them to a provider — nothing about "preparing" ever creates something later
  discarded. Simplest; requires no schema change to the existing `UNIQUE` constraint; but means
  there is no way to correct a value, reconsider, and correct it back before anyone sees it, without
  that reconsideration itself becoming a second permanent, audited revision.
- **(b) Staged draft, explicit discard** — an amendment's proposed values live in a separate
  structure until release-time, and only release-time promotion actually inserts the
  `LabObservation` successor row(s). Preserves a true "discard this draft" operation, but is a
  materially larger design (a second, temporary value-holding shape parallel to `LabObservation`
  itself) for a capability real laboratory workflow evidence has not yet demonstrated is needed.

**This is a genuine open sub-question, not decided here** — it has real operational-workflow
consequences (can a `LAB_TECH` safely reconsider mid-preparation) that AGENTS.md's "obviously should"
guard applies to directly. **Recommended for technical soundness, not adopted:** (a), because it
requires no relaxation of an already-shipped, already-qualified database constraint, and because it
extends DH-EX-013's own established philosophy rather than introducing a second one. If Liban selects
(b) instead, the `UNIQUE` constraint on `supersedes_observation_id` would need to become a partial
index scoped to *released* successors only — a real future migration, not a minor addition.

### 4 — Architecture alternatives (at least three, as required)

**Model 1 — Release manifest.** A new immutable `LabResultReleaseVersion`-equivalent row per release
event (original or amendment), plus immutable membership rows, each naming exactly
`(lab_order_item_id, lab_observation_id)` — the exact effective observation, per item, that this
release authorized. Release #1's manifest names every item's observation as it stood at that moment;
release #2 (post-amendment) is a **new** manifest naming the corrected item's new observation id and
the **same, unduplicated** observation ids for every unaffected item. Reconstruction of any release is
a single join from that release's manifest rows to `LabObservation` by id — exact, immutable, and
never dependent on live "current effective" state. **Does not itself resolve who prepares/releases an
amendment, `DoctorReview` behavior, or provider display** — purely a binding mechanism.

**Model 2 — Result-set/version entity.** Introduce a first-class result-set-version aggregate
underneath the order (e.g., `LabResultSetVersion`), and re-point observations, releases, and reviews
to bind through it rather than through the order directly. More general — could eventually
accommodate future needs Model 1 does not anticipate — but is a heavier structural change: every
existing per-item read path (`get_for_item`, `list_for_order`, `get_for_lab`) would need to learn a
new intermediate aggregate that does not exist today, for a generality this evidence does not yet
demonstrate is needed. Evaluated as disproportionate to the problem actually audited.

**Model 3 — Amendment episode + release manifest.** An `LabResultAmendment` (or equivalent) row
tracks the amendment *workflow* (`PREPARING`→`RELEASED`, `prepared_by_identity_id`,
`released_by_identity_id`, `released_at`) — the natural home for §3's prepare/release split and for
§7's authority split below. Its **release** step is exactly Model 1's manifest mechanic: creates one
new immutable release-version + manifest naming the exact effective observation per item at that
moment. `LabResultRelease` itself is **never mutated, never replaced, and never reinterpreted** — it
remains, permanently, "release #1," exactly as ADR-044 shipped it; every subsequent amendment release
is a **new**, additive row of the same manifest shape, chained by order and by a sequence/version
number. This is Model 1 plus an explicit workflow container for the parts of the problem Model 1
alone leaves unanswered.

**A fourth design was considered and rejected**, per the ticket's own invitation to name one if
cleaner: reusing `LabResultRelease` itself as a self-referential chain (mirroring `LabObservation`'s
own `supersedes_observation_id` pattern) rather than a new table. Rejected because `LabResultRelease`
is explicitly documented and tested today as "immutable once created: no revision... anywhere in this
codebase" (`models.py:679`) — adding a supersession column to it would be a materially different kind
of change than adding a new, purely additive table, and would require re-auditing every existing
`LabResultRelease` read path (`get_for_order`, the grandfather-rule check in `release()`) for
"effective release" semantics that do not exist today. A new, additive table achieves the identical
result with zero risk to DH-EX-012's already-shipped, already-qualified invariants.

### 5 — Recommended architecture

**Model 3, technically recommended, not accepted.** It subsumes Model 1's exact reconstruction
mechanism (the manifest) as its own release step's output, avoids Model 2's disproportionate
structural weight, and gives §7/§8's authority split, §9's `DoctorReview` versioning, and §12's
concurrency invariants a single natural home (the amendment-episode row) rather than scattering
workflow state across ad hoc columns on other tables. `LabResultRelease` remains untouched forever,
preserving ADR-044's shipped immutability guarantee with zero risk to existing tests or behavior.

### 6 — `DoctorReview` recommendation (ADR-045 Question 4)

**Option C, evaluated as the only option that satisfies the ticket's own load-bearing invariant**
("clinician review applies to a specific released version"). Options A and B are re-evaluated, not
newly invented, from DH-EX-012A's own packet: **(A) leave unchanged** — confirmed to reproduce
exactly §2's naive-failure-mode risk (a terminal disposition silently describing content it never
saw); rejected. **(B) reset to `PENDING`** — destroys the historical fact that the *original* content
was reviewed and what that review concluded; a real, disclosed regression against `DoctorReview`'s
own already-shipped `ReviewAlreadyFinalized` invariant and its existing test coverage; rejected.
**(C) preserve history, create a new review obligation per amended release** — requires a real
migration (`DoctorReview.lab_order_id`'s `unique=True` constraint must be widened or dropped; this is
not an application-code-only change), and each `DoctorReview` row must bind to the specific
release/manifest version id it reviews — **never inferred from timestamp proximity or "most recent
row," an explicit foreign key**, exactly as the ticket requires. **(D) unsupported for MVP** remains a
live, non-technical alternative to (C) — see §16 below; it is not technically inferior, it is a scope
decision.

### 7 — Pilot recommendation (ADR-045 Question 5)

Not decided here — DH-EX-012A's own instruction stands: this must not be answered by timeline
pressure. **Technical recommendation, explicitly mine, pending Liban, not a resolution:** Option 2
(a documented facility fallback entirely outside DHealth) accepts that DHealth's own record can become
known-wrong with no in-system trace — a real compromise of the core trust proposition of "an
interoperable diagnostic network" whose entire value is a reconstructable, trustworthy record.
Options 1 (block the pilot) and 3 (implement amendment first) both preserve that trust proposition;
between them, the cost is pilot timing, not correctness. I recommend against Option 2 specifically,
without picking between 1 and 3 — that tradeoff (how much amendment capability is minimally
sufficient before a real-patient pilot, versus delaying the pilot further) is a product-timeline
decision only Liban can make.

### 8 — Amendment roles (§C/§D)

**Preparation: `LAB_TECH`**, matching entry and pre-release-correction authority exactly (no new
role). **Release: `LAB_RESULT_RELEASER`**, explicitly required — unlike pre-release correction (which
deliberately does not require it, per ADR-045 Question 2's own resolution), an amendment changes
content a provider and clinician have already acted on, which is a materially higher-consequence
event than a pre-release typo fix. Same human may hold both roles simultaneously, consistent with
ADR-044's Question 2 resolution, unless Liban states a new reason to require a second person — not
invented here. **Both authorization primitives already exist** (`_require_lab_tech`,
`_require_lab_result_releaser`) — this requires no new role, only a new call sequence.

### 9 — Release/version binding (§B)

**Exact recommended mechanism:** Model 3's release-version + manifest, per §4/§5 above — each release
(original or amendment) is one immutable row naming its own effective `(item, observation_id)` set.
**Explicitly rejected:** duplicating clinical scalar values into the manifest for convenience — the
manifest binds by `LabObservation.id` only; the value itself is read through that id, exactly once,
never copied. This directly satisfies the ticket's own instruction not to duplicate scalar values
when an immutable id can bind content safely.

### 10 — Provider display (§G)

**Option B, recommended:** the latest release-version shown prominently, explicitly labeled
`AMENDED` once more than one release-version exists for an order, with the prior release-version(s)
accessible as history via the same order. Option A (latest-only, no flag) is expressly forbidden by
the ticket's own rule against silently replacing previously-shown content. Option C (every release as
a fully separate equal report) is the most complete but the largest response-shape and UX cost, with
no field evidence yet that it is required rather than merely thorough.

### 11 — Legacy behavior (§L)

**Explicit DH-EX-012 releases:** amend cleanly under Model 3 — `LabResultRelease` becomes,
implicitly, "release-version 1," untouched, with every subsequent amendment release adding version
2, 3, ... alongside it, never replacing it.

**Grandfathered `COMPLETED` orders with no `LabResultRelease` row at all:** no version-1 anchor
exists to extend from. **Recorded as an explicit Human Decision, not resolved here** — fabricating a
synthetic "release-version 1" for a grandfathered order would violate this codebase's own established
no-retroactive-fabrication discipline (`LegacyCompletedOrderHasNoRelease`'s entire reason for
existing). The safe default, evaluated not adopted, is that grandfathered orders remain outside
amendment support entirely until Liban decides otherwise.

### 12 — ResultDelivery interaction (§J)

**Required for data correctness: none.** `ResultDelivery` already carries no reference to result
content of any kind (confirmed: no code path ever sets it based on a value) — amendment does not
make its existing shape any less correct than it already is. **Deferable to a later notification-UX
ticket:** distinguishing "delivered the original" from "delivered an amendment" as a *notification*
concept is real future work, but is not required for DHealth's own record to remain accurate — the
release-version manifest already carries that fact independently of whatever `ResultDelivery`
eventually represents.

### 13 — Multi-observation panels (§M)

An amendment episode may correct one or several observations, across one or several items, in a
single episode. **Unchanged observations are never duplicated** — a release-version manifest simply
references their existing, still-effective observation ids directly; only genuinely corrected items
gain a new observation id in the new manifest. This is the same mechanism that makes exact
reconstruction of any release trivial (§9) and multi-observation-safe by construction, with zero new
schema needed beyond the manifest table itself.

### 14 — Multiple sequential amendments (§N)

The architecture must not, and under Model 3 does not, assume only one amendment can occur: each
release (original or amendment) produces one more manifest row in the same append-only chain,
indexed by an order-scoped sequence number. Release #3 (a second amendment) reconstructs identically
to release #2 — a new manifest, referencing whichever observation ids are effective for each item at
that moment, some unchanged since release #1, some now on their second correction.

### 15 — Concurrency and locking invariants for future implementation (§O)

Described as required invariants, no runtime code written:

- **Two amendment preparations racing on the same observation:** if §3's resolution (a) is chosen,
  identical to DH-EX-013's own existing defense — the (still-`COMPLETED`-but-lockable) `LabOrder`
  row's `FOR UPDATE` lock serializes attempts, with the existing `UNIQUE` constraint as backstop.
- **Two releasers attempting to release the same amendment episode:** must mirror
  `LabResultReleaseRepository.release`'s own existing idempotent-replay pattern exactly — a second
  release attempt on an already-released episode returns the existing version/manifest
  (`created=False`), never creates two release-versions for one episode.
- **Amendment release vs. provider read:** the provider-read path must stop reading live "current
  effective" state (§2's risk) and instead join through the order's **latest release-version
  manifest** — a real, disclosed query-shape change any future implementation must make, not an
  optional refinement.
- **A second amendment beginning while a first is unreleased:** recommended invariant — at most one
  open (unreleased) amendment episode per order at a time, enforced the same way `LabResultRelease`
  already enforces "at most one release per order" (a `UNIQUE`-backed constraint on an
  "open episode" marker), so no ambiguity ever exists about which pending corrections belong to
  which future release.
- **Re-review race:** structurally eliminated by §6's binding — each `DoctorReview` row keyed to its
  own release-version id can never collide with another version's review row; `_mark_in_session`'s
  existing `FOR UPDATE` + terminal-status check applies unchanged, once per version.

### 16 — Security/tenancy findings (§ security audit)

No new authorization surface is required beyond what §8 names: `_require_lab_tech` for preparation,
`_require_lab_result_releaser` for release, exactly mirroring today's entry/release split. A future
versioned-review endpoint needs the identical lab-ownership/non-enumerating-404 discipline every
existing lab-scoped route already applies (`_lock_order_and_item_for_entry`'s
`selection.lab_organization_id` check is the exact precedent). Patient-bearer access requires no new
boundary — confirmed unaffected (§1). Audit vocabulary: a future amendment implementation needs new
`event_type` values (e.g. an amendment-prepared, amendment-released, amendment-review-recorded
family — **illustrative only, not adopted, real naming is an implementation-ticket decision**),
each requiring the identical `CHECK`-widen-plus-downgrade-guard migration pattern DH-EX-013's
`0017_observation_revisions` already established; `resource_type` needs no new value (§1).

### 17 — Performance/query findings

Model 3's manifest read is a single join (`release_version → manifest rows → LabObservation`,
batched exactly as `get_for_lab`'s existing two/three-query pattern already batches observations
today) — one query for the release-version, one batched query for its manifest-plus-observations,
regardless of item or observation count. No per-item or per-observation query is introduced by this
design; the existing O(1)-queries-per-request discipline (`get_for_item`/`list_for_order`/
`get_for_lab`) is preserved by construction, not merely hoped for.

### 18 — Amendment reason (§E)

`Human Decision / Field Workflow Input Required.` Pre-release correction requires no reason
(ADR-045 Question 3, Option C, already accepted). Post-release amendment is evaluated as a
*materially different* event — clinical content has already been exposed to a provider and possibly
acted on by a clinician — but `docs/MVP-1.0.md` §13's existing "no free-text clinical notes" boundary
applies identically to an amendment reason as it did to a correction reason, and closed reason codes
would constitute real laboratory-workflow content this ADR is not positioned to invent (the identical
constraint ADR-045 Question 3 already named for pre-release correction). Options recorded, none
adopted: no reason; free-text (in tension with §13); closed reason code (requires real laboratory
input to design the code list itself); optional non-clinical annotation (an operational/administrative
note only, e.g. "specimen re-run," never a clinical judgment — evaluated as the least-risky option if
a reason is wanted at all, not adopted).

### 19 — Human Decisions required from Liban, smallest possible numbered list

1. **ADR-045 Question 4 — `DoctorReview` after amendment:** Option C (recommended, §6) vs. Option D
   (unsupported for MVP) — Options A/B are evaluated and not recommended.
2. **ADR-045 Question 5 — pilot sufficiency:** Option 1, 2, or 3 (§7) — recommendation given against
   Option 2 only, not a full resolution.
3. **§3 — amendment preparation semantics:** immediate-permanent insert (a, recommended) vs. a
   staged/discardable draft (b, requires a future schema change to the existing `UNIQUE` constraint).
4. **§18 — amendment reason:** none / free-text (in tension with an existing decision) / closed
   reason code (requires real laboratory input) / optional non-clinical annotation.
5. **§11 — grandfathered `COMPLETED` orders with no `LabResultRelease`:** excluded from amendment
   support until further decided (recommended default) vs. some other explicit treatment.
6. **Whether to proceed with implementation at all**, and if so, whether Model 3 (§5, recommended)
   is accepted as the architecture DH-EX-014 (a future implementation ticket) builds against.

**Full audit, evidence, and reasoning:** `PROJECT_STATE.md`'s DH-EX-014D entry.

**ADR-045's Questions 4–5 remain OPEN**, unedited by this entry — this ADR is the linked decision
packet answering them, not a revision of ADR-045 itself.

---

**Decisions accepted by Liban — 2026-09-14 (DH-EX-014A).** All six items in §19's numbered list
above are resolved. This section is an append, not a revision — the RECOMMENDED packet above
(architecture options, evidence, reasoning) stands unchanged as the record of what was evaluated
and why; this is the record of what was chosen. DH-EX-014A implements only the immutable
release-version/manifest foundation and versioned-review foundation §19 item 6 accepts below — it
does **not** implement post-release amendment preparation or release, which remains DH-EX-014B.

**Decision 1 (§19 item 6) — architecture: Model 3 accepted.** Amendment episode + immutable
release manifest, exactly as recommended in §4/§5. Implemented in this ticket: only the manifest
and version foundation (`LabResultReleaseVersion`, `LabResultReleaseManifestEntry`) — the amendment
episode itself, and any preparation/release workflow built on top of it, is explicitly out of scope
here and remains DH-EX-014B's job.

**Decision 2 (§19 item 1) — ADR-045 Question 4: Option C accepted.** Versioned review history.
Every released result version gets its own review obligation, bound by explicit
`release_version_id`, never inferred from timestamp or "most recent row." A historical review is
never reset, rewritten, reassigned to a different release, or treated as covering content it did
not see. Implemented in this ticket for the only version that exists (Version 1); DH-EX-014B will
create the second review row for Version 2 using the identical mechanism, unchanged.

**Decision 3 (§19 item 2) — ADR-045 Question 5: post-release amendment required before the first
real-patient pilot.** Of the three options §7 evaluated, this is Option 3 (implement amendment
before pilot) — not Option 1 (block the pilot with no further work) or Option 2 (a manual
out-of-system fallback, which this decision explicitly does not accept). **DH-EX-014A alone does
not satisfy this requirement** — it is the versioning foundation only, not the amendment mutation
workflow. DH-EX-014B remains a real-patient-pilot blocker until it exists and is qualified.

**Decision 4 (§19 item 3) — amendment preparation semantics: immediate/permanent insert accepted
for DH-EX-014B.** Option (a) from §3, not option (b) — no discardable-draft system for MVP. A
future amendment's observation correction will be entered exactly like a pre-release correction:
the moment it is entered, it is a real, permanent revision. This requires no relaxation of the
existing `UNIQUE` constraint on `supersedes_observation_id`. **Not implemented in this ticket** —
recorded here as the accepted design DH-EX-014B must build against, per this ticket's own explicit
scope boundary ("do not implement amendment preparation in this ticket").

**Decision 5 (§19 item 4) — amendment reason: no mandatory reason accepted for MVP.** No reason
codes, no free-text clinical reason, and no amendment taxonomy were invented to reach this
decision — matching ADR-045 Question 3's own precedent for pre-release correction. **Not
implemented in this ticket** (no amendment-preparation endpoint exists yet to carry a reason field
either way) — recorded as the accepted design for DH-EX-014B.

**Decision 6 (§19 item 5) — grandfathered `COMPLETED`-without-`LabResultRelease` orders: excluded
from amendment support in MVP, the recommended default from §11.** No fabricated release, version,
manifest, or releaser is ever created for such an order — it retains exactly its existing
grandfather-rule behavior (readable, reviewable, never amendment-eligible), unchanged by this
ticket or by whatever DH-EX-014B eventually builds.

**Implemented exactly as this ADR's own audit evidence recommended, now binding for the foundation,
not merely recommended:**

- `LabResultRelease` remains completely untouched, immutable, exactly as ADR-044 shipped it — no
  mutation, no reinterpretation, no "amended" field added to it.
- A new `LabResultReleaseVersion` row (`version_number`, `UNIQUE(lab_order_id, version_number)`)
  binds Version 1 to the existing `LabResultRelease` row via `lab_result_release_id` (itself
  database-`UNIQUE`) — enforced by a `CHECK` constraint, not merely application code.
- A new `LabResultReleaseManifestEntry` row per effective observation names the release's exact
  `(item, observation_id)` set, binding by immutable id only — no clinical scalar value duplicated.
- A successful original release atomically creates the release, its Version 1, its full manifest,
  and binds the order's existing `PENDING` review to that version — all four commit together or
  none do, verified by a real negative control (see `PROJECT_STATE.md`'s DH-EX-014A entry).
- Provider reads of an explicit-release order now derive content from the release-version manifest,
  never from live current-effective state — closing exactly the naive-mutation risk this ADR's own
  §2 identified. A grandfathered order's provider read is unaffected, using its unchanged legacy
  path.
- `DoctorReview.lab_order_id` is no longer database-unique; a new `release_version_id` column plus
  two database constraints (a plain `UNIQUE` on the version id; a genuine partial unique index on
  `lab_order_id` scoped to the one legacy/unbound row) enforce "at most one review per version, at
  most one unbound legacy row per order" — a real migration (`0018_exchange_release_versions`), not
  an application-code-only change, exactly as §6 anticipated.
- Original-release backfill for every pre-existing `LabResultRelease` row is reconstruction from a
  proven structural invariant (every observation-mutating code path is gated on `IN_PROGRESS`, so a
  released order's effective observations cannot have changed since release), never a fabrication —
  proven and cited in the migration's own docstring and `PROJECT_STATE.md`'s DH-EX-014A entry, not
  merely asserted.
- Real-PostgreSQL qualification (not merely SQLite) exercised: an original-release race (exactly
  one version/manifest/bound review survives), the existing correction-vs-release race re-qualified
  against the new manifest, a concurrent-reader-never-observes-a-partial-manifest proof, and a
  review race preserving the existing one-terminal-disposition invariant.
- Downgrade of migration `0018_exchange_release_versions` fails closed if any release version with
  `version_number > 1` exists, or if any order carries more than one `DoctorReview` row — both real,
  future DH-EX-014B history the pre-0018 schema cannot represent — verified against real PostgreSQL,
  including a genuine guard-removal demonstration showing the exact silent semantic loss (an
  amendment's entire existence becomes untraceable, and the surviving review status silently
  appears to describe content it may never have reviewed).

**Implementation record:** `PROJECT_STATE.md`'s DH-EX-014A entry (migration
`0018_exchange_release_versions`, `exchange/models.py`'s `LabResultReleaseVersion`/
`LabResultReleaseManifestEntry`/updated `DoctorReview`, `exchange/results.py`'s extended
`LabResultReleaseRepository.release`/new manifest and version-history reads/version-aware
`DoctorReviewRepository`, the new release-version-history endpoint, real-PostgreSQL qualification,
and full negative-control evidence).

**Status: §19 items 1, 2, 3, 4, 5, and 6 are now RESOLVED** for the foundation scope described
above. **DH-EX-014B (amendment preparation and release) remains required and remains a
real-patient-pilot blocker per Decision 3** — recorded here rather than editing the header or the
RECOMMENDED packet above, so the original evaluated architecture remains legible as written at the
time it was proposed.

### Accepted decisions (2026-09-15 DH-EX-014B) — post-release amendment preparation/release under Model 3

**Date:** 2026-09-15 · **Status:** ACCEPTED AND IMPLEMENTED — Model 3 post-release amendment preparation and release workflow is closed · **Ticket:** DH-EX-014B.

Implements the post-release amendment preparation and release workflow designed and accepted in ADR-048 Model 3 (§19 items 1–6), satisfying the real-patient pilot prerequisite established in Decision 3.

**Accepted implementation specifics:**
- **Table and schema naming:** table `exchange_amendments` with columns `id`, `lab_order_id`, `lab_organization_id`, `base_release_version_id`, `started_by_identity_id`, `started_at`, `released_by_identity_id`, `released_at`, `status` (`PREPARING`, `RELEASED`).
- **Zero reason requirement:** zero amendment reason column in schema, zero reason field in API request payload, adhering to Decision 5 (no reason taxonomy or required justification invented for MVP).
- **One active preparation episode per order:** enforced at database level via partial unique index `uq_exchange_amendments_one_preparing_per_order` on `(lab_order_id)` WHERE `status = 'PREPARING'`.
- **Strict role separation:** `LAB_TECH` starts amendments (`POST .../amendments`) and corrects observations (`POST .../amendments/{id}/items/{item_id}/observations/{obs_id}/correct`). `LAB_RESULT_RELEASER` releases amendments (`POST .../amendments/{id}/release`). Same human may hold both roles, but role boundaries are enforced fail-closed (403 Forbidden).
- **Lineage integrity defense:** `AmendmentReleaseIntegrityError` (HTTP 409 Conflict) prevents releasing an amendment if an effective observation at release time differs from the base release manifest but is not attributable to the amendment being released.
- **Provider release-version history query scaling:** single joined query with `DoctorReview` on `release_version_id == version.id` (`get_release_version_history_with_reviews`) and batch fetch (`get_for_release_versions`), eliminating N+1 query scaling (1 SELECT query for history with reviews; 7 SELECTs / 15 total statements for provider API endpoint, constant across 1 vs 4+ versions).
- **Exact-version provider reads and reviews:** `GET .../release-versions/{v}` returns the exact immutable manifest and review status for that version. Unversioned review mutations reject with `ExplicitReviewVersionRequired` once multiple release versions exist.
- **Migration `0019_exchange_amendments` downgrade safety:** downgrade fails closed if any `exchange_amendments` row exists (`DowngradeBlockedByAmendmentHistory`) or if any amendment audit event exists (`DowngradeBlockedByAmendmentAuditVocabulary`). Clean round-trip downgrade/re-upgrade verified against real PostgreSQL.
- **All 12 negative controls verified:** NC 1 to NC 12 individually tested with red failure and restored green.

### Dated clarification (2026-09-18, DH-CLINIC-007) — Native Clinic review UX targets the exact displayed version, never "latest"

**Status:** clarification, not a revision — the RECOMMENDED/ACCEPTED packets above stand unchanged.
DH-CLINIC-007 built no new backend review-mutation or amendment machinery; it exposed the
version-specific review contract ADR-048/DH-EX-014A/DH-EX-014B already established through a
provider-scoped operational UI (a review queue and version-aware detail screen) for the first time.
This entry records the resulting UX-facing operating rule explicitly, since ADR-048 itself never
had cause to state it in UI terms:

**Native Clinic review actions target the exact immutable release version whose content was
displayed; a new amendment creates a separate pending review obligation and never inherits the
prior version's disposition.** Concretely: the Clinic frontend always loads a version's exact
manifest (`GET .../release-versions/{release_version_id}`) before offering any review disposition
control for it, and always calls the version-specific review route
(`POST .../release-versions/{release_version_id}/review/...`) rather than the legacy order-only
route whenever any release version exists for the order — the legacy route remains reachable only
for a genuinely grandfathered `COMPLETED`-without-release-version order. Reviewing an older version
after a newer amendment has already been released is permitted and affects only that older
version's own review row; the newer version's review remains independently `PENDING` until
someone reviews it too. No third review status, clinical-urgency ranking, or follow-up-task/
scheduling/contact mechanism was added — `FOLLOW_UP_REQUIRED` continues to mean only that a
clinician recorded follow-up as necessary, nothing about scheduling, contact, or completion.

**Implementation record:** `PROJECT_STATE.md`'s DH-CLINIC-007 entry (the provider-scoped review
queue endpoint, the rewritten `ClinicResultsPage.tsx`, real-PostgreSQL PG-R2 qualification, and full
evidence).

### Dated addendum (2026-09-18, DH-LAB-005) — amendment release gains the identical fingerprint gate the original release already had

**Status:** addendum, not a revision — the RECOMMENDED/ACCEPTED packets above stand unchanged. This
closes a genuine safety gap DH-EX-014B's own implementation left open: `release_amendment` had no
mechanism analogous to DH-LAB-004's original `release()` fingerprint check, so a releaser could
release an amendment whose proposed effective set had changed since they last inspected it — the
exact "release a set the releaser never saw" failure ADR-044/DH-LAB-004 already closed for Version
1, left open for Version 2+.

`AmendmentInspectionRecord` now carries `result_fingerprint` — computed by the identical
`compute_result_fingerprint` algorithm DH-LAB-004 already established (never a second
implementation), over the amendment's proposed effective set while `PREPARING`, or its own
immutable manifest once `RELEASED`. `release_amendment` now requires
`expected_result_fingerprint`; the check happens inside the existing `LabOrder FOR UPDATE` lock,
after the amendment's own existing missing-count/lineage checks but before any release-version row
is created, raising the already-existing `StaleResultFingerprint` (never a new exception) on
mismatch. Replay of an already-`RELEASED` amendment stays unconditionally safe regardless of the
fingerprint given, matching `release()`'s own established replay discipline exactly. This ticket
also added, reusing DH-LAB-004's own least-privilege-queue convention, a dedicated
`GET /organizations/{lab_id}/amendment-release-queue` (`LAB_RESULT_RELEASER` only, never lists an
amendment with zero revisions) and a lab-facing `GET .../release-versions/{release_version_id}`
exact-content read mirroring the provider-facing one this ADR's own DH-EX-014A/014B sections
already established.

**Implementation record:** `PROJECT_STATE.md`'s DH-LAB-005 entry (real-PostgreSQL qualification of
the amendment-correction-vs-amendment-release and two-releasers-racing-the-same-amendment races,
and full evidence).

## ADR-049 — ACCEPTED / IMPLEMENTED / CI-QUALIFIED (2026-09-16, DH-CLINIC-003C): Option A — a backend-owned durable `LabOrderIntent` is the sole cross-reload recovery mechanism for native Clinic lab ordering

**Date raised:** 2026-09-15/16 (DH-CLINIC-003B's own "Human Decision Gate: Cross-Reload Exact
Replay Architecture," `PROJECT_STATE.md`, which ended **STOPPED AT HUMAN DECISION GATE: YES**) ·
**Status:** ACCEPTED, IMPLEMENTED, CI-QUALIFIED · **Ticket:** DH-CLINIC-003C.

**Decided by:** Liban — **Option A**, selected during DH-CLINIC-003C review, not the primary
agent's recommended Option C, closing DH-CLINIC-003B's own decision gate. Implementation history:
commit `91e752d82d5f398bba2ede964dc3822b2468fd1e` ("Implement backend-owned `LabOrderIntent`
repository with idempotency and commit functionality") is the first commit building Option A;
cited here as implementation evidence only, not as proof of who made the architectural call — git
author metadata is not a record of decision authorship.

### The three options, exactly as DH-CLINIC-003B originally framed them

1. **Option A: Backend-owned intent (Draft Order Resource).** Before the final mutation, the
   client creates a draft order resource on the backend. *Pros:* zero clinical data stored in the
   browser; seamless resume across devices/browsers/reloads without re-selecting tests. *Cons:* a
   new database table/migration, API endpoints, lifecycle management.
2. **Option B: Scoped browser storage with test IDs.** Store `catalog_entry_ids` alongside
   technical metadata in `localStorage`/`sessionStorage`. *Pros:* zero backend schema changes,
   automatic payload restoration. *Cons:* places diagnostic test selections into browser storage on
   potentially shared clinic workstation PCs, raising privacy/governance concerns under ADR-043 —
   explicitly recorded as **do not adopt without explicit sign-off from Liban**.
3. **Option C: Fail-closed without payload replay across reload** (the DH-CLINIC-003B baseline
   actually shipped at the time). Browser stores strictly technical non-PHI metadata; an uncommitted
   reload blocks new orders and requires re-selecting the same tests under the frozen idempotency
   key. *Pros:* zero migrations, 100% compliant with zero-clinical-data-in-storage, duplicate-safe
   via the database unique constraint. *Cons:* the clinician must re-check test checkboxes after an
   uncommitted reload.

**Clarification on what was actually qualified, versus the original framing above.** Option A's
original description (as proposed in the DH-CLINIC-003B gate) said "seamless resume across
devices/browsers/reloads." What DH-CLINIC-003C actually implements and has qualified is narrower:
the backend intent itself is genuinely durable and device-independent, but the *current* UI
recovery mechanism discovers it only through an opaque technical pointer (idempotency key,
optionally the intent id) that the browser persists locally. Recovery is therefore qualified for
reload, restart, and provider/patient navigation on the **same browser profile** that still holds
that local pointer — not for opening the same unresolved order from a different device or browser.
Nothing in the backend model prevents a future cross-device discovery workflow (e.g., listing a
clinician's own open intents server-side), but no such workflow has been built or qualified by this
ADR; the durable-intent architecture leaves room for it without requiring one now.

### What was decided, and why B and C were not selected

**Option A selected and built.** The browser is **not** the source of truth for clinical order
content once a durable intent exists. The backend owns, immutably from the moment of registration:
provider organization, patient reference, the practitioner (server-derived from the authenticated
clinician, never client-supplied), the idempotency key, the exact diagnostic catalog selection, and
an immutable snapshot of each selected catalog entry's code/display name at selection time — so a
later catalog edit can never retroactively change what an already-registered intent says it asked
for. Browser persistent storage after this ADR contains only an opaque technical recovery identity
(§ below); it is never the record of which tests were requested.

**Option B was not adopted.** The gate's own recorded risk — diagnostic test selections sitting in
`localStorage` on a potentially shared clinic workstation — was never granted the explicit sign-off
the gate required. Option A does not request an exception to that risk; it removes the risk
category structurally by never needing test identifiers in the browser at all, committed and
uncommitted alike.

**Option C was superseded, for a real limitation, not repudiated as broken.** It was a legitimate,
shipped MVP baseline that met the duplicate-safety and browser-privacy bar of its time: genuinely
duplicate-safe via the database unique constraint, zero-migration, and fully compliant with the
no-clinical-data-in-storage rule. Its cost was not merely a UI inconvenience — it intentionally
required the clinician to manually re-select services after an unresolved full reload, which meant
Option C could not durably preserve or exactly replay the *original* diagnostic selection when no
authoritative server order yet existed. Concretely: if the first request had not committed before
the reload, the clinician could reselect a different service set, and that altered set — not the
one originally intended — could become the first order actually created under the retained
idempotency key. The key stayed duplicate-safe; the clinical content it was attached to could
still drift. Option A was selected specifically to close this exact-replay gap: once a durable
intent exists, the backend's own frozen snapshot is what reload ever replays, never a
browser-side reconstruction.

### Bidirectional ownership invariant

For every order created through DH-CLINIC-003C's intent path (including the legacy endpoint, which
converges through the same reservation — see below):

- `LabOrder.intent_id == LabOrderIntent.id`
- `LabOrderIntent.committed_lab_order_id == LabOrder.id`

Both pointers are written and checked inside the same database transaction
(`order_intents.py`'s `LabOrderIntentRepository.commit`); a mismatch raises `IdempotencyKeyConflict`
rather than allowing one-sided provenance. `exchange_lab_orders.intent_id` is a nullable, `UNIQUE`
foreign key added by migration `0021_order_intent_ownership` (see the migration-history note
below) — nullable **solely** because pre-003C `LabOrder` rows legitimately predate this model and
have no intent to point to. **No backfill was performed or attempted** for those historical rows;
`intent_id = NULL` on an old order is the honest, permanent, correct state, not a gap to be closed
later.

### Legacy `/orders` convergence

The legacy provider order-creation endpoint (`POST .../orders`) does not own a separate creation
path for *new* orders. Every new provider `LabOrder` — legacy or native — is created by first
acquiring (or consuming an already-existing) `LabOrderIntent` reservation for that
`(provider_organization_id, idempotency_key)` pair, then committing it; the intent table's own
unique constraint is the single owner under concurrent legacy and native-Clinic requests for the
same key. The legacy endpoint remains, for backward compatibility, but shares the durable intent
ownership model completely — it is not a second, independent way to create a *new* order.

**Historical (pre-003C, `intent_id IS NULL`) exact replay is a narrow, explicit exception,
checked before any new intent reservation is attempted.** Final-review testing found that without
this exception, an exact retry of a historical order regressed from its original 200-idempotent
replay to a `409` — because the legacy route, finding no reservation, would create a *new* intent,
and that new intent's commit would then find the historical order already sitting under the same
key with `intent_id IS NULL`, which the bidirectional-ownership check correctly (but wrongly, for
this one case) treats as a foreign claim. The corrected legacy route looks up an existing
`intent_id IS NULL` order under the exact same key *before* reserving anything: an exact canonical
match (patient, practitioner, service set) replays it unchanged — `200`, the same historical
order id, no new `LabOrderIntent`, no new audit row, `intent_id` still `NULL` — and a mismatch
still conflicts (`409`), exactly as the pre-003C endpoint always behaved. The historical row is
never claimed into a new intent either way. This exception is scoped to the legacy endpoint only;
native intent registration/commit is unaffected and still fails closed if it ever collides with a
historical key.

### Practitioner ownership

The practitioner on an intent is always derived server-side from the authenticated clinician
(`principal.identity_id` → `PractitionerReference.get_or_create`); the request schema
(`LabOrderIntentCreateRequest`, `extra="forbid"`) has no `practitioner_reference_id` field at all,
so the browser has no channel to supply or override it — confirmed both structurally and by a
permanent regression test that a spoofed field in the request body is rejected (`422`). Intent
retrieval (`GET .../order-intents/{id}` and `GET .../order-intents/by-idempotency-key/{key}`) is
restricted to the original authoring clinician: a different clinician at the **same** provider
receives an anti-enumerating `404`, not `403` — the response never discloses that an intent with
that id exists at all. The commit endpoint's own status contract differs — a wrong-clinician commit
attempt returns `403 Forbidden`, not `404` — and this ADR does not change that contract. The two
endpoints are simply not required to share a status code for a wrong-clinician caller.

### Recovery behavior

- An unresolved durable intent survives a full browser reload/restart; there is no destructive TTL
  of any kind (verified by reading the complete `pendingOrderStorage.ts` module — no expiry code
  exists to remove).
- There is no "Option C"-style manual catalog reconstruction once a durable intent exists: on
  reload, the backend intent's own item snapshots restore the exact original selection, read-only.
- A recovered intent whose provider, patient, idempotency key, or (when the browser already knows
  it) intent id does not exactly match the current scope fails closed — the recovery record is
  retained, but nothing is rendered or committed from it.
- A transient lookup failure does not reopen service selection as if this were an ordinary new
  order.
- A definitive commit-step error (e.g. `422`, `403`) after successful registration retains the
  durable recovery identity — it is not treated as a fresh, unregistered attempt.
- A stale asynchronous response for one patient/provider scope cannot attach its content to a
  different scope the clinician has since switched to.

**A real defect was found and fixed during the coverage-closure pass that verified the property
above.** `reconcilingStoredOrder` (a UI-only loading flag, not a data-integrity mechanism) was
component-wide state whose `true` was set synchronously for whichever patient/provider was open,
but whose `false` reset only ran inside the same staleness-guarded async callback that correctly
blocks a stale response from attaching content — so switching scope before that callback resolved
left the flag stuck `true`, disabling "Nouvel ordre de laboratoire" for an unrelated patient with no
pending order of her own. Fixed by resetting the flag synchronously at the top of the recovery
effect on every scope change, with the existing logic re-arming it immediately if the new scope
genuinely has something to reconcile. No data ever attached to the wrong scope; this was an
availability/usability defect, not a correctness-of-clinical-content one. Permanent regression
tests cover both the original bug and the requirement that the fix not fail reconciliation open for
a scope that is genuinely still pending.

### Persistent browser boundary (verified against `pendingOrderStorage.ts` as shipped)

Exactly: `user_id`, `provider_id`, `patient_reference_id`, `idempotency_key`, `created_at`, and two
optional fields — `intent_id` (once the backend intent is known) and `confirmed_order_id` (once a
commit has succeeded). Never: catalog entry ids, service codes, service display names, patient
display name or contact details, result values, or any client-supplied practitioner value. A
permanent test reads the raw serialized `localStorage` string directly (not merely the typed
getter, which would silently drop an unknown field) and asserts this exact key allowlist.

### Concurrency, qualified specifically (not claimed generally race-free)

Real PostgreSQL qualification (`test_dhex003c_postgres_qualification.py`, CI's disposable
`postgres:16-alpine` service, never a facility database) covers exactly these scenarios, no more:
concurrent commits of the same intent converge on exactly one `LabOrder` (PG2); native
registration racing the legacy endpoint with the same canonical payload converges (PG3); the same
race with a mismatched payload correctly conflicts (PG4); native commit racing the legacy endpoint
with the same payload converges (PG5); that race with a mismatch correctly conflicts (PG6). A
same-session commit-replay regression (reading a just-committed order through the locking
transaction, not an independent session that cannot yet see it) is also qualified. **One
negative-control finding is recorded honestly rather than turned into false evidence**: removing
the commit method's early "already committed" fast-path check did not, by itself, produce a
duplicate `LabOrder` — the `exchange_lab_orders` table's own `UNIQUE(provider_organization_id,
idempotency_key)` constraint independently converges the race regardless. This is legitimate
defense-in-depth, not a gap in the negative-control method; the fast path is a contention/latency
optimization on top of a schema-level guarantee, not the sole mechanism preventing duplication.

### Migration history

`0020_exchange_order_intents` (2026-09-16) introduced the `exchange_lab_order_intents` and
`exchange_lab_order_intent_items` tables. A required pre-implementation independent review found
that `LabOrder`'s ORM model already declared a nullable `intent_id` field that `0020` never added
to the database — an ORM/schema mismatch, not a design change. `0021_order_intent_ownership`
(same day) is the corrective migration: it adds `exchange_lab_orders.intent_id` (nullable, `UNIQUE`,
`FOREIGN KEY ... RESTRICT`) and its downgrade guard refuses to run while any intent-owned order
exists. This is the true, unedited sequence — `0020` is not described as having always been
correct, and no history was rewritten to hide the gap.

### Evidence

CI-qualified at commit `6619e08bf3b2fe87e5ab1f8c93594b64c41e8851` (run `35135484902`, all 9 jobs
green): adapter `851 passed, 98 skipped, 1 warning`; migration current == heads == expected
`0021_order_intent_ownership`; real PostgreSQL `5 passed` (PG2–PG6); UI `26 files / 366 tests`
passed, including 60 Clinic-specific tests; bundle secret scan clean. This is CI-qualified evidence
only — no real Keycloak/browser runtime qualification, no real-patient qualification, and no
deployment/pilot qualification is claimed by this ADR.

## ADR-050 — ACCEPTED / IMPLEMENTED / CONFIG-QUALIFIED (2026-09-17, DH-CLINIC-004A): native DHealth browser apps authenticate against the `dhealth-exchange` realm through a shared, public PKCE client; tokens stay in memory; requests are same-origin

**Date raised:** 2026-09-17, DH-CLINIC-004A · **Status:** ACCEPTED, IMPLEMENTED,
CONFIG-QUALIFIED · **Ticket:** DH-CLINIC-004A. **Not** real-Keycloak/browser-qualified — that is
DH-CLINIC-004B's explicit job; see the Evidence section below for exactly what "config-qualified"
means here and what it does not claim.

**Why this needed a decision, not just an implementation.** DH-CLINIC-003C (ADR-049) built the
durable-intent recovery model assuming an authenticated caller, but no native DHealth frontend
could actually authenticate as a real browser user in a deployment — `ClinicProvider` defaulted its
token to `null` and called `/me` anonymously regardless. Closing that gap touches five things no
prior ADR had decided: which realm a native browser app uses, what kind of OIDC client a browser is
safe to be, where the resulting token lives, how the browser reaches the Exchange API without a new
cross-origin dependency, and whether this is Clinic-specific or shared infrastructure DHealth Lab
must later reuse. All five are decided together here because they are one architecture, not five
independent choices.

### Decisions

1. **Same realm, new client — never a second identity provider.** Native DHealth browser apps
   authenticate against the existing `dhealth-exchange` Keycloak realm (DH-EX-008), not a new realm
   and not the reference-connector's `daher` realm. A new client, `dhealth-native-ui`, is added
   alongside the existing confidential `dhealth-exchange-api` client — additively; `dhealth-exchange-api`
   is unchanged and keeps its secret.
2. **Public client, Authorization Code + PKCE S256, no hand-rolled OAuth.** `dhealth-native-ui` is
   `publicClient: true`, carries no secret, has the standard flow enabled and implicit/direct-grant/
   service-account flows disabled, and enforces `pkce.code.challenge.method: S256` at the Keycloak
   client level (not merely relying on the browser adapter's own default). The official `keycloak-js`
   browser adapter is used end to end — no manual PKCE generation, code exchange, refresh protocol,
   or logout protocol. `redirectUris`/`webOrigins` are scoped to `https://app.${BASE_DOMAIN}` only,
   never a wildcard.
3. **Same audience, unmodified backend validation.** `dhealth-native-ui` carries the identical
   `exchange-api-audience` protocol mapper the confidential client already has, stamping
   `dhealth-exchange-api` into every access token regardless of which client issued it.
   `identity_auth.py`'s `TokenValidator` checks only `iss`/`aud`/signature, never `azp` — proven by a
   new unit test that mints a token with `azp: dhealth-native-ui` and confirms it validates
   unchanged. Zero backend code was touched by this ADR.
4. **Tokens live in memory only, never in `localStorage`/`IndexedDB`/an app-created cookie.** The
   shared auth layer (`ui/src/features/auth/exchange/`) holds its `keycloak-js` instance and the
   resulting access token in a React component's closure/state for the lifetime of that mounted
   provider. Unmounting it (switching to the reference-connector app) drops that state naturally —
   itself a deliberate consequence of decision 5's scoping, not a separate persistence mechanism.
   The existing `pendingOrderStorage.ts`/`pendingPatientStorage.ts` technical-recovery identities
   remain completely independent of this token; neither was touched.
5. **Same-origin `/exchange/v1/*`, not cross-origin + CORS.** `ExchangeClient`'s pre-existing default
   base path (`/exchange/v1`, relative) was designed for same-origin use; this ADR closes the routing
   gap that made it unreachable rather than reaching for CORS. `app.${BASE_DOMAIN}`'s Caddy vhost now
   proxies `/exchange/v1/*` to the adapter, path-scoped exactly like the dedicated `exchange.
   ${BASE_DOMAIN}` vhost, which remains untouched and still exists.
6. **Shared infrastructure, not Clinic-owned.** The OIDC session lives in `ExchangeAuthProvider`/
   `useExchangeAuth()` under `ui/src/features/auth/exchange/`, independent of any Clinic module.
   `ClinicApp`/`ClinicProvider` consume an already-authenticated `ExchangeClient` injected from
   outside; Clinic itself has zero OAuth/OIDC code. A future DHealth Lab frontend reuses this layer
   unchanged — wrap it in its own `ExchangeAuthProvider` and `ExchangeAuthGate`, exactly as
   `App.tsx` does for Clinic.

### What this explicitly does not do

- Does not weaken, replace, or bypass `identity_auth.py`'s issuer/audience/signature validation in
  any way — no backend file changed.
- Does not turn `dhealth-exchange-api` into a public client, does not remove its secret, does not
  merge it with `dhealth-native-ui`.
- Does not introduce CORS, a BFF session, or a cookie-based token transport.
- Does not touch the reference-connector's `daher` realm, its OpenMRS/OpenELIS login flow, or its
  own logout (`navigateToKeycloakEndSession`) — native Clinic logout is a separate code path that
  terminates only the `dhealth-exchange` session, and switching applications is explicitly not the
  same operation as logging out of either.
- Does not attempt real-Keycloak import/reconciliation for `dhealth-native-ui` on a deployment whose
  Keycloak database already has the `dhealth-exchange` realm imported — `docker-compose.yml`'s
  `--import-realm` flag refuses to overwrite an existing realm, so this client only appears
  automatically on a fresh Keycloak database. Reconciling it into an already-imported realm (the
  same class of problem `keycloak/reconcile-sso.sh` solves for the `daher` realm's clients, which
  deliberately never touches this realm) is unbuilt and is DH-CLINIC-004B's concern, not silently
  patched over here.

### Evidence

**CONFIG-QUALIFIED, not runtime-qualified.** Verified: the realm JSON's structure and security
attributes (CI's extended static check, run locally before push); the Caddy route's placement and
target (same method); `identity_auth.py` accepting a `dhealth-native-ui`-issued token (new adapter
unit test, real RSA-signed JWT, no network) — full adapter suite re-run green, **855 passed, 98
skipped**; the full UI test suite including new coverage for initialization states, single-flight/
fail-closed token refresh, no-anonymous-`/me`-before-auth, and logout/app-switch separation —
**29 files, 397 tests, all passing**. `git diff --check` clean; bundle secret scan clean including
the new `KEYCLOAK_EXCHANGE_API_CLIENT_SECRET` needle. See `PROJECT_STATE.md`'s DH-CLINIC-004A entry
for the exact qualifying commit and CI run id. **Not verified — explicitly DH-CLINIC-004B's job:** a
real Keycloak import of this realm file, a real browser OIDC round trip (redirect, login, PKCE
exchange, token acceptance), real-Keycloak session refresh/expiry behavior, and the
realm-reconciliation gap noted
above. No pilot-readiness or production-deployment claim follows from this ADR.

### 2026-09-17 dated overlay (DH-CLINIC-004B) — realm reconciliation is now real-Keycloak-qualified; browser E2E is not

DH-CLINIC-004B built `keycloak/reconcile-exchange.sh` (the reconciliation script this ADR's Evidence
section named as unbuilt) and ran it against the **real, pinned Keycloak image**
(`quay.io/keycloak/keycloak:26.7.1@sha256:f1f1f01e...c01c6`) in CI, not a mock or static-config
check. Two things follow, at different evidence levels — do not conflate them.

**Runtime-qualified.** Run `35210377720` (commit `7752bf9`, `workflow_dispatch`, Case A/fresh
realm): Keycloak imported both realms cleanly (`Realm 'dhealth-exchange' imported`, Keycloak's own
log), `keycloak-sso-config` and `keycloak-exchange-config` both `Exited (0)`, with the new
reconciler's own log line confirming it: `Keycloak Exchange clients reconciled: dhealth-native-ui
public PKCE browser client`. The realm-reconciliation gap this ADR flagged as unbuilt is now closed
and runtime-proven for the fresh-realm case. The upgrade case (adding the client to an
*already-imported* realm — this gap's actual scenario) is written and was scheduled in the same run
but never executed; see below.

**The real run also surfaced a genuine defect in this ADR's own artifact**, never caught by CI's
structural checks or the unit test cited above because neither validates against a real Keycloak
column-length constraint: `roles.realm[0].description` and `clients[1].description` in
`dhealth-exchange-realm.json` (this ADR's own file) were 327 and 320 characters — over Keycloak's
`VARCHAR(255)` limit on those columns. Against a real database this crashes the entire realm import
(`ERROR: value too long for type character varying(255)`), not just that one field, taking the
Keycloak process down with it. Fixed in commit `7752bf9`, meaning preserved, length re-scanned
across every committed realm-config JSON file. This is the clearest illustration yet of why this
ADR's Evidence section called itself CONFIG-QUALIFIED rather than qualified: config-level review had
already looked at both these fields twice (authoring, then the DH-EX-008 static-check extension) and
neither review could have caught this — only a real import could.

**Still not qualified — the actual browser round trip.** The same run's Case A stalled after
Keycloak/reconciliation succeeded: `openmrs-backend` and `openelis-webapp` (pre-existing
reference-connector services, unmodified by either ADR) never reached Docker `healthy` within the
run's 3600s wait window, which blocks Caddy from starting (`depends_on: openmrs-gateway`/
`openelis-frontend`), which blocks every `app.${BASE_DOMAIN}` vhost the Playwright suite needs. Case
B (upgrade path) was configured to run in the same workflow dispatch but was `skipped` once Case A
failed. This is consistent with, not a new instance contradicting, `.github/workflows/ci.yml`'s own
header note that full-stack boot is a documented ~50-minute-plus manual/fresh-VM procedure, not
something a shared CI runner is expected to complete quickly. No real browser has completed the
Authorization Code + PKCE round trip against this stack. See `PROJECT_STATE.md`'s DH-CLINIC-004B
entry for the full evidence and the honest completion status.

## ADR-051 — ACCEPTED / IMPLEMENTED (2026-09-17, DH-IDENTITY-001): provider-scoped Patient directory separated from source-identity PatientReference provenance

**Date raised:** 2026-09-17, DH-IDENTITY-001 · **Status:** ACCEPTED, IMPLEMENTED · **Ticket:**
DH-IDENTITY-001.

**Why this needed a decision, not just an implementation.** `PatientReference` (DH-EX-001) was
deliberately designed as an exact source-system identity/provenance binding, never a patient
record -- but native DHealth Clinic had no other identity primitive to build on, so it grew into a
de facto lightweight patient registry: the browser minted a UUID, called the binding endpoint, and
treated the resulting reference as "the patient." That was sufficient to bootstrap Clinic (DH-CLINIC-002A)
but is not a safe long-term identity architecture -- DH-CLINIC-005's wrong-patient-safe human
identity UX needs a real provider-scoped patient directory to consume, not a UUID the browser
invented for itself.

### Decisions

1. **`Patient` is a provider-scoped native patient-*directory* record** (`exchange_patients`) --
   display name, optional exact date of birth, optional paired contact. Never a national identity,
   never a cross-provider/global DHealth person registry, never an EMR, never a longitudinal
   medical record. Two different provider organizations may hold two separate `Patient` rows for
   the same real human, on purpose -- cross-provider identity resolution is out of Launch-1 scope.
2. **`PatientReference` keeps its exact, unmodified DH-EX-001 meaning**: the source-system
   identity/provenance binding. It is now explicitly *for* a `Patient` (`patient_id`, NOT NULL),
   never an overloaded stand-in for one. Its own uniqueness key, snapshot fields, and no-fuzzy-
   matching discipline are all unchanged.
3. **Many `PatientReference` rows may eventually point at one `Patient`** (`patient_id` indexed,
   deliberately not unique) -- schema-permitted, but no linking/merge API exists in this ticket
   (Decision 12). Every reference created by any code path in this ticket is 1:1 with its own new
   `Patient`; multi-reference linkage requires a future, separately-designed workflow.
4. **No fuzzy/demographic matching, anywhere, ever.** Two `Patient` rows with byte-identical
   `display_name`/`date_of_birth`/contact are always allowed to coexist. The only thing that ever
   links two records is an explicit `patient_id`, set once, atomically, at creation time -- never
   inferred later by comparing names, phones, or birth dates. Proven by test
   (`test_i6_same_demographics_new_key_creates_second_patient`,
   `test_l4_same_demographics_different_source_identity_do_not_auto_link`).
5. **Existing `PatientReference` rows backfill 1:1, never merged or grouped by similarity**
   (migration `0022_exchange_patient_directory`). 500 existing references become 500 new `Patient`
   rows, even where several clearly describe the same real person under different local ids --
   duplicates are preferable to an unapproved identity merge.
6. **The native server owns every new `Patient`'s id** -- the browser can never supply one
   (`PatientCreateRequest` rejects it at the schema layer, `extra="forbid"`).
7. **Native reference identity belongs to DHealth itself, not to "Clinic."** A natively-created
   Patient's own `PatientReference` uses `source_system="dhealth"`,
   `source_identity_system="urn:dhealth:patient-id"`, `source_identity_value=str(patient.id)` --
   deliberately not `"dhealth-clinic"`, so a future DHealth Lab frontend registering through the
   same endpoint carries the identical, already-shared namespace.
8. **Native registration is idempotent**, keyed on `(provider_organization_id, idempotency_key)`
   against an immutable `exchange_patient_registration_requests` fingerprint record -- exact replay
   returns the same Patient; a conflicting replay (same key, different content) is refused (`409`),
   never silently returns mismatched data. The same idempotency key, or identical demographics,
   under a *different* key, are both explicitly allowed to create a second, different Patient
   (Decision 4's direct consequence, not a bug).
9. **`PatientReference` remains the current `LabOrder` API's compatibility bridge.** Every
   existing order/result endpoint still takes a `patient_reference_id`; this ticket does not
   rationalize those APIs onto `patient_id`. `PatientDirectoryEntry.primary_patient_reference_id`
   is the explicit, documented transition bridge Clinic's frontend uses to keep calling them
   unchanged.
10. **Database-level tenant safety, not just an application check.** `PatientReference`'s
    composite foreign key `(patient_id, source_organization_id)` →
    `Patient(id, provider_organization_id)` makes a cross-provider reference-to-patient link a
    rejected write, proven against real PostgreSQL (SQLite does not enforce foreign keys by
    default) by `test_pg_composite_fk_rejects_cross_provider_reference_to_patient_link`.
11. **Global/national MPI is explicitly not Launch-1 scope** -- no cross-provider identity graph,
    no government identifier integration, no biometric matching, anywhere in this codebase.
12. **Demographic correction, reference-linking, and merge workflows are explicitly deferred.**
    No `PATCH`/`PUT` on `Patient`, no `POST .../merge`, no `POST .../references/{id}/attach` exist
    in this ticket -- each is a patient-safety action requiring its own authorization/audit/
    correction/undo/UX/governance contract this ticket does not design.

### What this explicitly does not do

- Does not touch `LabOrder`/`LabOrderItem`/results/release-version schemas or APIs in any way --
  every existing order/result endpoint is unchanged, still keyed on `patient_reference_id`.
- Does not remove, rename, or change the meaning of any existing `PatientReference` column or its
  own uniqueness constraint.
- Does not add sex, gender, race, nationality, religion, address, marital status, diagnosis,
  medical history, medications, or insurance fields -- identity architecture only, not a step
  toward a full EMR demographic record.
- Does not implement a patient merge/link API, duplicate-scoring, or any demographic-matching
  logic, temporarily or otherwise.
- Does not redesign the Orders or Results pages -- that is DH-CLINIC-005's explicit job, not
  combined into this ticket.

### Evidence

Real-Keycloak/browser runtime qualification is unrelated to this ticket and remains whatever
DH-CLINIC-004B's own status says (see its entry; this ticket does not change that claim). For
DH-IDENTITY-001 itself: adapter full suite, real PostgreSQL concurrency (PG1-PG8, composite-FK
rejection) and migration qualification (M1-M6, plus a real §41 downgrade-guard refusal), and UI
lint/typecheck/test/build. See `PROJECT_STATE.md`'s DH-IDENTITY-001 entry for exact commands, run
evidence, and counts.

### 2026-09-17 clarification (DH-CLINIC-005): the patient-safety UX rule this ADR's architecture exists to enforce

DH-CLINIC-005 consumed this ADR's `Patient`/`PatientReference` split to remove UUID fragments as
the primary way Native Clinic's Orders/Results screens identify a patient to a human. That work
surfaced two durable engineering rules this ADR's own architecture was always meant to imply, now
stated explicitly rather than left implicit:

- **Human-facing clinical workflows must identify patients using the provider `Patient` directory.**
  `PatientReference` identifiers (and their own `display_name`/contact snapshot fields) remain
  technical/provenance identifiers and must never be the primary way a clinician distinguishes one
  patient from another -- not in a list, not in a confirmation screen, not in an error message.
- **Clinical result/order screens must not render stale asynchronous content after patient/order/
  provider context changes.** A response that no longer corresponds to the currently selected
  order, patient, or active provider organization must never be assembled into what the clinician
  sees -- it is discarded, not rendered, regardless of arrival order.

Neither rule changes `Patient`/`PatientReference`'s own meaning or this ADR's 12 decisions above --
both were already entailed by "identity is provider-scoped and exact-identity is provenance, not
human identity" and by this codebase's existing tenant-scoping/anti-enumeration discipline. They are
recorded here, appended, because DH-CLINIC-005 is the ticket that first made them load-bearing UI
requirements rather than only a backend invariant. See `PROJECT_STATE.md`'s DH-CLINIC-005 entry for
the concurrency-race test evidence (RR1-RR7) this second rule rests on.

## ADR-052 — ACCEPTED / IMPLEMENTED (2026-09-18, DH-CLINIC-006): provider-assigned launch routing uses explicit provider-lab relationships, not marketplace discovery

**Date raised:** 2026-09-18, DH-CLINIC-006 · **Status:** ACCEPTED, IMPLEMENTED · **Ticket:**
DH-CLINIC-006.

**Why this needed a decision, not just an implementation.** DH-EX-010 built
`LabSelectionRepository.assign_by_provider` -- ADR-043's pre-authorized launch option (a), a
provider routing an order to a known lab with no price marketplace/consent step. It never answered
a prior question: *which* labs is a given provider actually configured to work with at all? Without
an explicit answer, the only concept already in the codebase that could plausibly fill that gap is
`LabDirectoryProfile.listed` -- the public marketplace-visibility flag -- and reaching for it would
silently convert "provider routes to a known partner" into "provider browses the public directory,"
reintroducing exactly the price-shopping/marketplace framing ADR-043 Law 1 forbids for this launch
path. This ADR records the concept that closes that gap instead.

### Decisions

1. **`ProviderLabRoute` (`exchange_provider_lab_routes`) is the configured provider-lab
   relationship** -- an explicit, platform-admin-created fact answering only "may this provider
   route orders to this DHealth lab organization?" It carries no lis_type/emr_type/api_url/
   transport/credential field of any kind; how an order physically reaches a lab's own production
   system is a future pilot-topology ticket's question, not this ADR's.
2. **Launch-1 route configuration is platform-admin-only.** No provider self-service network
   onboarding and no bilateral invitation/acceptance flow exist yet
   (`PUT /organizations/{provider_id}/lab-routes/{lab_id}`, platform-admin-gated, idempotent,
   audited). A provider or lab administering their own membership/roles cannot create or modify a
   route between themselves and anyone else.
3. **Clinic never browses the public marketplace to route an order.** The order-specific
   eligibility read (`GET .../orders/{order_id}/eligible-labs`) and the provider's own directory
   read (`GET .../lab-routes`) are both keyed on `ProviderLabRoute.active`, never on
   `LabDirectoryProfile.listed` -- structurally, not just by convention: neither query joins the
   marketplace listing table, and the Clinic-facing frontend client has no method that calls
   `GET /labs` or the marketplace lab-options endpoint at all.
4. **`listed` is irrelevant to provider-configured routing.** An unlisted (private, not publicly
   discoverable) lab with an active route remains a fully valid, fully assignable destination.
   Configuring a route to a lab never makes that lab publicly listed, and listing a lab never
   implies any provider is routed to it -- the two facts are independent by design, in both
   directions.
5. **`accepting_orders` remains the required, separately-tracked operational state.** A route can
   be active while the lab is not currently accepting orders (and vice versa); neither flag is ever
   auto-toggled from the other. Whole-order eligibility and assignment both require an active route
   *and* `accepting_orders=True` *and* an ACTIVE `LAB` organization -- three independent conditions,
   never conflated into one.
6. **`LabOffering` remains the sole capability-matrix source.** No test/catalog identifier is ever
   copied onto `ProviderLabRoute`; whether a lab can perform a given test is answered exactly as it
   already was, by that lab's own offerings.
7. **Price is never a prerequisite for provider-assigned routing** -- unchanged from DH-EX-010, and
   now proven again at the route layer: the eligibility read joins no `LabPrice` table, and a lab
   with zero price rows is fully eligible and fully assignable, with every item's
   `selected_price_amount`/`selected_price_currency` staying `NULL` (never an implied `0`).
8. **One order, one lab, whole-order-only** -- unchanged from DH-EX-010's own MVP architecture. A
   lab covering only some ordered tests is not eligible at all; an order is never split across labs
   or given a partial/child assignment.
9. **Lab assignment is a separate durable step strictly after order creation**, never folded into
   `LabOrderIntent`. The intent/commit architecture DH-CLINIC-003C built is unchanged; routing an
   already-committed order to a lab is a second, independent mutation against the existing
   `LabSelection` model, matching this codebase's established two-named-methods discipline
   (`select()` vs. `assign_by_provider()`) rather than growing either into a wider, boolean-flagged
   operation.
10. **No automatic or implicit reassignment.** Deactivating a route never touches an
    already-created `LabSelection` -- it only prevents a *new* assignment. A different-lab
    assignment attempt against an order that already has a selection is refused
    (`DifferentLabAlreadySelected`), exactly as before; there is no change-lab/re-route/replace-
    selected-lab workflow in this ticket.
11. **`ProviderLabRoute` says nothing about Native Lab vs. LIS/Connect integration.** It is a
    DHealth-side routing permission only -- it does not model, and never will model, how a
    real lab's own OpenELIS/other-LIS/EMR system receives an order. That choice belongs entirely to
    a future pilot-topology ticket.
12. **This does not solve "no double entry."** DH-CLINIC-006 proves DHealth-side routing to a
    DHealth lab *organization* -- not that any real lab staff member received the order in any real
    system, and not that any production transport is qualified. ADR-043 Law 2 remains unresolved
    until the pilot-topology ticket's own choice (Native DHealth Lab, or DHealth Connect) is made
    and implemented.

### What this explicitly does not do

- Does not remove, deprecate, or reinterpret any existing marketplace/price API, `LabPrice` model,
  or patient price-consent workflow (DH-EX-003/DH-EX-005) -- this is a separate, additive launch
  path alongside the existing one, not a replacement.
- Does not build provider self-service network onboarding, bilateral invitation/acceptance, or any
  provider-facing route-configuration UI -- Launch-1 configuration is a platform-admin action only.
- Does not add a reassignment/change-lab/re-route workflow.
- Does not model or contact any LIS/EMR system (OpenELIS or otherwise), FHIR, HL7, or any
  transport/credential mechanism.
- Does not change DH-CLINIC-004B's real-browser-Keycloak qualification status in any way -- that
  ticket remains separately OPEN/PARTIALLY QUALIFIED, unrelated to this ADR.

### Evidence

Adapter full suite, real-PostgreSQL concurrency qualification (§20 PG1/PG2/PG5: concurrent
same-lab assignment converges to one `LabSelection`; concurrent different-lab assignment has
exactly one winner and one clean conflict; a route deactivation racing an assignment resolves to
one of two truthful outcomes only), and migration qualification (fresh-database upgrade to
`0023_provider_lab_routes`, downgrade guard). UI lint/typecheck/test/build. See
`PROJECT_STATE.md`'s DH-CLINIC-006 entry for exact commands, run evidence, and counts.

## ADR-053 — ACCEPTED / IMPLEMENTED (2026-09-18, DH-LAB-003): Native Lab result templates are scoped to `LabOffering`, immutable per version, and additive to the existing source-flexible observation model

**Date raised:** 2026-09-18, DH-LAB-003 · **Status:** ACCEPTED, IMPLEMENTED · **Ticket:**
DH-LAB-003.

**Why this needed a decision, not just an implementation.** The existing low-level Exchange
observation API (`LabObservation`/`record_observation`, DH-EX-012C/ADR-047) is deliberately
source-flexible: a caller supplies its own `source_code`/`display_label`/`result_type`/
`numeric_unit`, because a future Connect-reported observation may have no DHealth-side definition
at all. That flexibility is correct for an interoperability primitive; it is wrong as the Native
DHealth Lab *human-entry* UX DH-LAB-004 will build next, which cannot let a lab technician type
arbitrary observation codes/labels/types into a Patient result. This ADR records the controlled
layer that closes that gap, and the boundary that keeps it from constraining Connect.

### Decisions

1. **Templates are scoped to `LabOffering`, never directly to `TestCatalogEntry`.**
   `TestCatalogEntry` is the provider-facing ordered *service* identity; `LabOffering` is one
   particular lab's own implementation of that service. Two labs offering the identical catalog
   test may legitimately report a different observation structure (methodology, local reporting
   format, observation count) without that being a second ordered-service identity. A
   `LabResultTemplate` row (`exchange_lab_result_templates`) therefore carries `lab_offering_id`,
   never `catalog_entry_id`.
2. **A template version is immutable once created; no edit API exists at all.** A structural
   change is always a new version (`version` N+1 for the same offering), never a mutation of
   version N. `LabResultTemplateRepository` exposes no update/delete method for either a template
   or its observation definitions.
3. **Exactly one active template per offering, enforced by a database partial unique index and a
   `FOR UPDATE`-locked compare-and-swap activation** (`activate_template`), mirroring
   `CatalogRepository.set_price`'s own established discover-and-swap-in-one-statement discipline.
   An offering may legitimately have **zero** active templates (before initial configuration) --
   `null` is a valid, honestly represented state, never fabricated.
4. **No standalone deactivation endpoint.** The only way to change what is active is to activate a
   different version (or the same one, as a safe replay); "turn it off and leave the offering
   template-less" is not exposed as its own action for an already-configured offering, keeping the
   state machine to one mutation shape rather than two.
5. **No reference-range field on the template model.** A static low/high pair depends on
   age/sex/methodology/population/instrument facts this product has no approved applicability
   model for yet. `LabResultTemplateObservation` carries no `reference_range_low`/`_high` column;
   a future LAB-004 observation derived from a template definition will carry `null` there until a
   richer, clinically-approved range model exists.
6. **No clinical-interpretation field anywhere in this layer.** No normal/abnormal/critical value,
   no enumerated TEXT choice set, no automatic comparison against a range. This is result
   *structure* configuration, never decision support.
7. **Configuration authority mirrors offerings/prices exactly: lab organization ORG_ADMIN, not
   LAB_TECH.** The staff who enter Patient results are not automatically the authority who defines
   the controlled structure they enter results into -- the same separation `LabOffering`/`LabPrice`
   configuration already uses.
8. **The existing low-level `LabObservation`/`record_observation` API is untouched and remains
   fully source-flexible.** No new column, no new constraint, no new required-template check was
   added to it. Templates are an *additive* safety layer this ticket introduces for Native Lab's
   own future human-entry mutation (DH-LAB-004) to require -- they are never retroactively imposed
   on the interoperability primitive itself, so a future Connect-reported observation with no
   matching DHealth-side template remains fully valid at that layer.
9. **Templates do not gate routing, eligibility, marketplace listing, or pricing.** Provider-lab
   eligibility (`eligible-labs`), provider-assigned routing (`assign_by_provider`), and public
   marketplace listing are unchanged by this ticket -- none of them queries
   `exchange_lab_result_templates` at all. A lab with zero configured templates remains fully
   assignable; the *result-entry-plan* read (below) is the only place a missing template becomes
   visible, and even there it is reported honestly (`template: null`), never used to block
   assignment or acceptance.
10. **A new order-scoped, read-only `GET .../lab-orders/{order_id}/result-entry-plan` resolves
    each `IN_PROGRESS` item's currently active template**, batched (never one query per item). A
    currently-assigned order's item with literally no `LabOffering` row at the selected lab is
    treated as a structural-integrity failure, not a normal missing-config state --
    `LabSelectionRepository.assign_by_provider`/`select` both require an active offering for every
    requested item at assignment time, and offering rows are never deleted (only deactivated), so
    this can only mean a genuine data-integrity violation, never an honestly-absent configuration.
11. **No result value, no result-entry mutation, and no LIS/Connect integration code exist in this
    ticket.** DH-LAB-003 is templates-plus-read-only-plan only. LAB-004 (Native Result Entry +
    Completion + Release) is the next ticket, and must pin/validate the exact template version at
    write time -- the browser submits only a `template_observation_definition_id` and a value; the
    server derives `source_code`/`display_label`/`result_type`/`numeric_unit` from the pinned
    template, never trusting client-supplied structure for a Native Lab entry.

### What this explicitly does not do

- Does not build a global analyte ontology, LOINC catalog, or any OpenMRS/OpenELIS concept mapping
  -- `source_code`/`source_system`/`display_label` stay exactly the hybrid-namespace shape
  ADR-047 already established.
- Does not add enumerated TEXT choice sets (e.g. positive/negative dropdowns) -- deferred, if ever
  needed, to its own explicit design.
- Does not add a template edit or delete API of any kind.
- Does not add or require any reference range, clinical interpretation, or abnormality flag.
- Does not change DH-CLINIC-004B's real-browser-Keycloak qualification status in any way -- that
  ticket remains separately OPEN/PARTIALLY QUALIFIED, unrelated to this ADR.
- Does not implement DH-LAB-004's own controlled result-entry mutation -- only documents the
  version-pinning contract that ticket must honor.

### Evidence

Adapter: 1029 passed (+41 over DH-LAB-002's 988: 27 template create/idempotency/conflict/
authorization/activation tests, 14 result-entry-plan functional/security/query-count tests), 108
skipped (+2: PG-A1/PG-A2 concurrent-activation tests, written to the same standard as every other
PG qualification file in this repository but **not executed this session** -- no Docker/PostgreSQL
reachable in this sandbox, see `PROJECT_STATE.md`'s DH-LAB-003 entry for the exact disclosure).
Migration `0024_result_templates` applied cleanly against a real disposable PostgreSQL instance in
push-triggered CI (`alembic upgrade head` succeeded 0022→0023→0024, `current == heads ==
0024_result_templates`) -- real-PostgreSQL evidence for the schema itself, even though the
concurrency tests above were not run against it this session. UI lint/typecheck/test/build clean.
See `PROJECT_STATE.md`'s DH-LAB-003 entry for exact commands, run evidence, and counts.

### Addendum (2026-09-18, DH-LAB-004): exact template-version pinning at first Native write

Decision 11 above is now implemented, exactly as promised, with one clarification this addendum
records rather than rewriting the paragraph above:

- **First-write pinning, not per-write resolution.** The first Native value write for an ordered
  item locks the owning `LabOffering` `FOR UPDATE` (the identical lock `activate_template`'s own
  compare-and-swap acquires), verifies the caller's named `template_id` is that offering's active
  template *at that exact locked instant*, and pins the item's `LabResult` container to it
  (`result_template_id`) permanently. Every later write for the same item must name that identical
  pinned template — never the offering's currently-active one, which may have since changed. If
  template activation wins the `LabOffering` lock first, the first write instead sees the target
  already inactive and refuses with a controlled conflict (`NoActiveTemplateForOffering`), never
  reinterpreting against whichever template happens to be active by the time it runs.
- **The pin survives every later template activation change.** `LabObservation` gains
  `template_observation_definition_id`, resolved against the *pinned* template's own definitions,
  not the offering's current one — an in-progress or historical entry keeps meaning what it meant
  when it was written, even after version N+1 activates.
- **The browser never submits result structure for a Native write**, exactly as promised:
  `POST .../items/{item_id}/native-result-values` accepts only `template_id` (identity) and, per
  value, `definition_id` plus `numeric_value`/`text_value`. `source_code`/`display_label`/
  `result_type`/`numeric_unit`/`source_system`/reference ranges are always derived server-side from
  the pinned template's own controlled `LabResultTemplateObservation` row.
- **The existing low-level `LabObservation`/`record_observation`/`correct_observation` API is
  additionally now refused, but only against a container/observation this ticket's own pinning has
  already claimed.** Once `LabResult.result_template_id` is set, the raw free-structure write and
  correct paths both refuse (`TemplateBoundContainerRefusesUnstructuredWrite`) rather than let an
  unstructured observation land alongside controlled ones, or let a controlled value be corrected
  into an unbound successor. A non-template-bound container (`result_template_id IS NULL` —
  every pre-DH-LAB-004 row, and any future Connect-reported container) is entirely unaffected;
  Decision 8's "untouched, fully source-flexible" promise holds for that case exactly as written.
  Real template-aware correction is deferred to DH-LAB-005, per Decision 11's own forward note.
- **Completion additionally requires every `required=true` pinned-template definition to have an
  effective value**, checked by `template_observation_definition_id` identity, never `source_code`
  string matching. Legacy (non-template-bound) completion semantics are unchanged.

No column, constraint, or check added by this addendum retroactively touches a pre-DH-LAB-004 row
— both new columns (`LabResult.result_template_id`, `LabObservation.
template_observation_definition_id`) are nullable, and migration `0025_native_result_binding`
backfills nothing.

### Addendum (2026-09-18, DH-LAB-005): the pinning promise above now also holds across a correction

"Real template-aware correction is deferred to DH-LAB-005" (immediately above) is now implemented,
and the pin it deferred is unchanged by it: a correction's successor observation preserves the
identical `lab_result_id` and the container's own pinned `result_template_id` — never re-derived,
never changed, even if a newer template version has since activated between the original entry and
the correction. `correct_native_value` (both the pre-release and the amendment-scoped copy)
derives the successor's structural fields from the predecessor's own
`template_observation_definition_id`, resolved against that same pinned template, exactly the way
the original write already did — a correction is not a second opportunity to reinterpret which
template governs an item. No schema change was needed for this addendum; it is a property of the
existing pinned columns, exercised by a new code path. See `PROJECT_STATE.md`'s DH-LAB-005 entry
for full evidence.

---

## ADR-054 — ACCEPTED / IMPLEMENTED (2026-09-20, DH-MVP-POLISH-001): one authenticated staff application; visible workflow derives from active organization and roles, not from a product the user picks

**Date raised:** 2026-09-20, DH-MVP-POLISH-001 · **Status:** ACCEPTED, IMPLEMENTED · **Ticket:**
DH-MVP-POLISH-001.

**Why this needed a decision, not just an implementation.** The native surfaces had grown as two
products -- `ClinicApp` and `LabApp`, reached through application-switching buttons in the reference
connector's header -- each with its own provider component, each calling `/me` for itself. That
shape asked the user to know in advance which product their role belonged to, and it made the same
question ("what may this person do?") answerable in two places that could disagree. Collapsing it
changes routing, session lifetime, and what the frontend's permission checks mean, so the choices
are recorded rather than left implicit in a diff.

### Decisions

1. **One staff application, one session.** `StaffApp` -> `WorkspaceProvider` -> `StaffWorkspace` is
   the only authenticated native surface. `/me` is fetched exactly once for the whole application;
   `ClinicProvider` and `LabProvider` remain as *projections* of that one workspace context (no
   `client` prop, no fetch of their own) so the existing page tests keep their meaning. A second
   `/me` fetch anywhere under the staff tree is a regression, and US3 fails on it.
2. **Capabilities derive from the active membership, and are usability only.** `canViewPatients`,
   `canCreateOrders`, `canReviewResults`, `canWorkLabOrders`, `canReleaseResults` are computed from
   the active organization's type, status and roles, each mapped to the backend gate that actually
   enforces it (`_require_clinician`, `_require_lab_tech`, `_require_lab_tech_or_releaser`, and the
   `LAB_RESULT_RELEASER` check in `authorization.py`). Hiding a control is a courtesy; the server
   remains the only authority. ADR-044's separation of entry from release authority is unchanged:
   `LAB_TECH` alone never sees release, and NC-B fails if it is granted.
3. **Nav renders only what exists.** `Aujourd'hui / Patients / Ordres / Résultats`. There is no
   `Réglages` item, because facility setup and administration are DH-MVP-ADMIN-001's subject and a
   nav entry leading to "Bientôt" is a promise the build cannot keep. When that ticket lands, the
   item appears with the screen behind it.
4. **An organization switch discards the org-scoped subtree rather than reconciling it.** The
   active organization's id is the `key` of the content subtree, so every org-scoped async surface
   is invalidated at once and a late response from organization A cannot render under B. This is a
   deliberate trade: it also discards unsaved in-subtree state on a switch, which is the safe
   direction. Pending-mutation maps are held *above* the keyed subtree so they survive. OS1 asserts
   on a heading, not a data row -- a row is cleared by the page's own effect either way, so a
   row-based assertion passes even with the `key` removed and proves nothing.
5. **Surfaces and their URLs.** Three surfaces exist, and two spellings are aliases:

   | URL | Surface | Notes |
   | --- | --- | --- |
   | `/` | staff | The default changed from the connector to the staff application. |
   | `/?app=staff` | staff | Canonical. |
   | `/?app=clinic` | staff | Alias, kept so existing links and bookmarks resolve. |
   | `/?app=lab` | staff | Alias, as above. Neither is a separate surface any more. |
   | `/?app=maternity` | reference connector | OpenMRS/maternity track, preserved. |
   | `/patient#<token>` | patient | Returns before any OIDC provider is constructed. |

   `clinic` and `lab` are *aliases*, not surfaces: they resolve to the one staff application, which
   then derives what to show from memberships. Nothing in the staff application reads them.
6. **Connector -> staff is one-directional by design.** The preserved connector header offers one
   button (`Ouvrir l'application DHealth`). There is no staff -> connector control: a staff user who
   needs the maternity connector reaches it by URL. Adding a visible route back would put the
   reference-connector track inside the staff product's navigation, which ADR-042/043 keep separate.
   Leaving the connector is not a logout -- unmounting the Exchange auth provider drops in-memory
   token state and must never call Keycloak's end-session endpoint.
7. **The token layer is shared with the preserved connector, and that has a consequence.**
   Retargeting `--color-primary` changes the connector's colour too, because the connector's
   stylesheets resolve the same tokens. That is accepted deliberately: one product, one palette. A
   future ticket that wants the connector to diverge must scope its own tokens rather than
   reintroduce literal hex values.
8. **Colour carries workflow responsibility, never clinical meaning.** petrol = a step is complete
   or under way; brass = something is still owed; clay = stopped, refused or superseded; ink =
   recorded fact. The `--color-success-*`/`--color-danger-*` names survive as historical aliases and
   mean "workflow step confirmed"/"workflow stopped". Consequences applied in this ticket: a patient
   surface follow-up marker moved off clay onto brass (clay beside a sentence about someone's own
   analysis reads as alarm about the result, which no DHealth surface may imply), and no value is
   ever coloured by its magnitude. That last point was verified rather than assumed: the only render
   site of `reference_range_low`/`reference_range_high` in the application styles the value cell with an
   unconditional `fontWeight`, and no component anywhere derives a colour, class or style from
   `numeric_value`.
9. **A corrected result is named, and its ordinal is demoted rather than erased.** Queue rows read
   `Rectificatif`; the version history, the relay's technical reference and the review restatement
   keep `version N`. `is_amendment` is `version_number > 1` server-side, so a first release still
   reads `Version 1` in a queue. The number is how a record is matched to the backend and discussed
   with a laboratory by telephone; it is demoted, never dropped.
10. **A review disposition takes two gestures and no modal.** The confirm control is always visible
    and inert until a disposition is chosen, and the sentence above it restates the disposition, the
    patient and the version on screen, bound by `aria-describedby`. A modal was rejected: someone
    working a queue dismisses the twentieth confirmation without reading it, which protects nobody.
    The restatement follows the version actually displayed, not the one the queue pointed at, and
    switching versions clears any chosen disposition.
11. **Typed result values are never persisted, and the UI says what that costs.** No autosave, and
    no draft in `localStorage`, `sessionStorage`, `IndexedDB`, cookies or the URL. Two events lose a
    draft and the per-field marker names both: a refresh, and a *failed* token refresh -- which sets
    auth status `error`, at which point `ExchangeAuthGate` renders its own notice in place of the
    tree below it and the entry form unmounts, before any redirect. A *successful* silent refresh
    loses nothing, which is the ordinary case for a token expiring while the panel sits open. The
    failure path cannot be bridged without persisting clinical values outside the component, so it
    is stated rather than papered over.
12. **The relay derives from real order state and invents nothing.** No node names a person: the
    backend models release and review authority as organization roles, so naming an individual as
    holding the release would be fabrication, and the node type has no field for a person at all. A
    fact the API does not date is shown undated rather than borrowing a nearby timestamp.
    `COMPLETED` implies released, because `release_lab_order_results` is the only supported
    transition into it. Six pairs are never collapsed: selected/accepted, entry-complete/released,
    released/reviewed, release/amendment, refused/cancelled, and analysis-under-way/
    results-available.

### Consequences

- `ClinicApp`, `ClinicShell`, `LabApp` and `LabShell` are deleted. Their test coverage was
  *rewritten onto the new surface*, not dropped: `ClinicApp.test.tsx` -> `StaffApp.provider.test.tsx`
  and `LabApp.test.tsx` -> `StaffApp.laboratory.test.tsx`.
- No migration. Expected head remains `0025_native_result_binding`; nothing in this ticket touched
  the adapter's schema.
- DH-CLINIC-004B's E2E paths that navigated *directly* to a per-product native app no longer exist
  as written. They are superseded, not closed: see `PROJECT_STATE.md`'s DH-MVP-POLISH-001 entry for
  which spec steps were rewritten and which remain unproven without a live stack.

### Addendum (2026-09-20, DH-MVP-POLISH-001, post-review): the authorization enum is not a label

Decision 8 above governs colour and Decision 9 the version ordinal, both of which are about keeping
technical vocabulary off the screen. The identity band broke the same rule and it was missed until
review: `StaffShell` rendered `activeOrganization.roles.join(' · ')`, so the masthead read
`CLINICIAN`, or `LAB_TECH · LAB_RESULT_RELEASER` for a dual-role laboratory user. A ticket that spent
its length replacing `Version 2 — Amendée` with `Rectificatif` and `PROVIDER` with `Établissement de
soins` left the authorization enum in the one band that is on screen at all times.

Roles are now labelled in French by `ui/src/features/workspace/roleLabels.ts`:

| Role | Label |
| --- | --- |
| `CLINICIAN` | Clinicien |
| `LAB_TECH` | Analyse |
| `LAB_RESULT_RELEASER` | Libération |
| `ORG_ADMIN` | Administration |

Four properties of that mapping are deliberate:

1. **The role values are untouched.** This is presentation only. Capability derivation, every request,
   and every existing test still work on the enum; nothing downstream reads a label.
2. **Entry and release stay two authorities.** A dual-role user reads `Analyse · Libération`, not one
   merged "laboratoire" role — ADR-044's separation, preserved in the wording.
3. **Presentation order is fixed** (clinician, entry, release, administration) rather than whatever
   order the API returned, so the band does not reshuffle between two responses describing the same
   authorities.
4. **`ORG_ADMIN` is labelled, not hidden.** It names authority the backend genuinely grants, and
   dropping a granted authority from an identity band would be its own inaccuracy. It is not a promise
   of a screen: this build still has no facility-administration surface and no `Réglages` item, per
   Decision 3. The band says what someone *is* here, not what this build can do.

An unrecognised role falls back to its raw value. That is chosen over dropping it: if a fifth role is
added server-side, an unlabelled code on screen is a visible prompt to name it, whereas silent
omission would hide an authority nobody would notice was missing. Tests pin all four known values, so
the fallback cannot quietly become the normal path for a role that already exists.

Proven by a negative control: restoring `roles.join(' · ')` fails both US11 and the new US13.

---

## ADR-055 — ACCEPTED / IMPLEMENTED (2026-09-21, DH-MVP-ONBOARD-001): a facility is enrolled, configured, operationally ready and production-routable as four separate facts

**Date raised:** 2026-09-21, DH-MVP-ONBOARD-001 · **Status:** ACCEPTED, IMPLEMENTED · **Ticket:**
DH-MVP-ONBOARD-001.

**Why this needed a decision.** Before this ticket, the only durable answer to "is this facility
part of the DHealth network?" was the existence of an `Organization` row, which says a name and a
type exist and nothing else. Every other question a network operator actually asks — who
administers this place, will they work in DHealth or keep their own software, what is that
software, can we integrate it, can clinics send them orders today, and if not what is stopping it —
had no representation at all. The failure mode of adding them casually is a system that reports a
facility as connected when all it knows is that somebody typed a name into a form, so the
separations below are recorded rather than left to whichever screen is being written next.

### Decisions

1. **Facility enrollment is distinct from technical integration.** Organizational enrollment answers
   "who is this facility on the network"; technical integration answers "how does their existing
   software talk to DHealth". `FacilityEnrollment` carries both but never conflates them, and
   creating an organization performs no integration of any kind.
2. **Every facility declares `NATIVE` or `CONNECT` explicitly.** Participation mode is chosen, never
   inferred — entering a LIS vendor name does not make a facility CONNECT. ADR-043 Law 2 (no double
   entry) makes these alternatives rather than a spectrum: a facility either works in DHealth or
   keeps its own system and connects it.
3. **Platform scope is not organization scope.** A DHealth platform administrator acts on the
   network, not inside a facility. The frontend models this as an explicit `WorkspaceScope`, and in
   platform scope `activeOrganization` is genuinely `null`.
4. **A platform administrator needs no fabricated membership.** No "DHealth Headquarters"
   organization exists, and none may be created to satisfy a UI expectation. Inventing one would
   make every capability check, audit row and org-scoped read answer questions about a facility that
   does not exist. A zero-membership platform admin reaches network administration directly; a
   person holding both platform authority and memberships switches between the two deliberately, and
   platform context never masquerades as a membership.
5. **Creating an organization does not imply production readiness.** A new facility is `DRAFT`. It
   becomes usable through configuration and staffing, which are evaluated, not asserted.
6. **A configured `ProviderLabRoute` does not imply operational readiness.** The route answers only
   "may this provider route orders to this lab?", exactly as ADR-052 defined it. Whether the lab can
   currently do the work is a separate, live question.
7. **Operational readiness does not imply a lab can fulfil every order.** Whole-order eligibility
   (does this lab offer every requested test?) remains its own check, unchanged.
8. **Native production routing requires minimum operational authority.** A Native provider needs an
   active `CLINICIAN`; a Native laboratory needs an active `LAB_TECH` *and* an active
   `LAB_RESULT_RELEASER`. The releaser is the sharp case: entry and release are separate authorities
   (ADR-044), so a lab holding only `LAB_TECH` can accept an order, enter every value, and be
   structurally incapable of releasing it — work arriving somewhere it can never leave, which is
   exactly what §37's gate exists to prevent. `ORG_ADMIN` alone satisfies no operational role.
9. **One human may hold both laboratory authorities.** The requirement is that both exist at the
   organization, never that two people do. A small laboratory where one person enters and releases
   is a real, permitted configuration, and ADR-044 explicitly declines to force a second human.
10. **Losing staffing blocks new routing without deleting route configuration.** Nothing rewrites
    `ProviderLabRoute.active` when people come and go: a platform administrator configured that
    relationship deliberately and it stays theirs. What changes is that the facility stops being an
    eligible destination and new assignments are refused. Restoring the staffing makes the same
    configured route usable again, with no route recreation.
11. **A CONNECT facility cannot reach production readiness before connector qualification exists.**
    `READY_FOR_ROUTING` is unreachable for a CONNECT facility, enforced in the repository rather than
    at one endpoint. This holds however complete the facility is — perfect staffing, offerings,
    templates, accepting orders and every interface field populated do not move it, because none of
    them makes a connector exist.
12. **Interface REPORTED ≠ VERIFIED ≠ connector QUALIFIED.** A partner's claim, DHealth having
    checked the interface exists as documented, and a connector having passed qualification are three
    facts. Recording verification changes no onboarding status and unlocks no routing, and only
    platform authority may record it — a facility's own `ORG_ADMIN` cannot certify itself.
13. **Cross-organization route activation stays platform-controlled**, unchanged from ADR-052, and is
    now additionally readiness-gated on the production paths: the activation endpoint, the assignment
    mutation, and the eligible-destinations read all consult one evaluator.
    `ProviderLabRouteRepository.upsert` remains the low-level persistence primitive with no policy in
    it, so fixtures and seeds can construct state without satisfying the policy they exist to test.
14. **Identity lookup grants nothing.** Finding somebody who has already authenticated is
    platform-admin-only, bounded, prefix-matched rather than fuzzy, and returns identity id, display
    name and email only. Creating the membership is a separate, explicit, audited act, and an email
    address is never authorization.
15. **No double entry remains binding.** A missing connector is never to be solved by asking staff to
    enter results in their LIS and again in DHealth. A temporary pilot exception would be its own
    documented decision, never the default enrollment path.
16. **Onboarding requires no real patient data.** Discovery records whether the source system *has* a
    patient identifier namespace or a release event, never any patient's data. Examples are synthetic.

### Consequences

- Migration `0026_facility_enrollment` adds three tables and backfills every existing organization as
  `NATIVE` / `ORGANIZATION_READY`. That is a statement of fact rather than a default: Connect does
  not exist in any form, so every organization operating today *is* Native. The alternative —
  treating a missing enrollment as "passes the readiness gate" — was rejected as a permanent
  fail-open hole.
- Readiness is server-derived and never recomputed in React. The frontend formats findings and
  displays the verdict; a copy of the rule in the browser would drift, and the drift would only
  surface when somebody activated a route that should have been refused.
- Refusal telemetry is best-effort and cannot alter a refusal. An audit failure neither permits a
  prohibited activation nor replaces the truthful 409 with a misleading server error.
- No DHealth Connect transport was built, and none may be inferred from this ADR. The connector
  contract and its qualification model are DH-CONNECT-CONTRACT-001's subject.
