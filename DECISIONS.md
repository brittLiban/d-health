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
