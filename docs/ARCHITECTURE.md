# Daher Health — v1 Pilot Architecture

> **Reading this document today (2026-09-12 or later)?** This document has two parts, on two
> different tracks. **§§1–16 below** describe the maternity/birth-centre clinical stack
> (OpenMRS + OpenELIS + Keycloak + the adapter) — real, running, historically-and-currently accurate
> architecture for that system, now the **first reference connector track** under DHealth Connect's
> model. **§17** describes the DHealth Exchange, added 2026-09-08 (ADR-042). Neither section is
> DHealth's current overall product definition — that is `DECISIONS.md`'s **ADR-043** and
> **`docs/MVP-1.0.md`**, which this document does not duplicate. Start there, then come here for
> the topology/versions/build-order detail of whichever track your ticket touches.

**Status:** DH-003's maternity foundation, build steps 1 and 3, the Keycloak provider, adapter
safety skeleton, local backup path, PostgreSQL tenant isolation, DH-021's generated keystore,
DH-016's reverse proxy, and DH-008's fail-closed SSO plumbing are built and runtime-verified. A
restore has been **performed** with partial coverage (DH-015). SSO activation and the one-login
demonstration remain blocked on staff names; the role types themselves are decided (ADR-017).
DH-025's in-flight crash reconciliation core is runtime-verified; attaching/querying its correlation
on the concrete OpenELIS resource remains a P0 gate before adapter transport activation. **DH-011 is
no longer decision-blocked** (BLOCKER-3 closed 2026-08-14) and is the critical path. A separate
multi-facility provisioning system exists under `provisioning/` — see §16. See `CURRENT_STATE.md`
for the exact verification boundary.
**Scope:** Single-facility pilot — one birth delivery centre + its laboratory, Djibouti. §16 is the
one deliberate exception, and it deploys facilities rather than serving them.
**Last updated:** 2026-08-16 (Africa/Djibouti)

---

## 0. Decisions taken — where this build deviates from the brief, and why

Three items in the brief could not be built exactly as written. All three were raised before any
code was written and resolved by Liban on 2026-08-10. Recorded here so the deviations are visible
to anyone who reads this repo later without the conversation.

| # | Item in the brief | Reality | **Decision** |
|---|---|---|---|
| A | "One PostgreSQL instance, separate DBs per service" | OpenMRS cannot safely run on PostgreSQL (§3) | **Two engines** — MariaDB for OpenMRS, PostgreSQL for OpenELIS + Keycloak + adapter |
| B | "Enable and configure the **Partograph module**" | No such maintained OpenMRS module exists (§7) | **Plain O3 form, structured capture only.** No alert lines, no action-line flagging, no prolonged-labour logic — that is decision support and belongs to Liban + the clinical lead, explicitly, before it is ever built |
| C | Build the adapter from scratch | `labonfhir` already does the FHIR round-trip (§6) | **Build the minimal custom round-trip first** against native OpenMRS/OpenELIS FHIR APIs; revisit `labonfhir` once the real message shapes are visible |

Two further decisions taken at the same time:

| Item | **Decision** |
|---|---|
| RapidPro | **Dropped from v1.** 5–7 containers for one content-free notification is disproportionate. Adapter calls the WhatsApp Cloud API directly (§8). RapidPro returns in v1.1/v1.2 when two-way messaging, campaigns, or SMS fallback are actually wanted. |
| WhatsApp credentials | **None held yet.** Build against a local mock/sandbox; real credentials are a `.env` change, never a code change. The WhatsApp bullet stays explicitly marked incomplete until real credentials are exercised. |

Everything else in the brief is buildable as specified.

---

## 1. What this system does — and does not do

**Does:** registers patients, records clinical observations and labour monitoring data, manages
maternity beds, places lab orders, routes those orders to the laboratory, routes results back to
the patient chart, notifies the patient that results are ready, authenticates staff once across
both applications, and backs everything up nightly to off-site storage.

**Does not:** interpret results, score risk, triage, recommend treatment, or generate any clinical
output. Every value displayed is a value a human entered. This is a **record-and-route** system.
No component in this design contains clinical logic, and any future request that would add it gets
flagged before implementation, not after.

**Data residency:** all patient data lives in this stack's own databases, on the facility's own VM.
No patient data is sent to any third-party AI/ML service. The only outbound patient-adjacent
traffic in v1 is the WhatsApp notification (a phone number plus a content-free "results are ready"
template — see §8) and the encrypted nightly backup to object storage (§9).

---

## 2. Service topology

The target topology has twelve long-running containers in one `docker-compose.yml`: Caddy,
OpenMRS backend, OpenMRS frontend, OpenMRS gateway, MariaDB, OpenELIS webapp, OpenELIS FHIR,
OpenELIS frontend, PostgreSQL, Keycloak, the adapter, and the backup sidecar. The current foundation
runs eleven: Caddy is built and locally verified (DH-016), and backup remains an opt-in profile
until production credentials exist (DH-022).

Compose also contains short-lived setup jobs: the OpenMRS demo-metadata preflight and maternity
seed, OpenELIS certificate provisioning, DH-020 PostgreSQL tenant-grant hardening, DH-024 OpenELIS
admin-password rotation, and DH-008 Keycloak client reconciliation. They must exit 0 before their
dependent services start; they do not remain running or add steady-state resource use. The password
job intentionally runs after the webapp's first healthy login redirect because the webapp bootstrap
rewrites its bundled admin hash; the frontend remains gated until rotation and in-database
verification finish.

**Corrected at build step 1:** O3 ships a fourth container, an nginx **gateway** that joins the SPA
and the API on a single origin. Caddy proxies *to* it rather than replacing it — O3's frontend
assumes the API lives at `/openmrs` on its own origin, and reimplementing that routing in Caddy
means maintaining it by hand across every O3 upgrade. One container to avoid owning someone else's
routing rules is a good trade.

(The diagram below collapses the OpenELIS frontend into its webapp box for legibility.)

```
                          Internet
                             │
                       ┌─────▼─────┐
                       │   caddy   │  automatic HTTPS (Let's Encrypt)
                       │  :80 :443 │  domains from .env
                       └─────┬─────┘
        ┌──────────────┬─────┴────────┬──────────────────┐
        │              │              │                  │
  emr.<domain>   lab.<domain>   id.<domain>       (no public route)
        │              │              │                  │
┌───────▼───────┐ ┌────▼─────────┐ ┌──▼────────┐  ┌──────▼───────┐
│ openmrs-      │ │ openelis     │ │ keycloak  │  │ adapter      │
│ frontend (O3) │ │ (Tomcat)     │ │           │  │ (custom)     │
└───────┬───────┘ └────┬─────────┘ └──┬────────┘  └──┬────┬──────┘
        │              │              │              │    │
┌───────▼───────┐ ┌────▼─────────┐    │              │    │
│ openmrs-      │ │ openelis-    │    │              │    │
│ backend       │ │ fhir (HAPI)  │◄───┼──────────────┘    │
│ (Tomcat)      │ │              │    │                   │
└───────┬───────┘ └────┬─────────┘    │                   │
        │              │              │                   │
┌───────▼───────┐ ┌────▼─────────────────▼──────┐         │
│   mariadb     │ │        postgres             │         │
│  (OpenMRS)    │ │ openelis │ keycloak │adapter│         │
└───────┬───────┘ └────┬────────────────────────┘         │
        │              │                                   │
    ┌───▼──────────────▼───┐              (notifier is a module *inside* the
    │   backup (sidecar)   │──► S3-         adapter container, not a separate
    │  nightly maria + pg  │    compatible  service — RapidPro dropped, §8)
    └──────────────────────┘    storage                    │
                                                           └──► Meta Cloud API / BSP
                                                                (mock until credentials exist)
```

**Note the shape of OpenMRS 3:** it is *not* one container. The O3 Reference Application is a
Tomcat backend (REST + FHIR R4 API, modules, database) plus a separate nginx container serving the
single-page frontend. Both are needed; they are versioned together.

**Note the shape of OpenELIS:** likewise multi-container — the Tomcat webapp, a HAPI FHIR server
that is the actual FHIR endpoint the adapter talks to, and a React frontend.

**Corrected at build step 3 (DH-005).** This section previously said we drop upstream's `certs`
*and* `proxy` containers "because Caddy terminates TLS for us". That was half wrong. `proxy` is
indeed dropped. **`certs` is kept**: it is not about public TLS, it supplies the internal
keystore that the webapp's and FHIR server's own `server.xml` require in order to open their
8443 connectors at all. Without it Tomcat aborts with `Error creating SSLContext`. Verified by
running it, not reasoned about.

**Consequence for §9 (Caddy):** neither OpenELIS component serves plain HTTP internally — the
webapp answers only on 8443, and the FHIR server's 8080 connector is commented out in the shipped
`server.xml`. Caddy must therefore proxy to **https** upstreams with certificate verification
relaxed for the internal self-signed keystore, not to plain HTTP as the O3 gateway allows.

---

## 3. Database topology — the one place I'm deviating from the brief

**The brief asks for:** one PostgreSQL, three databases.

**The problem:** OpenMRS does not run reliably on PostgreSQL. The platform has nominal PostgreSQL
support since 2.4.0, but the Liquibase changesets and — critically — the *module* SQL (bed
management, initializer, FHIR) are written against MySQL. The OpenMRS community's own integrated
distribution, Ozone HIS, runs on MySQL, and the maintainers stated flatly in November 2025 that
they do not recommend attempting PostgreSQL until it is properly supported. Putting a maternity
pilot on an unsupported database path means every module upgrade is a gamble, and there is no one
to ask when a changeset fails.

**Second constraint I verified:** OpenELIS ships its database as a *purpose-built image*
(`postgres:14.4` + baked-in schema and reference-data SQL in `docker-entrypoint-initdb.d`). It is
not designed to be pointed at a generic empty Postgres. Its app *connection* is configurable, but
its *provisioning* is not.

**Decided topology (approved 2026-08-10):**

| Engine | Container | Databases | Why |
|---|---|---|---|
| MariaDB 10.11 | `openmrs-db` | `openmrs` | The only supported path for OpenMRS + modules. 10.11, not 11.4 — the line the OpenMRS reference distribution tests against (ADR-011) |
| PostgreSQL 14.4 (OpenELIS image) | `postgres` | `clinlims` (OpenELIS), `keycloak`, `adapter` | OpenELIS's own image, extended with an init script that also creates the Keycloak and adapter databases and roles |

That is **two engines, two containers** — one more container than the brief, but the spirit of the
brief (small footprint, no per-service DB sprawl) is preserved: Keycloak and the adapter are
co-tenants of the Postgres instance OpenELIS already requires, rather than getting their own.

**Tenant boundary, verified at DH-020:** PostgreSQL's default database-level `PUBLIC` grants are
revoked on `clinlims`, `keycloak`, and `adapter`, with privileges restored explicitly to each
database owner. All six cross-tenant role/database combinations are denied. The idempotent grant
script runs from `initdb.d` on a fresh volume and as a one-shot Compose job for an existing volume;
OpenELIS's own schema grants are deliberately left untouched.

**Fresh-volume readiness correction at DH-024:** `pg_isready` alone also answers against the
temporary server used by `initdb.d`. That server is deliberately stopped before the final one
starts, which allowed the hardening job to race into a connection refusal. Readiness now requires
both `pg_isready` and `postgres` as PID 1; the complete empty-volume database → hardening → webapp
→ password rotation → frontend path was run successfully.

The rejected alternative was all-PostgreSQL: literal compliance with the brief, but running this
module set on a database path essentially nobody runs in production, with failures likely to
surface during a module upgrade rather than at build time.

**Multi-facility note:** at multi-site scale each facility keeps its own pair; a central OpenHIM/
OpenCR layer would sit above them. Nothing in this topology blocks that — it just isn't v1.

---

## 4. Configuration as code — how "no manual steps" is actually achieved

The Definition of Done says `docker compose up -d` plus a filled `.env` and nothing else. That is
only reachable if every piece of configuration loads itself on first boot. Mechanisms:

| Config | Mechanism | Lives in |
|---|---|---|
| OpenMRS global properties, locales, concepts, forms, roles/privileges, locations | **`openmrs-module-initializer`** — reads a config tree at startup, idempotently | `openmrs-config/` |
| OpenMRS O3 frontend behavior | `SPA_DEFAULT_LOCALE=fr`; the pinned distro already includes `esm-ward-app` and `esm-bed-management-app` | `docker-compose.yml` + pinned frontend image |
| Maternity ward | Initializer 2.12.0 location CSV, deterministic UUID, admission-location tag | `openmrs-config/initializer/locations/` |
| Maternity admission Visit type (`Daher Maternity Admission`) | Initializer 2.12.0 native `visittypes` CSV domain (`Domain.VISIT_TYPES` → folder `visittypes`, confirmed by disassembling the pinned module — ADR-028), deterministic UUID, additive-only alongside upstream's own visit types | `openmrs-config/initializer/visittypes/` |
| Bed types and beds | Initializer 2.12.0 has no bed domains. A short-lived, non-root job uses bedmanagement 7.2.0 REST after backend health; create-only, conflict-failing, additive-only (ADR-007) | `openmrs-config/seed/` |
| O3 demo content | Local `on`/`off` backend build targets share the exact digest-pinned upstream base. `off` removes only `referenceapplication-demo` source directories because not every Initializer loader honors filename exclusions. An opt-in patient preflight requires its metadata to exist from an earlier content-on/patients-off boot | `openmrs-config/Dockerfile`, `openmrs-config/validate-demo-readiness.sh` |
| Keycloak realm, clients, roles, French login theme | **Built and runtime-verified at DH-007/DH-008.** `--import-realm` loads committed JSON for a fresh database; because it skips an existing realm, an idempotent one-shot reconciles both application clients without deleting clients, roles, or users. Environment placeholders keep secrets out of Git. | `keycloak/realm-config/`, `keycloak/reconcile-sso.sh`, `keycloak/mappers/` |
| OpenMRS SSO module/config | Official OAuth2 Login 1.5.0 is built from exact tag commit `2770233627343a53719ec981e997d94b1d90b7ad`, then atomically installed into fresh or existing data volumes. A mode-`0600` runtime file enables or disables it. | `openmrs-config/Dockerfile`, `openmrs-config/configure-sso.sh` |
| OpenELIS lab tests, LOINC mappings, "accept external orders" | OpenELIS stores these in its DB; upstream documents them as admin-UI steps. I'll capture them as a committed SQL/REST seed applied on first boot so they are reproducible. | `openelis-config/` |
| OpenELIS local admin password | Upstream ignores `DEFAULT_PW` and bootstraps a published hash. A post-webapp one-shot bcrypts `${OE_ADMIN_PASSWORD}`, updates exactly one admin row, verifies it, and gates the frontend | `openelis-config/postgres-init/set-admin-password.sh` |
| Adapter behaviour | environment variables only | `.env` |

Nothing in this list is "click through the admin UI after deploy". If I hit something that genuinely
cannot be automated, it goes in `README.md` as an explicit numbered post-deploy step and I tell you.

---

## 5. Identity and SSO

Keycloak is the single identity provider in realm `daher` (ADR-014). Protocol choice is
constrained by the exact application builds in this deployment, not by a preference for protocol
uniformity:

- **OpenMRS uses OIDC.** O3 3.7.x does not ship an OIDC login module, so the derived image builds
  official `openmrs-module-oauth2login` 1.5.0 from its exact release commit. Its `roles` claim must
  contain exact OpenMRS role names; its `provider` claim is hard-coded to `false` until Liban
  approves which staff classes are providers.
- **OpenELIS uses SAML.** OpenELIS 3.2.1.11 exposes OIDC configuration, but exact source and runtime
  bytecode show its OAuth success path is wired to `CustomFormAuthenticationSuccessHandler`. That
  handler accepts `UserDetails`, while an OIDC login supplies an `OidcUser`; it consequently clears
  authentication and returns to `LoginPage`. The SAML path is wired to
  `CustomSSOAuthenticationSuccessHandler`, the working upstream path. It consumes a case-sensitive
  `Role` attribute containing exact OpenELIS native role names.
- **The adapter is not a user.** It gets its own Keycloak service account (client credentials
  grant), so its writes into OpenMRS are attributable to a named machine identity in the audit
  trail rather than borrowing a clinician's account.
- **Keycloak login theme is French by default**, English selectable.

**DH-008 status:** the realm/client definitions, existing-realm reconciliation, pinned OpenMRS
module, disabled-mode persistence behavior, local-login preservation, and both protocol metadata
paths are built and runtime-verified. `SSO_ENABLED=false` is the deliberate default. Activation and
the acceptance demonstration—one Keycloak login, both applications, no second prompt—are blocked
until Liban approves the mapping from `clinician`, `lab-tech`, and `sys-admin` to each application's
native roles and the OpenMRS-provider rule. No mapping was inferred.

### OpenMRS configuration visibility and V1 TestOrder create boundary

OpenMRS 2.8.7's built-in `Authenticated` role retains `Get Global Properties`: core patient search
and native order-number generation make protected internal `AdministrationService` reads. This is
an internal platform compatibility contract, not permission for ordinary Daher users to browse
configuration. The Daher module intercepts the pinned external setting/configuration HTTP paths
and requires `Daher View System Configuration`, granted directly only to
`Organizational: System Administrator`. Internal Java calls do not cross this interceptor.

Daher V1 does not use generic REST `/order` for laboratory order entry. Its module endpoint creates
only new native `TestOrder` objects from `encounterUuid`, `conceptUuid`, and `careSettingUuid`.
Patient, the authenticated user's sole active Provider, Test Order type, `NEW` action, native order
number, and audit creator are server-derived. The clinician must directly hold `Add Orders`; exact
internal resolution temporarily proxies only `Get Providers`, `Get Concepts`, `Get Care Settings`,
and `Get Order Types`, one trusted read at a time with `finally` cleanup. The response is
Daher-owned and does not require `Get Orders`. See ADR-041 and
`docs/DH-011H2C-EXTERNAL-CONFIGURATION-BOUNDARY.md`.

This boundary ends at OpenMRS. ADR-037's exact Provider-UUID mapping into native OpenELIS remains a
separate, unimplemented step; no name/fuzzy requester matching is part of this architecture.

The committed 1-hour idle, 10-hour maximum, and 5-minute access-token lifetimes were approved by
Liban for the pilot (ADR-013). They remain a human workflow/security decision if later changed.

Break-glass: local form login remains enabled in both applications. Set `SSO_ENABLED=false`, run
`docker compose up -d`, and verify local administration before diagnosing Keycloak. OpenMRS's
runtime wrapper then stores only `oauth2.enabled=false`—not the client secret—in its mode-`0600`
properties file. Rotate a local credential after emergency use.

---

## 6. The adapter — and the prior art you should know about

### The prior art

`openmrs-module-labonfhir` (v1.5.3 in the current OpenMRS HIS distribution) already implements
exactly the round-trip in the brief: OpenMRS lab order → FHIR `ServiceRequest` + `Task` → OpenELIS;
OpenELIS result → FHIR `DiagnosticReport`/`Observation` → back into the OpenMRS chart. It is
maintained by the OpenMRS community and is the path the official HIS distribution uses. Separately,
Ozone ships Apache Camel routes (`eip-openelis-openmrs`) for the same integration.

I am telling you this before building rather than after, because "write a custom adapter" and
"there is a maintained module that does this" are worth two minutes of your time to reconcile.

### Decision — build minimal first, decide on the dependency later

**Approved 2026-08-10:** build the minimal custom order/result round-trip directly against the
native OpenMRS and OpenELIS FHIR APIs. Once the actual message shapes are visible in practice,
revisit whether `labonfhir` earns its place as a dependency or whether the hand-rolled version is
already sufficient.

The reasoning is that the decision is cheap to defer and expensive to take blind: the audit ledger,
idempotency, retry supervision, and notification trigger are ours to build under either choice, so
no work is wasted by starting minimal. What we gain by waiting is a concrete view of the payloads
before committing to a module whose compatibility becomes a constraint on every future OpenMRS
upgrade.

Revisit trigger: once step 5 of §13 passes end-to-end, record the observed `ServiceRequest` and
`DiagnosticReport` shapes here and make the call.

### Trigger decision accepted — custom adapter with OpenMRS polling

`labonfhir` is not only a mapper: it also makes OpenMRS emit order events. Evidence gathered against
this exact distribution now establishes that OpenMRS FHIR has no `Subscription` resource and its
event module exposes only an in-process broker. A separate adapter therefore polls
`ServiceRequest` by `_lastUpdated`; the durable watermark and ledger design is written in
`docs/WATERMARK-DESIGN.md`. Liban accepted that design on 2026-08-12 with a configurable 15-second
poll interval. DH-010 has runtime-verified the ledger/dead-letter/audit/retry foundation; the
watermark and poller themselves remain DH-011.

Order discontinuation was tested with the correct verb. The original resource changes without
advancing `meta.lastUpdated`, but a new timestamped `DISCONTINUE` ServiceRequest carries `replaces`.
The adapter must process that relationship and must not filter on status.

The reverse leg is asymmetric: OpenELIS advertises FHIR `Subscription` and has an outbound
subscriber configuration, but no delivery has been observed. Corrected-result version/timestamp
behavior is also untested and must be characterised before trusting the result path.

**⚠️ How the order reaches OpenELIS is an open question, not a settled part of this design
(BLOCKER-6, raised 2026-08-16).** This section — and every other document here — described the
adapter as "routing the order to OpenELIS" without naming an ingestion mechanism. Probing found that
OpenELIS's documented inbound path is a **poll** from a remote FHIR source it is configured to trust
(`remote.source.uri`, currently empty), not a push into its FHIR store; and that the laboratory has
never received an order at all. Five candidates are recorded with their costs in `PROJECT_STATE.md`;
ADR-026 adds Option F, the business FHIR providers inside the OpenELIS webapp itself. **None is
selected**, because the choice changes what the adapter is and, for one option, what stores patient
data.

**DH-011B's full spike (2026-08-17) established the integration topology, though not yet the
selection.** There are **two distinct FHIR servers** in this stack, and the distinction is the whole
finding:

```
                 ┌──────────────────────── OpenELIS webapp container ─────────┐
adapter ──HTTP   │  /OpenELIS-Global/fhir/*   HAPI 7.0.2 (embedded)           │
  Basic ────────▶│  8 business providers ──▶ SamplePatientEntryService ──▶    │
                 │                            clinlims.patient / provider /   │
                 │                            sample / sample_item / analysis │
                 └───────────┬───────────────────────────────────────────────┘
                             │ publishes (fhir.subscriber.resources)
                             ▼
                 ┌──────────────────────── openelis-fhir container ───────────┐
                 │  /fhir/*  HAPI 6.6.0, mutual TLS — generic JPA store       │
                 │  This is option C's target, and NOT the business API       │
                 └───────────────────────────────────────────────────────────┘
```

**Verified:** creates through the business API produce real `clinlims` domain rows — it is not a
façade over the generic store. **Not verified:** `sample`/`sample_item`/`analysis` were never reached,
because every ServiceRequest is refused with `Generated lab number is invalid` (`SITEYEARNUM` formats
accessions as `%013d`, all-numeric, while the configured prefix is alphabetic `DEV01` — a facility
site code is required). **A trap for whatever is chosen:** the business API's *create* writes locally
while its *search* delegates to `http://fhir.openelis.org:8080`, a port this stack does not serve, and
the submitted `identifier` is discarded — so a submitted correlation and a never-submitted control both
return `total: 0`. Full evidence in ADR-026 and `PROJECT_STATE.md`; BLOCKER-6 remains open.

The `labonfhir` revisit is now costed rather than hypothetical: it provides an in-process,
event-driven order trigger but still polls results, is not shipped in this reference distribution,
and builds against `fhir2` 2.2.0 while this stack runs 4.2.0. Liban chose the custom adapter for v1;
ADR-004's post-golden-flow revisit remains in place.

### Adapter design (unchanged by that later decision)

**Language: Python.** You left it to me. Python's `fhir.resources` provides Pydantic models
generated from the FHIR R4 specification, with active maintenance — it validates resources at
construction time, which for a clinical data path means a malformed `Observation` fails in our
process with a clear error instead of silently landing in a chart. The Node FHIR client ecosystem
has no equivalent with comparable maintenance. Stack: FastAPI + `fhir.resources` + `httpx` +
SQLAlchemy + `tenacity` for backoff.

**Idempotency and audit are two separate mechanisms, deliberately:**

1. **Processed-message ledger** — a table in the adapter's own Postgres database, keyed on the
   source event's stable identifier (order UUID / result ID + version). Insert-first with a unique
   constraint; a duplicate insert means "already handled, stop". This survives restarts and makes
   double-delivery a no-op rather than a duplicate order in a patient's chart.
2. **Audit log** — append-only structured JSON (timestamp, direction, event type, source system,
   source record ID, target system, target record ID, patient identifier, outcome) written to a
   **named Docker volume** and included in the nightly backup. Separate from the ledger because the
   ledger answers "did we do this?" and the audit answers "what did we do, to whom, when" — and the
   second is the one that matters in an incident review.

**Failure handling:** exponential backoff with jitter on unreachable OpenMRS/OpenELIS; messages
that exhaust retries land in a dead-letter table and raise a loud log event. Nothing is dropped
silently. Ever.

**DH-010 implementation status (2026-08-13):** this safety foundation now runs. Alembic owns the
ledger and dead-letter schema; the stable source-event key is unique; state transitions are
guarded; the strict audit shape cannot accept arbitrary FHIR/clinical-value fields; audit writes
use append mode, a process lock, `fsync`, and mode `0600`; only explicitly transient exceptions
retry. The non-root container has a read-only root filesystem and readiness checks PostgreSQL plus
the named audit volume. The original fourteen-test foundation and the live migration/health check
passed. Order polling and FHIR transport are still DH-011, not implied by a healthy skeleton.

**DH-025 crash recovery (built and verified):** a stable SHA-256 correlation is derived from the
full source-event tuple under `urn:daher-health:adapter:source-event`. A session advisory lock is
held for the adapter's entire process lifetime and is part of readiness, fencing out a second
recovery/transport process. The coordinator marks complete on exactly one target match, retries
only after proven zero matches, and fails closed on an unavailable lookup, multiple matches, dead
letter, or other ambiguity. Twenty-seven tests cover concurrency and crash points on both sides of
the write; the real PostgreSQL fence, rollback-only coordinator path, and restart reacquisition
passed. DH-011 must still attach and demonstrate lookup of this correlation on its concrete
OpenELIS Task/ServiceRequest before transport activation.

**DH-011E order poller (built 2026-08-17, not activated).** The OpenMRS-side half of the transport
now exists: a durable watermark bootstrapped once from the server's clock (ADR-015), a window bounded
at both ends, deduplication by `(type, id, versionId)`, a completeness check against the bundle's
`total`, and **client-side ordering** — `fhir2` 4.2.0 accepts `_sort` and silently ignores it, so the
accepted design's reliance on server ordering was corrected in `WATERMARK-DESIGN.md` §3. Its output
is a transport-neutral `SourceOrderEvent`. **No loop runs it and no delivery implementation exists**,
because BLOCKER-6 has not decided how an order reaches OpenELIS. 94 tests, five controls.

**Timezone:** every container and both databases run `TZ=Africa/Djibouti` (UTC+3). Clinical
timestamps that silently differ by three hours between the EMR and the LIS are a patient-safety
problem, not a cosmetic one. DH-011E makes this executable rather than documentary: a `meta.lastUpdated`
whose offset is not `+03:00` is refused at construction, so a UTC-running adapter fails loudly
instead of shifting its window by three hours and dropping results.

---

## 7. Maternity clinical features — Partograph needs a decision

### Bed management — buildable as specified

`openmrs-module-bedmanagement` (backend) + the O3 `esm-ward-app` and `esm-bed-management-app`
frontends give ward/bed configuration, admission, bed assignment, and transfer. One
"Maternité / Maternity" ward, with two bed types: delivery (`Salle d'accouchement`) and postpartum
(`Post-partum`).

**Bed counts are configuration, not code** (decided 2026-08-10). Two `.env` variables —
`MATERNITY_DELIVERY_BEDS` and `MATERNITY_POSTPARTUM_BEDS` — drive an idempotent seed that creates
beds up to the configured count on boot. Changing a count is an `.env` edit and a restart; no
rebuild, no SQL, no admin clicking. The seed only *adds* beds; it never deletes a bed that may have
a patient or history attached to it, so lowering a count is a deliberate manual operation
documented in the runbook rather than a silent destructive side effect of an `.env` typo.

⚠️ The committed defaults are **placeholders, not the facility's real numbers** — they are marked as
such in `.env.example` and in the clinical sign-off checklist. Real counts from the birth centre
replace them before go-live.

**Implemented and verified at DH-003.** Initializer creates the deterministic `Maternité`
admission ward and French backend properties. Initializer 2.12.0 has no bed or bed-type domain, so
the `openmrs-maternity-config` job uses bedmanagement 7.2.0 REST after backend readiness. The job
runs non-root with a read-only filesystem and dropped capabilities. It exposes no update/delete
path: mismatched existing metadata stops startup. An isolated empty-volume boot and the full
same/increase/lower/zero lifecycle passed; see `PROJECT_STATE.md` for exact counts.

### ANC forms — buildable as specified

O3 form schemas (JSON), loaded via Initializer, based on the OpenMRS reference maternal health
configuration. Marked **needs sign-off from clinical lead** — see §11.

### Partograph — rescoped, decided 2026-08-10

There is no maintained OpenMRS partograph module to enable. What exists in the ecosystem are
external tools (CommCare/mLabor, iDeliver) that some implementers interface with OpenMRS — not a
native module you flip on. "Enable and configure the Partograph module" therefore had no referent,
and the brief was corrected.

**What v1 builds:** a plain OpenMRS custom form, authored with the O3 Form Builder / form engine,
capturing partograph data points as **structured, time-stamped data entry only** — cervical
dilatation, fetal heart rate, contractions, descent, maternal observations. Values a midwife
enters, stored and displayed back chronologically. Nothing more.

**What v1 explicitly does not build, by decision, not by omission:**

- ❌ alert lines
- ❌ action lines and action-line flagging
- ❌ any automated "this labour is prolonged / obstructed" determination
- ❌ any threshold, colour-coding, or highlight that communicates a clinical judgement

These are decision support. They collide directly with non-negotiable constraint #3, and the
determination of whether this system should ever make them is a **clinical-safety call belonging to
Liban and the clinical lead at the birth centre, made explicitly and in advance** — not something
this build infers its way into. If that sign-off is ever given, it gets recorded in
`docs/CLINICAL-SIGNOFF.md` with a date and a name before any line of that code is written.

A graphical partogram that plots *only* recorded values, with no threshold lines, is a candidate
for v1.1 and would still need the same conversation about what the axes imply.

---

## 8. Notifications

**Trigger:** result successfully written to the OpenMRS chart → send one templated WhatsApp message
to the patient's registered number.

**Content:** notification only — *"Vos résultats sont disponibles. Merci de contacter la clinique."*
No result values, no test names, no clinical content of any kind. The consent question is unresolved,
so the design forecloses it: the notifier is never given result values in the first place, so a
future misconfiguration cannot leak them.

**Templates:** French (default) and English, as WhatsApp-approved message templates, in
`adapter/templates/`. Language selected from the patient's recorded language preference in OpenMRS,
defaulting to French.

**Transport — decided 2026-08-10: RapidPro dropped from v1.**

The brief specified RapidPro. RapidPro is Django + Celery + Redis + its own PostgreSQL +
Elasticsearch + mailroom + courier — five to seven additional containers, comfortably the heaviest
component in the stack, to send one templated message with no branching logic. That is
disproportionate to the job, and it enlarges the surface area that has to be stable for the
1 September pilot.

Instead: a small notifier inside the adapter calls the **WhatsApp Cloud API (or Twilio) directly** —
one HTTP call, one message type, no platform. RapidPro returns in v1.1/v1.2 when two-way messaging,
campaigns, or SMS fallback logic are actually wanted; those are real capabilities that justify a
real platform, and none of them are v1.

**Credentials — decided 2026-08-10: none held yet.** No WhatsApp Cloud API account and no BSP
account exist at time of writing. The notifier is therefore built against a **local mock/sandbox**:
fully wired, fully testable, exercising the real code path and the real templates. Swapping
mock → sandbox → production is **`.env`-only, never a code change**. The WhatsApp line item stays
explicitly marked **incomplete** until real credentials have been exercised end-to-end — it is not
reported as done on the strength of a passing mock.

VM sizing without RapidPro: 4 vCPU / 16 GB is comfortable.

---

## 9. Reverse proxy, TLS, and domains

**Decision — subdomains, not path prefixes.** I'm deciding this one rather than asking, because the
tradeoff isn't close: Keycloak and the O3 single-page app both assume they own their path root.
Keycloak's OIDC redirect URIs, and O3's `importmap` and service-worker scope, both break or require
per-app rewriting hacks under a path prefix, and those hacks are exactly the sort of thing that
fails confusingly six months later during an upgrade. Subdomains cost one wildcard DNS record.

```
emr.${BASE_DOMAIN}   → openmrs-frontend (which proxies /openmrs to the backend)
lab.${BASE_DOMAIN}   → openelis frontend + /api to the OpenELIS webapp
id.${BASE_DOMAIN}    → keycloak
```

All three read from `${BASE_DOMAIN}` in `.env` — nothing hardcoded, so the same compose file serves
the next facility by changing one variable. Caddy obtains and renews certificates automatically.
The adapter, the databases, and the FHIR endpoints are **not** publicly routed; they are reachable
only on the internal Docker network. TLS is built **last**, per your process requirement, so we are
not debugging certificates and application behaviour at the same time.

---

## 10. Backup and restore

Nightly containerised scheduler sidecar (implemented at DH-014; it retains the container
environment reliably rather than depending on distro-specific cron environment behavior):

1. `mariadb-dump` of the OpenMRS database (`--single-transaction`)
2. `pg_dump` of `clinlims`, `keycloak`, and `adapter`
3. The adapter audit-log volume and the OpenMRS data volume (uploaded complex obs, module data)
4. A live Keycloak realm export, supplementing its authoritative PostgreSQL dump
5. Compress, encrypt, upload to S3-compatible storage, prune beyond `${BACKUP_RETENTION_DAYS}`
6. Write a success/failure marker the runbook tells you how to check

**Both engines. Every time.** A restore drill that only restores PostgreSQL would come back with a
working lab system and no patient records. That failure mode is an explicit checklist item in
[`../backup/restore-drill.md`](../backup/restore-drill.md), which now exists: DH-015 was
**performed** 2026-08-13 with partial coverage, and must be re-run once a lab result and an SSO
login exist to check.

Credentials from `.env`. The drill — restore into a scratch stack, verify a known test patient, a
known lab result, and a working SSO login — is performed and its output recorded before this pilot
is called ready. Per your instruction, this is not deferred and not optional.

**Verification boundary, 2026-08-13:** the complete local path above—including encryption and
archive read-back—passed. Off-site upload was explicitly disabled so no data left the host; it is
unverified and still requires an approved destination. A restore **was** subsequently performed the
same day (DH-015, partial coverage) and found two defects: `pg_restore --clean` silently losing rows
over OpenELIS's pre-populated image, and a half-initialised PostgreSQL at exit 127.

The scheduler is behind Compose's `backup` profile. This keeps the documented default
`docker compose up -d` usable before the required key/destination exist instead of creating a
credential-failure restart loop. Enabling the profile is part of provisioning the approved backup:
set `COMPOSE_PROFILES=backup` in `.env` after the required settings exist. The normal
`docker compose up -d` then remains the one-command deployment path.

---

## 11. Clinical content — sign-off boundary

I am not a clinician and neither is this system. Every piece of clinical content in this build —
the prenatal panel test list, ANC form fields, the Labour Care Guide fields, ward/bed labels — is
drawn from **WHO / OpenMRS / OpenELIS reference defaults, unmodified**, and is marked in the
repository as:

> ⚠️ **NEEDS SIGN-OFF FROM CLINICAL LEAD** — reference default, not validated for this facility.

Nothing clinical is invented, extrapolated, or "sensibly adjusted".
[`CLINICAL-SIGNOFF.md`](CLINICAL-SIGNOFF.md) now exists (created at DH-006) and inventories the 37
lab sets actually installed in this stack for the clinical lead to tick. Nothing in it is
pre-selected, proposed, or preferred, and **no clinical content is signed off**.

---

## 12. Pinned versions (draft)

Exact tags are confirmed as each service is built; nothing uses `:latest`. Where an upstream
publishes only a moving branch tag, I pin the **image digest** and record it here with its date.

### Digests pulled and pinned — build step 1, 2026-08-10 (DH-002)

These are repository digests as reported by the registry at pull time, copied verbatim into
`docker-compose.yml`. They are the exact images that were booted and verified.

| Image | Digest |
|---|---|
| `mariadb:10.11.18` | `sha256:de61fed4a40d3842f3ee09944ba52792156cfd9adf489b2cc670fc6ded28df8d` |
| `openmrs/…-3-backend:3.7.x` | `sha256:440bf92f4749faa38ca48a3e09f6651dee016089f0cbf6031dc5b1a979000ed7` |
| `openmrs/…-3-frontend:3.7.x` | `sha256:34352f481bf7e72b62406843925479042463fbbd7c8c3f7f5b9cda20b8390117` |
| `openmrs/…-3-gateway:3.7.x` | `sha256:e0f59c5e361bb092d0f5b36b32a1ac314aaabdd95db9047c8ca3309d0f2d7eb3` |

All four tags existed as listed — no substitutions were needed.

### Digests pulled and pinned — build step 3, 2026-08-12 (DH-005)

| Image | Digest |
|---|---|
| `itechuw/openelis-global-2:3.2.1.11` | `sha256:c7c7200141baf4c496121db1a6712f9fcf0866654e6f91e150fa0e4bbfc5ecab` |
| `itechuw/openelis-global-2-database:3.2.1.11` | `sha256:c7750aad1650a1dbc73f97240167bb778e9df23592e41c2c2232d47a4054c14f` |
| `itechuw/openelis-global-2-fhir:3.2.1.11` | `sha256:e58bc63f2248b55670017b7854ca3dd17c69c2977c4b38b98c20068f8f8e2320` |
| `itechuw/openelis-global-2-frontend:3.2.1.11` | `sha256:8054581aee1ef5b15e4a1ebc4fa8d96d7c5021ca36e580a7d78d2f2c7c78bb5a` |
| ~~`itechuw/certgen:main`~~ **removed at DH-021** | ~~`sha256:e27a8194300ba73309e835a4070e9ce531687eb3ee604895de781f3061791635`~~ |
| `eclipse-temurin:17-jre-alpine` (base of local `daher-health/certgen`) | `sha256:02320dd4ce20e243dfb915c686089cf9315c763084fafbb12d5c9993aee18b57` |

All four OpenELIS tags existed as this section predicted.

**`certgen` was removed at DH-021 (2026-08-13).** It never generated anything — it shipped
pre-built keystores inside a public image, so the private key protecting both internal 8443
connectors was publicly downloadable, and its certificate had *already expired* (`notAfter
2026-07-23`) while nothing on that path validated expiry. It is replaced by a local image built
from the digest-pinned `eclipse-temurin` base above, which generates the material on the deployment
and renders the matching `common.properties` in the same job so the keystore and its password
cannot drift apart. PostgreSQL is **14.4**, as
predicted — confirmed from the running container, not from the tag. The O3 backend runs on Amazon
Linux 2; the frontend and gateway on Alpine 3.24. All three ship `curl`, and the MariaDB image
ships the `mariadb` client binary, so the healthcheck commands in the compose file resolve
(verified by `docker run`, not assumed).

### Digest pulled and pinned — build step 4a, 2026-08-13 (DH-007)

| Image | Digest |
|---|---|
| `quay.io/keycloak/keycloak:26.7.1` | `sha256:f1f1f01e472c8a78df40d8f2a49a925274eda4d3d80d5f6edbb5c880ee3c01c6` |

The digest was read back from the pulled local image and is the image that passed realm import,
secret interpolation, readiness, and restart verification.

### Base digest pulled and pinned — DH-014 backup sidecar / DH-020 hardening job, 2026-08-13

| Image | Digest |
|---|---|
| `postgres:14.4` | `sha256:9ceb24f8c5f15c053d973a3610866f473690875dc13eb3282b45302189321040` |

The locally built `daher-health/backup:0.1.0` image uses this exact base so its `pg_dump` major and
minor match OpenELIS PostgreSQL. The DH-020 one-shot uses the same pinned image directly for `psql`.
The sidecar image itself is built from committed source rather than pulled from a registry.

| Component | Tag | Why |
|---|---|---|
| OpenMRS O3 backend | local `daher-health/openmrs-backend:3.7-{on,off}`; exact `openmrs/openmrs-reference-application-3-backend:3.7.x` digest base; Maven builder `3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82` | Current released O3 line. The local base removes only upstream's competing demo-patient trigger and adds official OAuth2 Login 1.5.0 from exact commit `2770233627343a53719ec981e997d94b1d90b7ad`; the two targets otherwise preserve or omit demo content while retaining upstream UID 1001 and startup contract. |
| OpenMRS O3 frontend | `openmrs/openmrs-reference-application-3-frontend:3.7.x` + digest | Must match the backend line |
| OpenMRS O3 gateway | `openmrs/openmrs-reference-application-3-gateway:3.7.x` + digest | nginx joining the SPA and API on one origin; upstream's tested routing |
| MariaDB | `mariadb:10.11.18` | **Corrected from 11.4 at build step 1.** The OpenMRS reference distribution tests against the 10.11 line; following upstream's tested pairing beats picking the newer LTS on my own judgement |
| OpenELIS | `itechuw/openelis-global-2:3.2.1.11` (+ matching `-database`, `-fhir`, `-frontend`) | Latest stable patch on the 3.2.1 line; the OpenMRS HIS distro integrates the 3.2.x line, so FHIR interop is exercised upstream |
| PostgreSQL | `postgres:14.4` (via OpenELIS's DB image) | Pinned by OpenELIS's own image; we do not get to choose it |
| Keycloak | `quay.io/keycloak/keycloak:26.7.1` + digest | Identity provider; realm import verified at DH-007 |
| Caddy | `caddy:2-alpine` → resolved to exact patch | Automatic HTTPS |
| Adapter | `daher-health/adapter:0.1.0`, built here; `python:3.13.14-slim-bookworm@sha256:67a1e1f…` base | DH-010 runtime-verified; base digest pulled 2026-08-12 |

⚠️ **OpenMRS/OpenELIS version compatibility is the fragile axis of this stack.** These two versions
are chosen together because the OpenMRS HIS distribution exercises this pairing upstream. Upgrading
one without the other is how the FHIR round-trip breaks; that warning goes in the runbook.

---

## 13. Build order

Per your process requirement — one service at a time, with a manual verification step you can
perform yourself before I move on.

| # | Step | You verify by |
|---|---|---|
| 1 | MariaDB + OpenMRS O3 standalone | Open OpenMRS, log in, register a test patient |
| 2 | OpenMRS maternity config — ward, beds, ANC form, Labour Care Guide form | Admit the test patient to a maternity bed; open the ANC form |
| 3 | PostgreSQL + OpenELIS standalone | Open OpenELIS, log in, see the prenatal panel tests |
| 4 | Keycloak + SSO for both | One login, both apps, no second prompt |
| 5 | Adapter — order out, result back | Place an order in OpenMRS, see it in OpenELIS, enter a result, see it in the chart |
| 6 | WhatsApp notification (against mock) | Result write-back fires a message; inspect the mock's captured payload and both language templates |
| 7 | Backup + **restore drill** | Watch the restore drill and confirm the test patient survives it |
| 8 | Caddy + TLS + domains | Reach all three subdomains over HTTPS |

`.env.example` grows at each step — every variable documented with a comment the moment it is
introduced, never batched at the end.

---

## 14. Out of scope for v1 (confirmed, not started)

Orthanc/PACS (v1.1) · billing UI · HR/scheduling · any AI/ML for imaging, triage, or diagnosis ·
OpenHIM/OpenCR multi-facility sync.

⚠️ **The self-service provisioning portal is no longer out of scope** — it started 2026-08-15 as a
deliberate schedule trade (see §16 and `PROJECT_STATE.md`). This line is left here rather than
deleted so the change of direction is visible rather than silently rewritten.

Where a v1 choice would need to change for multi-facility, it is noted inline in this document
(§3) — noted, not built.

---

## 16. Multi-facility provisioning (ADR-020, ADR-021)

**This is a separate system from the clinical stack**, living in `provisioning/`. It creates and
manages facility deployments. It never touches clinical data — that boundary is the reason every
other decision in this section looks the way it does.

### Topology

One clinical stack per facility, always. **Never a shared multi-tenant clinical database**: with
patient records, one query-scoping bug in a shared database is a cross-facility breach, and separate
stacks make that class of bug impossible rather than unlikely.

Default is **one VM per facility**. But the deploy target is a **field on the facility record**, not
an assumption in the provisioning logic, so a shared-host model stays available later without a
rewrite (ADR-020).

`FACILITY_ID` namespaces the Compose project, and therefore every container, volume and network
(DH-026). ⚠️ It is an identity, not a display name: Compose derives volume names from it, so
changing it does not rename anything — it points the stack at fresh empty volumes and orphans the
patient database while appearing to start normally.

### Trust boundary

| | Website server (portal) | Infrastructure (provisioning API) |
|---|---|---|
| Trust | **Untrusted.** Public, assume breach | Trusted |
| Holds | A scoped credential only | Deploy capability |
| Can reach clinical data | **No** | **No** |

The portal authenticates as an untrusted client over TLS with a credential scoped to create, list,
status, rotate, and soft-decommission. It grants no shell, Docker socket, or database access. The
API's own audit log is authoritative over anything the client claims, and lives outside the API
host's mutable filesystem so it survives that host being rebuilt or tampered with.

Full blast-radius walkthrough: **ADR-021**. Short version — a fully compromised website server buys
availability damage and a cleanup job, loudly logged, and not a clinical data breach.

### Why the API cannot read clinical data

Three layers, in increasing strength:

1. Response schemas are closed (`extra="forbid"`), so a clinical field cannot be bolted on.
2. A test walks every model against clinical vocabulary and fails the build on a match.
3. **The service holds no clinical database credentials at all.**

The third is the real guarantee — the first two catch mistakes, the third makes the mistake
unreachable. Verified by control: adding a `patient_count` field makes the suite fail; removing it
returns to green.

### How the API reaches a facility host (ADR-022)

**Pull-based host agent, outbound-only.** Facility hosts poll for jobs addressed to themselves and
authenticate outbound with a per-host token. **No clinical host exposes an inbound port.**

The deciding constraint: **Docker access is root access.** Anyone who can cause an arbitrary
container spec to run can `docker run -v /:/host` and read every clinical volume. So the question is
never "can an attacker get a shell" but "can an attacker cause an arbitrary spec to run" — hence:

> **The compose file lives on the facility host. The API sends parameters, never a spec.**

| Option | API-host breach yields | Inbound port on clinical host |
|---|---|---|
| Remote Docker socket | **Root on every facility VM** | Yes |
| SSH forced command | Fixed script with chosen params | Yes |
| Signed webhook listener | Same as above, plus bespoke code parsing untrusted input | Yes |
| **Pull agent (chosen)** | Can *enqueue*; nothing executes until an agent pulls and validates | **None** |

**The agent validates its own jobs.** It does not trust the queue. That local validation — not the
transport — is the real boundary, so a job runs only if the target host is itself, the verb is in
its fixed set, and every parameter matches a strict pattern.

**Execution is bounded per verb, not per agent.** `up --detach` waits on this stack's health graph —
`openmrs-backend` alone declares `start_period: 1800s`, and a cold boot is ≈49 minutes — so the
verbs that wait on that graph get a bound sized to it, while `status` and `stop` stay tight enough
that a genuine hang is visible quickly. A single bound was known bug 17: the first real provisioning
would have been reported as failed while the stack came up correctly. The bound is asserted against
this compose file in the provisioning test suite, so adding a slower gate here fails that suite
rather than the next deployment.

**Secrets are generated on the facility host**, not by the API. A breached API host has never held
any facility's database passwords.

**Bootstrap is the one irreducibly manual step.** A host cannot poll until it has an identity, and
that first credential cannot be delivered automatically without recreating the problem: an operator
runs a single-use, short-lived enrollment token on a bare VM, and the agent then generates its own
long-lived credential locally. Documented as manual rather than described as "no manual steps".

**Two escalation paths closed in the API:** `deploy_target` is resolved from an operator-registered
host allowlist rather than supplied by a caller, and an already-occupied host is a hard conflict
rather than an overwrite.

### Two operations that cannot do what their names suggest

Both were found by building them, and both are recorded because the obvious implementation is
actively harmful.

**Credential rotation (ADR-024).** Rewriting a facility's env file rotates nothing useful and
destroys plenty: database passwords were set once by `initdb`, so the stack would present new
credentials to databases holding the old ones and lose every database; `OMRS_ADMIN_PASSWORD` is
first-boot-only; and the backup passphrase decrypts existing archives. Rotation is therefore
**scoped** — TLS material and Keycloak client secrets — and **refuses** the rest, naming what is
actually required instead.

**Backup key custody (ADR-025).** The agent generates a facility's credentials locally, which is
what keeps the API from ever holding them. Applied to the backup passphrase that would produce
archives encrypted with a key stored **only on the machine being backed up** — useless in the
disaster backups exist for. The operator supplies it at host bootstrap, so it exists in two places
by construction. **A credential that must survive the loss of a machine cannot be generated on that
machine.**

Backup is scheduled only when it can succeed — a passphrase *and* an off-site destination. Absent
either, the scheduler is not enabled and the facility reports having no backup **as a problem**,
because a facility with no backup is one disk failure from losing patient records.

### State

The control plane's state is durable (SQLite): facilities, host registrations, agent enrolments,
API credentials and the job queue all survive a restart. Before that, restarting the API lost the
entire fleet and recovery meant re-enrolling every VM by hand. Two races are closed with conditional
updates — claiming a host and redeeming an enrolment token — so single-use survives two requests
arriving *together*, not just in sequence.

### Deliberately incomplete

**DNS/TLS provisioning is unconfigured**, built against a provider-agnostic interface, and reports
`dns_configured: false` rather than implying success. No domain or DNS provider exists yet
(NEEDS LIBAN #15). A provisioning step that appears to succeed against invented values is the false
green this project has repeatedly been bitten by. The same applies to the audit log's external
destination.

---

## 15. Open items

Open technical and human gates are tracked here and in `PROJECT_STATE.md`; several do block later
implementation or pilot acceptance:

| Item | Status | Needed by |
|---|---|---|
| Real maternity bed counts | Placeholder values in `.env`; changing them is an `.env` edit + restart (§7) | Go-live |
| WhatsApp Cloud API / BSP credentials | Building against mock; swap is `.env`-only. Line item stays marked **incomplete** until real credentials run end-to-end (§8) | Go-live |
| `labonfhir` vs. hand-rolled transport | **Resolved for v1:** custom adapter; revisit after the golden flow (§6) | After golden flow |
| Initializer coverage of bed domains | **Resolved at DH-003:** no bed domains in 2.12.0; verified additive REST seed (§4, §7) | Closed |
| Demo patients/content | **Resolved and empty-volume verified:** `OMRS_DEMO_PATIENTS=off|on` (default `off`) is independent of `OMRS_DEMO_CONTENT=on|off` (default upstream/as-shipped). `off` never deletes existing DB content | Closed; clinical vocabulary still needs sign-off |
| Keycloak session lifetimes | **Resolved for pilot (ADR-013):** 1h idle / 10h maximum / 5m token | Revisit after observed pilot workflow |
| **Cold-boot time** — ≈49 min on 4 vCPU, almost entirely OCL concept imports | Measured at build step 1. Warm boot ≈38s. Relevant to the restore drill (DH-015): a restore into a scratch stack inherits this cost | Noted; revisit if it affects DH-015 |
| Clinical sign-off on all clinical content | [`CLINICAL-SIGNOFF.md`](CLINICAL-SIGNOFF.md) exists and lists the installed lab sets with nothing pre-selected (§11). No item is signed off | Clinically live — **hard gate** |
| Horiba Micros60/Pentra60 analyzer test imports | Both upstream CBC CSVs repeatedly abort on the existing unique test description `LYM#(Whole Blood)`. Endpoints remain reachable, but neither analyzer configuration is verified. **Human Decision Required: Yes** — confirm whether either analyzer is in facility scope before changing laboratory tests | DH-023 / laboratory configuration |
| Adapter in-flight crash reconciliation | DH-025's opaque correlation, process-lifetime fence, coordinator, and before/after-write crash tests pass. The concrete OpenELIS writer/probe binding is not yet possible because the target resource shape is not built | Bind and demonstrate in DH-011 before activation; timeout-based claim stealing remains forbidden |

**Timeline note:** the pilot targets **1 September 2026**. Scope decisions above were taken partly
to keep the surface area small enough to be stable by then. Anything that grows the container count
between now and then should be weighed against that date.

---

## 17. The DHealth Exchange (post-pivot, ADR-042) — added 2026-09-08, does not replace §1–§16

**This section describes a new, additive direction, not a rewrite.** Everything in §1–§16 above
describes the maternity-first clinical stack as it actually is — built, partly verified, and still
the honest record of that scope. Nothing in it is deleted or superseded by this section. §16's
provisioning system in particular is a strong candidate foundation for the org-onboarding control
plane below, not a competing system.

**Why this section exists.** DECISIONS.md's ADR-042 records a product-direction pivot: DHealth is
no longer primarily a maternity/birth-centre EMR. It is a digital lab order + result exchange
connecting doctors/clinics/hospitals, patients, and independent laboratories — reachable through
DHealth's own Provider/Lab portals, or through an existing EMR/LIS via a connector. OpenMRS and
OpenELIS become the first two reference connectors, not the definition of the product.

**Recommended architecture — Option C, hybrid.**

```
                         DHEALTH EXCHANGE (core)
              organization identity · routing · order/result
              status machine · correlation · idempotency ·
              audit · dead-letter  — connector-agnostic
                              │
              ┌───────────────┴────────────────┐
              │                                │
        PROVIDER SIDE                      LAB SIDE
              │                                │
   DHealth Provider Portal            DHealth Lab Portal
        (persists clinical                (persists clinical
      payload — DHealth IS the          payload — DHealth IS the
      endpoint for this org)            endpoint for this org)
              OR                                OR
    OpenMRS connector                  OpenELIS connector
   (payload stays in OpenMRS;         (payload stays in OpenELIS;
    Exchange holds routing             Exchange holds routing
    metadata + correlation only)       metadata + correlation only)
                              │
                              ▼
                     PATIENT SECURE LINK
                  (WhatsApp/SMS-delivered —
                   ADR-003's real-credential
                   gate is now more load-bearing,
                   not less)
```

Pure "central exchange stores only routing metadata" was evaluated and rejected: the Provider/Lab
Portal requirement means DHealth **is** the endpoint system for portal-only organizations, so there
is nowhere else for an order's test selection or a lab's result to live for those participants.
Pure "central exchange stores full normalized clinical records" was rejected as an unbounded EMR
risk. §16's "never a shared multi-tenant clinical database" rule (ADR-020/ADR-021) is unaffected —
it is scoped to per-facility *clinical* stores (ADR-001), not to this routing/audit-scoped Exchange
core.

**Central data boundary (ADR-042).** The Exchange core may persist: organization identity,
patient/practitioner references (source-system + source-identity only, no national ID, no fuzzy
matching), requested test catalog entries, sender and selected-lab organization identity, a frozen
price snapshot at selection time, order status, the lab result value, result-delivery state, and
the doctor's follow-up disposition. It is explicitly **not** a longitudinal medical record: no
free-text clinical notes, no medication list, no diagnosis history, no general encounter data.

**2026-09-17 dated note (DH-IDENTITY-001/ADR-051):** a provider-scoped `Patient` directory record
now also exists, additively -- display name, optional exact date of birth, optional contact. This
does not widen the boundary above: still no clinical notes/medications/diagnosis, still no national
identity, still no fuzzy matching, still no cross-provider person graph. `PatientReference` keeps
its exact original meaning unchanged, now explicitly binding *to* a `Patient` rather than standing
in for one on its own. See `DECISIONS.md` ADR-051 for the full decision record.

**2026-09-17 dated note (DH-CLINIC-005):** order/result read APIs (`LabOrderResponse`,
`LabOrderSummaryResponse`, `ProviderOrderResultsResponse`, `ProviderReleaseVersionContentResponse`)
now additionally carry a `patient` field -- the identical minimal name/DOB/contact projection
ADR-051 already defined, resolved server-side through `patient_reference_id -> PatientReference
.patient_id -> Patient`, batched (one query per list, never N+1). Existing fields, including
`patient_reference_id` itself, are unchanged. No schema migration, no new table: this is a read-
side API addition only. Native Clinic's Orders/Results screens now render this identity as the
primary patient signal instead of a UUID fragment -- see `DECISIONS.md` ADR-051's 2026-09-17
clarification for the two durable UX rules this made explicit.

**2026-09-18 dated note (DH-CLINIC-006/ADR-052):** a new `ProviderLabRoute` model
(`exchange_provider_lab_routes`) records an explicit, platform-admin-configured fact -- "this
provider may route orders to this lab organization" -- answering the question `assign_by_provider`
(DH-EX-010) always needed but never had: which labs a provider is actually configured to work with.
It is deliberately orthogonal to two existing concepts it could otherwise be confused with:
`LabDirectoryProfile.listed` (public marketplace visibility) and `accepting_orders` (current
operational capacity) -- neither is ever auto-toggled from route activity, and an unlisted lab with
an active route remains a fully valid, fully assignable destination. `LabOrderResponse`/
`LabOrderSummaryResponse` gained a `lab_assignment` field (destination lab id/name/timestamp),
resolved from the canonical `LabSelection`, batched. See `DECISIONS.md` ADR-052 for the full
decision record, including why this is not a "no double entry" (ADR-043 Law 2) solution.

**2026-09-18 dated note (DH-CLINIC-007/ADR-048):** a new provider-scoped read-only endpoint,
`GET .../organizations/{provider_id}/result-reviews`, exposes the review-obligation queue ADR-048's
own versioned `DoctorReview` model already implied but never surfaced operationally -- one entry per
outstanding review obligation (never one per order; an order with two independently-pending amendment
versions surfaces as two entries), ordered by workflow age (`released_at`/`order_created_at`
ascending), never by clinical urgency. No new model, no migration, no new mutation: this is a single
joined read (`DoctorReview` join `LabOrder`, outer-join `LabResultReleaseVersion`) reusing
DH-CLINIC-005's batched patient-identity resolution, deliberately gated on `LabOrder.status ==
COMPLETED` since a `DoctorReview` row is created `PENDING` at order-creation time, long before any
result exists -- omitting that gate would wrongly surface every in-progress order as needing review.
The Clinic frontend's results page was rewritten around this endpoint as its single source for both
the pending-review and history sections, replacing a separate `listOrders`-based list. See
`DECISIONS.md` ADR-048's 2026-09-18 dated clarification for the resulting UX operating rule (review
actions always target the exact displayed version, never "latest").

**2026-09-18 dated note (DH-LAB-001):** the first DHealth Lab frontend (`ui/src/features/lab/`) now
exists, against the pre-existing lab-workflow backend (DH-EX-010/011/012) -- no new backend was
built for it beyond a lab-safe patient-identity projection and its N+1-safe batched resolution
(below). Scope is deliberately narrow: authentication, lab-organization context, assigned-order
queue, safe order detail, accept/reject. Result entry, result release, collection recording, and
LIS/Connect integration are explicitly out of scope, deferred to DH-LAB-002 through DH-LAB-005. The
lab-safe `LabPatientIdentityResponse` (`patient_id`/`display_name`/`date_of_birth` only, no contact
fields at all) extends DH-CLINIC-005's "identity from `Patient`, never `PatientReference`'s own
snapshot" rule to Lab. Because a lab's own queue can span multiple distinct ordering providers
(unlike Clinic's always-single-provider lists), the batched resolution groups patient-reference ids
by provider and calls the existing provider-scoped batch resolver once per distinct provider,
bounded by provider count rather than order count. **This ticket also corrected a documentation
drift** (recorded in `DECISIONS.md` ADR-043's own 2026-09-18 dated clarification): native DHealth
Lab and DHealth Connect are parallel, permanently supported capabilities, not a fork to choose
between before more native work continues.

**2026-09-18 dated note (DH-LAB-002):** collection-milestone recording and start-processing are now
wired into Native Lab (`LabOrdersPage`), against the pre-existing `LabWorkflowRepository.
record_collection`/`start_processing` (DH-EX-011) -- again no new backend workflow endpoint, table,
or `LabOrderStatus` value. Two things did change backend-side: (1) `list_for_lab`'s query gained
`.options(selectinload(LabOrder.items))`, closing the `LabOrder.items` lazy-load N+1 that DH-EX-011
found and explicitly left unfixed as out of its own scope -- one bounded `WHERE lab_order_id IN
(...)` query per queue read now, not one per order; (2) two backend authorization negative-control
tests were added (`LAB_RESULT_RELEASER`-alone denied on both `record-collection` and
`start-processing`) that DH-EX-011 could not have written, since that role was introduced afterward
by DH-EX-012. Frontend-side, collection and processing each get their own explicit confirmation step
(reusing the inline-panel-confirmation pattern DH-LAB-001 established for reject, which keeps Patient
identity visible throughout per DH-LAB-002's own §44) and their own lost-response recovery predicate
-- deliberately *not* unified into one generic mutation helper with accept/reject, since collection
(no `LabOrder.status` change at all, success judged by `collection_recorded_at`) and processing (a
genuine fourth "prerequisite not met" outcome the other three actions never have) each have a
recovery shape the others don't. `LabHomePage`'s own count-request effect is hardened to the same
generation/`currentLabRef`/`AbortController` discipline `LabOrdersPage` already used, closing a gap
DH-LAB-001 disclosed but left open. Result entry, result release, and LIS/Connect integration remain
explicitly out of scope, deferred to DH-LAB-003 onward.

**2026-09-18 dated note (DH-LAB-003, ADR-053):** a controlled, immutable, `LabOffering`-scoped
result-structure template layer now exists (`LabResultTemplate`/`LabResultTemplateObservation`,
migration `0024_result_templates` -- the first new schema since DH-LAB-001). Scoped to
`LabOffering`, deliberately never to `TestCatalogEntry`: two labs offering the identical catalog
test may report a different observation structure without that being a second ordered-service
identity. Activation (`LabResultTemplateRepository.activate_template`) is a `FOR UPDATE`-locked
compare-and-swap mirroring `CatalogRepository.set_price`'s own discover-and-swap-in-one-statement
discipline -- the owning offering is locked first, then the current active row (if any) is
discovered and deactivated in one `UPDATE ... RETURNING` before the target is activated; an
already-active-target reactivation is always a safe replay, identical to `set_price`'s own
already-correct-value exception. No reference-range or clinical-interpretation field exists on this
model at all (this product has no approved applicability model for a static range yet). The
existing low-level `LabObservation`/`record_observation` API (DH-EX-012C/ADR-047) is completely
untouched -- templates are additive Native Lab configuration DH-LAB-004's own future human-entry
mutation will require, never a new constraint retroactively imposed on the interoperability
primitive a future Connect-reported observation still needs to use freely. A new batched, read-only
`GET .../lab-orders/{order_id}/result-entry-plan` resolves each `IN_PROGRESS` item's currently
active template for the frontend (never one query per item: one batched offering lookup, one
batched catalog-entry lookup for `is_synthetic`, and `get_active_templates_for_offerings`'s own two
batched queries). A currently-assigned order's item with no `LabOffering` row at the selected lab
at all is a structural-integrity failure, not a normal missing-config state -- `assign_by_provider`/
`select` both require one at assignment time, and offering rows are never deleted. Frontend-side,
`LabOrdersPage`'s `IN_PROGRESS` branch now renders a read-only structure preview, loaded as a third
request in the panel's own lifecycle sharing `panelGenerationRef` rather than a dedicated ref of its
own -- any event that already invalidates the panel (a different order, closing it, a lab switch)
invalidates the plan identically. No result-entry, no result value, no LIS/Connect code exists in
this ticket -- DH-LAB-004 is next, and must pin/validate the exact template version at write time
(ADR-053 Decision 11).

**2026-09-18 dated note (DH-LAB-004, ADR-053 addendum + ADR-044 addendum):** the value-entry
mutation now exists. Migration `0025_native_result_binding` adds nullable
`LabResult.result_template_id`/`LabObservation.template_observation_definition_id` (both `ON
DELETE RESTRICT`) plus a partial unique index enforcing one effective observation per controlled
definition per container. `LabResultRepository.record_native_values`'s first write for an item
locks the owning `LabOffering FOR UPDATE` (the identical lock `activate_template`'s own CAS
acquires), pins the container to whichever template is active at that locked instant, and the pin
survives any later activation change -- a controlled conflict (`NoActiveTemplateForOffering`) if
activation wins the lock first instead. The browser submits only `template_id` and, per value,
`definition_id` + a value; `source_code`/`display_label`/`result_type`/`numeric_unit` are always
derived server-side from the pinned template's own definition. Batch replay is idempotent per
definition (exact value -> safe no-op; different value -> `ObservationValueConflict`, never
overwritten); every check for a batch resolves before any insert. The raw, free-structure
`record_observation`/`correct_observation` API now refuses to mutate a Native-template-bound
container/observation at all (`TemplateBoundContainerRefusesUnstructuredWrite`, proven with a
genuine red/green discrimination test) -- a non-template-bound container is entirely unaffected.
`complete_entry` additionally requires every `required=true` pinned-template definition to have a
value. A new batched `GET .../result-entry-workspace` resolves each item's governing template
(pinned, even if no longer active, or the offering's current active template for an unstarted
item) plus entered values, truthfully distinguishing `NATIVE_TEMPLATE`/`LEGACY_OR_EXTERNAL`/
`UNSTARTED_NO_TEMPLATE`. A new least-privilege `GET /organizations/{lab_id}/release-queue`
(`LAB_RESULT_RELEASER` only) never broadens the `LAB_TECH` work queue. **The release-inspection
fingerprint** (`compute_result_fingerprint`, a SHA-256 digest over the canonical sorted set of
every effective `(item_id, observation_id)` pair -- immutable identities, never raw values) closes
the race where a releaser inspects a result set, a technician corrects a value, and a stale
release would otherwise release an unseen set: `POST .../release-results` now requires
`expected_result_fingerprint`, checked inside the same `LabOrder`-locked transaction before any
release row is created, refusing with `StaleResultFingerprint` (also proven with a genuine
red/green discrimination test) on mismatch. Both the first-write-pin-vs-activation race and the
release-vs-correction race were qualified against a real, natively-installed PostgreSQL instance
found in this session's sandbox (`tests/integration/test_dhlab004_postgres_qualification.py`,
executed directly, 50+ repetitions, zero flakes) -- a stronger evidence position than DH-LAB-002/
DH-LAB-003 reached, both blocked by a Docker-image-pull network limitation this native-Postgres
path does not depend on. Frontend-side, `NativeResultEntryForm` replaces DH-LAB-003's read-only
preview (explicit "Enregistrer les valeurs" save, no autosave, saved values shown read-only from
an authoritative re-read, "Terminer la saisie" gated and confirmed); a new `LabReleasePage` gives
releaser-only users the release queue/inspection/confirmation UI DH-LAB-001 only had a placeholder
for, with its own confirmation wording, stale-fingerprint forced-re-inspection handling, and
lost-response recovery via authoritative re-read. Correction/amendment UX for a Native-template-
bound result is deliberately not built here -- DH-LAB-005's own scope (ADR-053 Decision 11).

**2026-09-18 dated note (DH-LAB-005, ADR-045 addendum + ADR-048 addendum):** the correction/
amendment gap above is now closed, reusing existing infrastructure exactly rather than duplicating
it. `LabResultRepository.correct_native_value` (pre-release) and
`LabResultAmendmentRepository.correct_native_value` (amendment-scoped) are the Native controlled
counterparts of the existing raw `correct_observation` methods: the browser submits only the new
value, and every structural field -- including the pinned `result_template_id`, unchanged by the
correction -- is derived server-side from the predecessor's own
`template_observation_definition_id`. No new migration: both new methods reuse the DH-LAB-004
schema unchanged. The raw amendment `correct_observation` is newly hardened to match the raw
pre-release one DH-LAB-004 already hardened (`TemplateBoundContainerRefusesUnstructuredWrite`,
proven with a second genuine red/green discrimination test on the amendment side). **Amendment
release gains the identical fingerprint gate the original release already had**:
`AmendmentInspectionRecord.result_fingerprint` and `release_amendment`'s new
`expected_result_fingerprint` reuse `compute_result_fingerprint` -- one algorithm, now four call
sites -- checked inside the same `LabOrder`-locked transaction, before any release-version row is
created, raising the already-existing `StaleResultFingerprint` on mismatch (no new exception). A
new least-privilege `GET /organizations/{lab_id}/amendment-release-queue` mirrors the existing
release queue's own convention exactly, never listing a `PREPARING` amendment with zero revisions.
A new `GET .../amendments` lets the frontend rediscover a `PREPARING` amendment after a page
refresh, and a new lab-facing `GET .../release-versions/{release_version_id}` mirrors the
provider-facing exact-version-content read that already existed, reconstructed from the immutable
manifest only. All four new concurrency races (concurrent Native corrections, correction-vs-
release, amendment-correction-vs-amendment-release, two-releasers-racing-one-amendment) were
qualified against the same native PostgreSQL instance DH-LAB-004 first located, run clean 4x in a
row. Frontend-side, `NativeResultEntryForm` gains a Corriger control and correction-history
display; a new `AmendmentWorkspace` component is the per-amendment correction workspace; a new
`ReleaseVersionHistory` component exposes every version's exact content on demand; `LabOrdersPage`'s
`COMPLETED` branch and `LabReleasePage`'s "Libération" tab (extended with an "Amendements" section,
never a second page) both drive amendment discovery/start/resume/release entirely from server
reads, never local memory. LIS/Connect integration and demo seed data remain deferred to
DH-DEMO-001+.

**Component reuse, from the DH-PIVOT-001 audit (evidence: adapter/src/daher_adapter/):**

| Component | Verdict |
|---|---|
| Message ledger, dead-letter/retry, audit log, crash-recovery coordinator (`ledger.py`, `dead_letter.py`, `audit.py`, `recovery.py`) | **KEEP AS CORE** — already connector-agnostic |
| Patient/practitioner identity pattern (`openelis.py`'s `PATIENT_IDENTITY_SYSTEM`, ADR-037) | Pattern reusable, concrete binding is OpenMRS/OpenELIS-connector-specific |
| Order poller/watermark, source-event model (`order_poller.py`, `watermark.py`, `source_events.py`) | **KEEP AS CONNECTOR** — FHIR/OpenMRS-shaped, not source-neutral |
| Provisioning trust boundary / job-queue / pull-agent (§16) | **KEEP AS CORE** for org onboarding; `Facility` model needs an `Organization`/`OrganizationUser` refactor |
| Keycloak realm | **KEEP AS CONNECTOR-scoped** — single-tenant-shaped today, needs a multi-org identity answer |
| `ui/` design-system | **KEEP AS CORE** candidate for new portal UIs |
| `ui/` feature code (`features/patients`, `features/admission`, `features/maternity`) | Not reusable as-is — 100% OpenMRS/maternity-coupled |

**Canonical internal model:** DHealth-owned domain model (`adapter/src/daher_adapter/exchange/`),
not FHIR resources. FHIR remains a connector/interoperability format where a connector needs it —
exactly how the adapter already uses it for OpenMRS/OpenELIS — not the Exchange's own database
shape.

**Status:** DH-EX-001 (Exchange core domain + persistence foundation) is committed and
exact-SHA CI qualified — real `pytest`/`ruff`/`alembic upgrade head` and real PostgreSQL
concurrency/migration qualification, per DH-EX-001R's independent review and DH-CI-001's
CI-hygiene fix (2026-09-08/09; qualified at commit `4f9c1d10dc4543aa785c0bee939108a339b45e61`, CI
run `34317829117`, all required jobs green).

**DH-EX-002 (organization onboarding, OIDC authentication, tenant isolation) is also committed and
exact-SHA CI qualified** (2026-09-09; qualified at commit `dc3d13b7a8d6a53422091b56138718a73e096715`,
per DH-EX-002R's independent security/runtime review, CI run `34390897519`, all required jobs
green). Qualified: exact OIDC `(issuer, subject)` identity, DB-owned organization membership/role
authorization separate from authentication, a provider/lab tenant role matrix, platform-admin as a
single Keycloak realm-role claim never derived from an organization role, and a transactionally
authoritative security-mutation audit (mutation and its audit row commit or roll back together, one
PostgreSQL transaction). A minimal `/exchange/v1` API exists for exactly this: identity bootstrap,
organization creation, and membership/role management — no lab-order API, no pricing/selection API,
no patient-link API. **Real Keycloak qualification was NOT run** (registry unreachable in the
qualification sandbox) and remains a deployment/pilot gate, not closed by this status.

**DH-EX-003 (lab directory, synthetic test catalog, lab offerings, DJF pricing, whole-order lab
eligibility) is also committed and exact-SHA CI qualified** (2026-09-09/10; qualified at commit
`33c5edb9ed76ac6bc0f22b1663de3b868dce2364`, per DH-EX-003R's independent marketplace/security/
data-integrity review, CI run `34432960783`, all required jobs green). Qualified: a `LabDirectoryProfile`
per LAB organization (`listed`/`accepting_orders` both default `false`), a synthetic-only DHealth
test catalog HTTP API (a client cannot submit `is_synthetic=false`; ordinary network reads expose
ACTIVE+SYNTHETIC entries only), lab offering administration with anti-enumeration cross-tenant
refusal, DJF-only current pricing with optimistic concurrency (CAS) and immutable price history, and
the whole-order eligibility query that answers "for this exact requested set of tests, which
participating labs can fulfil the **entire** order, and what does each currently charge?" — never a
partial-lab result, and (DH-EX-003R's fix) evaluated under PostgreSQL `REPEATABLE READ` so its
multi-pass query observes one coherent snapshot. DH-EX-002's transactional security-audit pattern
was generalized, not replaced, to cover these new mutation types. This is a read-only backend query
and admin API — it does not create a `LabSelection`, does not freeze a `LabOrderItem` price, and
there is no patient-facing marketplace UI yet.

**DH-EX-004 (DHealth-native Provider lab-order creation) is also committed and exact-SHA CI
qualified** (2026-09-10; qualified at commit `6cc6bdac45faf07a843a08699f8c1b8f269b313f`, per
DH-EX-004R's independent clinical-order/security/data-integrity review, CI run `34441434606`, all
required jobs green). Qualified: `POST/GET /exchange/v1/organizations/{provider_id}/orders` and
`GET .../orders/{order_id}`, gated by a new `require_clinician` authorization check that
deliberately does **not** let platform admin bypass — clinical ordering requires an ACTIVE
`CLINICIAN` membership in the ACTIVE provider organization itself, not network-administration
authority. The ordering practitioner is always server-derived from the authenticated
`ExchangeIdentity` (never client-supplied) via a durable, UI-independent source-identity namespace,
proven to produce a distinct `PractitionerReference` per provider organization for the same human. A
new order requires provider-scoped canonical-UUIDv4 idempotency and creates one `LabOrder` +
`LabOrderItem`s + `PENDING` `DoctorReview` + `PENDING` `ResultDelivery`, atomically with the
authoritative `lab_order_created` audit row, landing at `AWAITING_LAB_SELECTION`. It does **not**
create a `LabSelection`, does not require a currently-eligible lab to exist, and does not freeze a
price — those remain later work.

**DH-EX-005 (secure patient order access + atomic patient lab selection) is also committed and
exact-SHA CI qualified** (2026-09-10; qualified at commit
`1a382f86df82c35e20a6bf79bccb22961d65cf47`, per DH-EX-005R's independent security/concurrency
review, CI run `34514393091`, all required jobs green). Qualified: a clinician (the same
`require_clinician` boundary as DH-EX-004) issues an opaque, order-scoped 256-bit bearer grant
(`POST/DELETE .../orders/{order_id}/patient-access`) — only its SHA-256 digest is ever persisted.
Three patient-scoped routes (`GET /patient/order`, `GET /patient/order/lab-options`,
`POST /patient/order/selection`), authenticated by that bearer grant alone, are a structurally
separate code path from OIDC and accept no client-supplied order/provider/patient id. Selection
re-runs the full DH-EX-003 whole-order eligibility contract, requires the patient's exact current
price-id consent, and re-validates the access grant itself (order match, unrevoked, unexpired)
inside the same transaction that freezes `LabOrderItem` prices, creates one `LabSelection`
(carrying `selected_via_patient_access_grant_id` as its own provenance, never a fabricated
`ExchangeIdentity`), and transitions the order to `SELECTED`. DH-EX-005R found and fixed one
BLOCKER (selection originally trusted grant/order identifiers resolved before its own mutation
transaction began, letting a grant revoked in that gap still authorize a selection) — fixed and
proven via 20 real-PostgreSQL selection-vs-revoke races. It does **not** mean a selected lab can see
or act on its order, and no result is ever produced or delivered — those remain later work.

**DH-EX-006 (selected-lab assigned-order workflow) is also committed, pushed to `origin/master`, and
exact-SHA CI qualified** (commit `1bfb08a19fc3f135e7e552f77b73acad4967c88d`, parent
`fcb6c6fd7dcbfda363da1ce84706ca19fdb3310f`, per DH-EX-006R's independent review; CI run
`34537938219`, all 9 required jobs green, adapter `574 passed / 39 skipped`, migration head
`0009_exchange_lab_workflow` — confirmed by Liban directly against the real GitHub Actions run; see
PROJECT_STATE.md's DH-EX-006 · FINAL CLOSURE entry). Established: a `LAB_TECH`
in an ACTIVE lab organization (`require_lab_tech`, mirroring `require_clinician`, no
platform-admin/`ORG_ADMIN`-alone bypass) can list/read the orders assigned to their lab and move one
through `SELECTED`→`ACCEPTED`, `SELECTED`→`REJECTED`, or `ACCEPTED`→`IN_PROGRESS`. Assignment
ownership is `LabSelection` alone, never re-derived from DH-EX-003 marketplace eligibility (listing/
accepting/offering-active/price), so a lab that later becomes unlisted, stops accepting, deactivates
an offering, or changes its price still owns and can work an order a patient already assigned it;
frozen selection-time prices are never rewritten, and patient-grant revocation/expiry is independent
of the assignment. Each mutation locks the order `FOR UPDATE`, checks assignment ownership *before*
evaluating replay-vs-conflict, mutates, and writes its security-audit row inside one transaction.
There is no `/complete` route and no path to `COMPLETED` anywhere in this work — DH-EX-007's scope.

**DH-EX-007 (lab result submission, order completion, provider result retrieval, and doctor
review) is also committed, pushed to `origin/master`, and exact-SHA CI-qualified** (commit
`30c5212`, CI migration guard advanced to `0010_exchange_results_workflow` in a follow-up commit
`053004a`; both merged via PR #1 at merge commit `d3a72d5c`, CI run `34649375844` — `event=push`,
`branch=master`, `head_sha=d3a72d5c`, `status=completed`, `conclusion=success`, 9/9 jobs
`success`, independently confirmed via authenticated GitHub API in the DH-EX-007D2 pass, 2026-09-12,
after the original DH-EX-007D pass had recorded this as unverified — see `PROJECT_STATE.md`'s
DH-EX-007 entry for the full evidence boundary and that correction). Established: the selected lab's `LAB_TECH` (same `require_lab_tech` boundary as DH-EX-006) records
one immutable result per ordered item, only once the order is `IN_PROGRESS`; result mutations
serialize at the `LabOrder` aggregate; the order transitions to `COMPLETED` only once PostgreSQL
proves every item now has a result — no application-level counting. The ordering provider (same
`require_clinician` boundary as DH-EX-004) can then retrieve the completed results, and record one
of two terminal review dispositions (`no-follow-up-required` / `follow-up-required`) via a fresh
practitioner binding resolved the same way DH-EX-004 resolves the ordering clinician. The
patient-facing path is unchanged from DH-EX-005 — `PatientOrderResponse` still excludes every
result value by construction (`extra="forbid"`, no result field defined), and no new patient route
was added. `ResultDelivery` stays `PENDING`: this ticket adds no delivery mechanism, message, or
notification for either the provider or the patient. Migration `0010_exchange_results_workflow` is
the ninth Exchange migration; `EXPECTED_HEAD` in `.github/workflows/ci.yml` agrees with it. Per the
implementation commit's own message: 605 passed / 42 skipped locally, real-PostgreSQL
result-concurrency qualification passed, one HIGH finding from the ticket's own independent review
fixed (API max-length validation widened to match the `LabResult` database bounds); real Keycloak
and a manual live/browser demonstration were explicitly not run.

Still not built, no connector wiring changed: the Provider Portal, the Lab Portal, patient UI, and
WhatsApp/SMS integration — none of this is equivalent to a completed Exchange MVP. No file under
`openmrs-config/` or `openelis-config/` has changed, and the connector Keycloak realm
(`daher-realm.json`) is untouched. Full audit, gap analysis, and the proposed ticket sequence
(DH-EX-001 through DH-EX-010): DH-PIVOT-001 (2026-09-08); build/review/closure detail:
PROJECT_STATE.md's DH-EX-001 through DH-EX-007 entries.

**DH-EX-008 (deployment wiring) through DH-EX-014A (release-version manifest + versioned review
foundation) extend the Exchange well past DH-EX-007** — reconciled into this document 2026-09-13/14
(DH-FIELD-001/DH-EX-014D; full per-ticket evidence remains in each ticket's own `PROJECT_STATE.md`
entry, not restated here):

- **DH-EX-008** wires the Exchange's existing OIDC auth code into the real Docker/Keycloak/Caddy
  deployment (env contract, realm import, `exchange.$BASE_DOMAIN` routing, a dedicated
  `/health/exchange-ready` endpoint). Statically verified; live Keycloak/Caddy qualification remains
  blocked in this sandbox (registry image pull unreachable) and is a separate follow-up
  (DH-EX-008R) — not a claim that Exchange auth works against a running Keycloak.
- **DH-EX-009** adds `POST .../organizations/{provider_id}/patient-references`, an exact-source-
  identity get-or-create bind — the first route letting a provider create a patient reference
  rather than requiring one to pre-exist. Narrower than full patient lookup/registration
  (§10 item 1 below is downgraded, not closed).
- **DH-CLINIC-002** adds native, clinician-authorized provider-local patient selection without
  changing that identity boundary: bounded `GET .../patient-references` listing,
  `POST .../patient-references/search` deterministic literal substring retrieval (JSON body, so
  names/contact fragments do not enter access-log URLs), and provider-scoped
  `GET .../patient-references/{patient_reference_id}` reconciliation/detail. The lookup payload is
  a separate minimal view (`patient_reference_id`, optional display/contact snapshots,
  `local_reference`) and omits source namespace internals. Native creation reuses DH-EX-009's exact
  bind with client-minted UUIDv4 values under `dhealth-clinic` /
  `urn:dhealth:clinic:patient-reference-id`; unresolved attempts retain the same tuple within the
  authenticated user + provider UI scope. No demographic matching, merge, cross-provider lookup,
  or longitudinal patient profile is introduced.
- **DH-EX-010/DH-EX-010A/DH-EX-010A1** add `POST .../orders/{order_id}/assign-lab`, a
  provider/facility-assigned routing path that shares the same `LabSelection` row as DH-EX-005's
  patient-selection path but never touches price consent — closing ADR-043's launch-option (a) gap
  the original pivot audit left open. DH-EX-010A/010A1 then extend the patient-access-grant
  mechanism (`access_mode`: `LAB_SELECTION` vs. `ORDER_READ`) so a patient link issued after
  provider assignment reads the already-settled order without regaining lab-choice authority, and
  close a downgrade-safety gap in the migration that introduced it.
- **DH-EX-011** adds `LabSampleCollection` and `POST .../lab-orders/{order_id}/record-collection` —
  an order-level, immutable "collection was recorded in DHealth at this time" fact, required before
  `start-processing` may run. This is a milestone, not specimen management: no container, volume,
  site, handling, storage, or adequacy state exists in the schema.
- **DH-EX-012 (ADR-044)** is the most structurally significant of this group: it removes DH-EX-007's
  automatic `IN_PROGRESS→COMPLETED` transition entirely. Recording a result — even an order's last
  missing one — now only ever reports `ready_for_release`; a new `LAB_RESULT_RELEASER` organization
  role (LAB-only, additive alongside `LAB_TECH` — the same human may hold and use both, no forced
  second person) must call a new `POST .../release-results` endpoint before `LabOrder.status` ever
  becomes `COMPLETED`. A new immutable, order-level `LabResultRelease` row (mirroring
  `LabSampleCollection`'s own shape) records who released and when; a pre-DH-EX-012 order already
  `COMPLETED` is grandfathered with no fabricated release row. Provider/clinician visibility gates
  and `ResultDelivery` are unchanged — only the *producer* of `COMPLETED` moved.
- **DH-EX-012A (ADR-045)** audited the gap DH-EX-012 sharpened rather than created: `LabResult`
  was, at that point, strictly insert-only — no `UPDATE` path anywhere in the package, no
  entering-actor column, no revision/version/supersedes metadata. It distinguished pre-release
  *correction* from post-release *amendment* as two separate, unresolved problems and evaluated
  options for each without adopting any.
- **DH-EX-012C (ADR-047, IMPLEMENTED, 2026-09-13)** replaces the `LabOrderItem → one LabResult`
  shape with `LabOrderItem → LabResult` (now a per-item *container* — identity plus one explicit
  `entry_completed_at`/`entry_completed_by_identity_id` completion signal, no value columns) `→
  LabObservation[]` (0..N observations per container, each carrying its own hybrid-namespace
  identity and value). Real field evidence (DH-FIELD-001, ADR-046) established that one ordered
  diagnostic service may return multiple observations; ADR-047 (recommended by DH-EX-012B,
  accepted by Liban with the zero-observation-completion question resolved as **no** — an item may
  never complete with zero observations) is the schema that satisfies it. Two new endpoints
  (`POST .../items/{item_id}/observations`, `POST .../items/{item_id}/complete-result-entry`)
  join the existing single-observation convenience route, which now atomically creates the
  container, records one observation, and completes the entry in one call. Completeness is a
  single explicit signal, never inferred by counting observations; release's own completeness
  re-check additionally defends against a malformed "complete but zero observations" container,
  demonstrated via a real-PostgreSQL negative control. Migration `0016_exchange_observations`
  migrates every legacy scalar `LabResult` row losslessly into one container plus one observation,
  with four independent downgrade guards, each demonstrated against real PostgreSQL — including a
  guard-removal control proving real, silent data loss occurs without them. No panel composition,
  analyte vocabulary, or clinical content of any kind was invented — observation identity is
  caller-supplied or, for the simple case, reused from the ordered item's own catalog snapshot.
- **DH-EX-013 (ADR-045 Questions 1–3, IMPLEMENTED, 2026-09-13)** resolves pre-release correction
  now that the underlying schema is stable: a `LabObservation` may be corrected before release via
  an immutable supersession chain (`supersedes_observation_id`, self-referential and `UNIQUE`;
  `superseded_at`, `NULL` iff effective — an explicit relational fact, never inferred from timestamp
  ordering). `POST .../observations/{observation_id}/correct` inserts the new effective observation
  and marks its predecessor superseded in one transaction; `LAB_TECH`-only, matching entry authority
  (`LAB_RESULT_RELEASER` not required), no reason field accepted or stored. The same `LabOrder`-
  locked `IN_PROGRESS` gate every other entry mutation already uses is what makes correction
  structurally refuse once the order is released — one shared mechanism, not a second bolted-on
  check. Every new observation (entry or correction) now carries `recorded_by_identity_id`;
  pre-existing rows keep it `NULL`, never backfilled. Release, provider reads, and lab inspection all
  filter to the effective observation only (`superseded_at IS NULL`); a new
  `GET .../observations/history` endpoint, `LAB_TECH`/`LAB_RESULT_RELEASER`-only, is the one place
  the full chain (superseded and effective alike) may be read. Migration `0017_observation_revisions`
  adds a full `UNIQUE` constraint on `supersedes_observation_id` as the database-level concurrency
  backstop behind the existing `LabOrder FOR UPDATE` lock (no partial index needed — PostgreSQL
  treats every `NULL` as distinct under `UNIQUE`, so the never-corrected majority of observations
  are unconstrained by it) — confirmed necessary, not redundant, by a
  negative control that reproduced real corruption (10 concurrent successors of one predecessor) only
  once *both* defenses were removed together. Post-release amendment (ADR-045 Questions 4–5) was
  still OPEN at this point, resolved into ADR-048's architecture by the next two tickets below.
- **DH-EX-014D (ADR-048, DECISION PACKET, 2026-09-14)** audits the naive-post-release-mutation risk
  precisely (the provider-read route re-queries live effective observations gated only on
  `LabOrder.status`, with no binding to which observations a specific release authorized) and
  recommends Model 3 — an amendment episode plus an immutable release-version manifest — over two
  other evaluated models, without implementing any of it. Liban accepted Model 3, ADR-045 Question 4
  Option C (versioned `DoctorReview`), and Question 5 (amendment required before the first
  real-patient pilot), appended to ADR-048.
- **DH-EX-014A (ADR-048 Model 3, IMPLEMENTED, 2026-09-14)** builds the accepted architecture's
  **versioning foundation only** — no amendment mutation exists yet. A new `LabResultReleaseVersion`
  (`UNIQUE(lab_order_id, version_number)`, a `CHECK` binding version 1 to the unchanged, immutable
  `LabResultRelease`) and `LabResultReleaseManifestEntry` (binds by `LabObservation.id` only, never
  duplicating a scalar value) let a release name the exact effective observation set it authorized.
  `LabResultReleaseRepository.release` now atomically creates the release, its Version 1, the full
  manifest, and binds the order's existing `PENDING` `DoctorReview` to that version — all four
  commit together or none do. `DoctorReview.lab_order_id` lost its database-`UNIQUE` constraint (a
  real migration, `0018_exchange_release_versions`) in favor of a plain `UNIQUE` on the new
  `release_version_id` plus a genuine partial unique index for the one legacy/unbound row per order
  — resolved by explicit `version_number` ordering, never by `reviewed_at` or any other timestamp.
  Provider reads of an explicit-release order now derive content from this manifest, never live
  state, closing exactly the risk DH-EX-014D identified; a grandfathered order (no `LabResultRelease`)
  is unaffected, using its unchanged legacy read. Original-release backfill for every pre-existing
  release is reconstruction from a proven invariant (every observation-mutating code path is gated
  on `IN_PROGRESS`, confirmed by an exhaustive grep of every `LabObservation` write site) — never a
  fabrication. Two downgrade guards, each demonstrated against real PostgreSQL including a
  guard-removal control that reproduced real, silent semantic loss (an amendment's entire existence
- **Post-release amendment is implemented (ADR-045 Questions 4–5, ADR-048 Model 3,
  DH-EX-014B):** an amendment episode (`LabResultAmendment`, PREPARING/RELEASED states) allows
  a LAB_TECH to prepare corrections while leaving existing releases immutable. When an authorized
  LAB_RESULT_RELEASER releases the amendment, a new `LabResultReleaseVersion` is created with its
  own immutable manifest and a fresh `PENDING` `DoctorReview` is created bound to that version.

Full per-ticket evidence: `PROJECT_STATE.md`'s DH-EX-008 through DH-EX-014B entries.

**DH-EX-014B update (2026-09-14):** The accepted amendment episode is now implemented on top of
the immutable version foundation. A LAB_TECH starts and corrects one PREPARING episode; a separate
LAB_RESULT_RELEASER releases a new manifest/version and fresh version-bound DoctorReview. Provider
history and exact-version reads use immutable manifests, while released amendment inspection never
shows a later proposal. SQLite tests (136 passed) and real-PostgreSQL qualification (33 passed
across 6 test suites) pass; real Keycloak qualification, manual live/browser demonstration, and
production deployment remain unrun, so no pilot-readiness claim follows.

### Durable Provider Lab Order Intent (ADR-049, DH-CLINIC-003C, 2026-09-16)

A native Clinic lab order (through the DHealth Clinic frontend's own UI, not an EMR connector) is
never created directly. It is always mediated by a backend-owned, immutable `LabOrderIntent` —
Option A of the three options DH-CLINIC-003B's own decision gate framed (ADR-049 records the
rejected Options B and C and why). The browser is not the source of truth for clinical order
content once a durable intent exists.

**New order flow.**

1. Clinician chooses a patient (DH-CLINIC-002's provider-local patient reference).
2. Clinician chooses the exact diagnostic services to order.
3. Frontend generates (or reuses, on retry) an idempotency key.
4. Frontend registers a durable `LabOrderIntent`: `POST .../order-intents`.
5. Backend freezes, in one row: provider organization, patient reference, the practitioner
   (server-derived from the authenticated clinician — the request schema has no
   `practitioner_reference_id` field, so the browser cannot supply or override it), the exact
   service set, and an immutable code/display-name snapshot of each selected catalog entry.
6. Browser persists only an opaque technical recovery identity (allowlist below) — never the
   service selection itself.
7. Frontend commits the intent: `POST .../order-intents/{id}/commit`.
8. Backend creates at most one `LabOrder` from the frozen intent content only; the commit endpoint
   accepts no request body, so nothing client-supplied can override patient/practitioner/services at
   this step.
9. `LabOrder.intent_id == LabOrderIntent.id` and `LabOrderIntent.committed_lab_order_id ==
   LabOrder.id` are written and checked together, in the same transaction.

The legacy `POST .../orders` endpoint (kept for backward compatibility) is not a second creation
path for *new* orders — it acquires or consumes the identical durable-intent reservation for its
`(provider_organization_id, idempotency_key)` pair before creating anything. One narrow, explicit
exception: if a pre-003C order (`intent_id IS NULL`) already exists under that exact key, the
legacy route checks for it *before* reserving a new intent, and an exact canonical match replays
it unchanged (`200`, no new intent, `intent_id` still `NULL`) — preserving the endpoint's original
pre-003C idempotent-replay contract for orders that predate this model, never retroactively
claiming a historical row into a new intent. A mismatch still conflicts (`409`).

**Reload/recovery flow.** If a technical recovery identity exists in browser storage on open:

1. First reconcile an already-committed `LabOrder` by idempotency key
   (`GET .../orders/by-idempotency-key/{key}`).
2. If none is found (a 404 here is not proof the order never committed — it only means "not found
   at this moment"), retrieve the durable intent by id (if known) or idempotency key.
3. Validate the recovered intent's `(provider_organization_id, patient_reference_id,
   idempotency_key, id)` tuple against the current scope. Any mismatch fails closed: nothing
   renders or commits, and the recovery record is retained, not discarded.
4. If the intent is already committed, load the authoritative order.
5. If uncommitted, restore the exact backend item snapshots — never a browser reconstruction of
   which services were selected.
6. The restored state remains immutable/read-only ("Modifier les services" does not appear);
   committing replays the exact stored intent only.
7. A definitive commit-step error at this point (e.g. `422`, `403`) retains the recovery identity —
   it is never treated as a fresh, unregistered attempt.

A transient lookup failure does not reopen service selection, and a stale asynchronous response for
one patient/provider scope cannot attach its content to a different scope the clinician has since
switched to (verified by tests that genuinely defer the mocked response across a scope switch, not
merely by removing an identity check).

**Persistent browser boundary** (`ui/src/features/clinic/pendingOrderStorage.ts`, verified against
the code as shipped — an exact allowlist, not an approximation): `user_id`, `provider_id`,
`patient_reference_id`, `idempotency_key`, `created_at`, and two optional fields —
`intent_id` (once the backend intent is known) and `confirmed_order_id` (once a commit has
succeeded). Never persisted: catalog entry ids, service codes, service display names, patient
display name or contact details, result values, or a client-supplied practitioner. There is no
destructive TTL of any kind — an old, unresolved recovery record is never silently discarded, since
that would make a potentially-committed order unrecoverable and risk a clinician re-submitting it.

**Concurrency model — the specific races that are qualified, not a general "race-free" claim.**
Real PostgreSQL (`test_dhex003c_postgres_qualification.py`, CI's disposable `postgres:16-alpine`
service, never a facility database) qualifies: concurrent commits of the same intent converge on
exactly one `LabOrder` (PG2); native intent registration racing the legacy endpoint with the same
canonical payload converges (PG3); the same race with a mismatched payload correctly conflicts
(PG4); native commit racing the legacy endpoint with the same payload converges (PG5); that race
with a mismatch correctly conflicts (PG6). Underlying mechanisms: a canonical `(provider_id,
idempotency_key)` reservation, a database `UNIQUE` constraint as the final authority, session-aware
transactional replay (a just-committed order is read through the locking transaction, never an
independent session that cannot yet see it), and row locking (`SELECT ... FOR UPDATE`) on commit.
One negative-control finding worth recording precisely: removing the commit method's own
"already-committed" fast-path check did not, by itself, allow a duplicate order — the database
`UNIQUE` constraint on `(provider_organization_id, idempotency_key)` independently converges that
race regardless. That is genuine defense-in-depth; the fast path is a contention/latency
optimization layered on top of a schema-level guarantee, not the sole mechanism preventing
duplication.

**Historical compatibility.** Pre-DH-CLINIC-003C `LabOrder` rows legitimately have `intent_id =
NULL` — no backfill was performed or attempted, and none is planned; that is the permanent, correct
state for orders that predate this model. Every *new* provider order path (native or legacy)
requires intent ownership going forward.

Full decision record and rejected alternatives: **ADR-049** (`DECISIONS.md`). Implementation and
qualification detail: `PROJECT_STATE.md`'s DH-CLINIC-003C entries.

~~**Runtime-deployability gap [DH-STATE-001, 2026-09-17]: everything above is proven at the
component/integration-test level only, not runtime-deployed.** Three architectural facts, read
together, mean the Clinic frontend cannot authenticate itself in a real deployment as the code
stands: `ui/src/App.tsx` mounts `ClinicApp` with no auth token of any kind; `ClinicProvider`
defaults that token to `null` and calls `GET /me` with it anyway; and `ExchangeClient` defaults its
`baseUrl` to the relative, same-origin path `/exchange/v1`. That same-origin request only reaches
the Exchange if the origin serving the Clinic app also routes `/exchange/v1/*` to the adapter —
`caddy/Caddyfile`'s `app.{$BASE_DOMAIN}` vhost (which serves the Clinic bundle) does not; only the
separate `exchange.{$BASE_DOMAIN}` vhost does, and that vhost serves no UI. Compounding this, the
`dhealth-exchange` Keycloak realm defines exactly one client, `dhealth-exchange-api`, confidential
(`publicClient: false`, client-secret-bearing) — no browser-safe public/PKCE client exists for a
frontend to authenticate against even if the routing gap were closed.~~ **CLOSED AT THE CONFIG
LEVEL [DH-CLINIC-004A, ADR-050, 2026-09-17]:** see "Native browser authentication" below — a public
PKCE client now exists, `app.{$BASE_DOMAIN}` now routes `/exchange/v1/*`, and Clinic now mounts
behind a real auth gate. **Still not runtime/browser-qualified** — no live Keycloak/Caddy stack has
been booted to observe an actual OIDC round trip; that remains DH-CLINIC-004B's job.

### Native browser authentication (ADR-050, DH-CLINIC-004A, 2026-09-17)

Native DHealth browser apps (Clinic today; Lab later) authenticate against the same
`dhealth-exchange` realm DH-EX-008 already uses for the backend, through a separate public client,
`dhealth-native-ui` (Authorization Code + PKCE S256, no secret, `keycloak-js`) — never the
confidential `dhealth-exchange-api` client and never the reference-connector's `daher` realm.

**Shared auth layer, not Clinic-owned** (`ui/src/features/auth/exchange/`):

- `ExchangeAuthProvider`/`useExchangeAuth()` owns the `keycloak-js` session. `init()` uses
  `onLoad: 'check-sso'`, not `'login-required'` — initialization itself never redirects the page;
  the provider observes the result and calls `login()` as its own explicit next step only when no
  session exists. This keeps `initializing`/`authenticating`/`authenticated`/`error` real, distinct,
  observable states rather than a redirect happening as an initialization side effect.
- The resulting access token never leaves memory: no `localStorage`, `IndexedDB`, or app-created
  cookie holds it. Unmounting the provider (switching to the reference-connector app) simply drops
  that in-memory state — which is why switching apps is explicitly not the same operation as logging
  out (see DECISIONS.md ADR-050, decision 4).
- Token acquisition (`exchangeTokenProvider.ts`) is the `getAuthToken` callback `ExchangeClient`
  already supported: single-flight (concurrent requests share one `updateToken` attempt, never each
  starting their own), and fails closed — a refresh failure returns `null` and flips the provider's
  status to `error`, never sends a stale token.
- `ExchangeAuthGate` is a small, generic presentational component: it renders its children only once
  `status === 'authenticated'`, so the data layer beneath it (`ClinicProvider`'s `/me` call) cannot
  fire before real authentication exists. `App.tsx` wraps native Clinic in
  `ExchangeAuthProvider` + `ExchangeAuthGate` and passes the resulting authenticated `ExchangeClient`
  into `ClinicApp`; Clinic itself contains no OAuth/OIDC code. **2026-09-18 update (DH-LAB-001):**
  DHealth Lab (`ui/src/features/lab/`) now exists and reuses the identical provider/gate pair — a
  second, independent `ExchangeAuthProvider` instance wraps `LabApp` exactly as Clinic's own wraps
  `ClinicApp`, per ADR-050 decision 6. No second OAuth client was introduced.

**Configuration, fail-closed** (`exchangeAuthConfig.ts`): the Keycloak origin is derived from the
browser's own hostname (`app.<domain>` → `https://id.<domain>`) only when the hostname genuinely
matches that shape; anything else (bare `localhost` under `npm run dev`, an unrecognized host) is
refused outright rather than guessed, unless an explicit `VITE_EXCHANGE_KEYCLOAK_URL` override is
set — all public configuration, never a secret, documented in `.env.example`.

**Same-origin routing, no CORS:** `app.{$BASE_DOMAIN}`'s Caddy vhost now also proxies
`/exchange/v1/*` to the adapter, path-scoped exactly like the pre-existing `exchange.{$BASE_DOMAIN}`
vhost (which is unchanged and still exists). `ExchangeClient`'s default relative base path
(`/exchange/v1`) therefore resolves from the Clinic app's own origin without any cross-origin
request or CORS configuration.

**Logout separation:** native Clinic's own logout calls `useExchangeAuth().logout()`, which invokes
`keycloak-js`'s `logout()` against the `dhealth-exchange` realm and redirects back to the app root —
never the reference-connector's `logoutLocalSessions()`/`navigateToKeycloakEndSession()` (OpenMRS/
OpenELIS/`daher` realm), which continues to run only from the Maternity connector's own logout path.

Full decision record: **ADR-050** (`DECISIONS.md`). Implementation and qualification detail:
`PROJECT_STATE.md`'s DH-CLINIC-004A entry.

## 18. Native demo environment topology (DH-DEMO-001) — added 2026-09-18

A dedicated Compose project, `docker-compose.native-demo.yml`, exists so the native Clinic →
Exchange → Lab loop can be booted and demonstrated **without** the historical OpenMRS/OpenELIS
reference-connector stack (§2) becoming healthy — that stack is preserved untouched, never
deleted or deprecated, and remains the repo's real production topology for the maternity/lab
track. The demo is a second, independent project (`name: dhealth-native-demo` — a distinct
Compose project name, so `docker compose down` in one never touches the other's containers or
volumes), not a fork of `docker-compose.yml` and not a parallel mock stack:

```
                    dhealth-native-demo (independent Compose project)

                          Internet (localhost only)
                             │
                       ┌─────▼─────┐
                       │   caddy   │  narrower Caddyfile: id./exchange./app. only
                       └─────┬─────┘
        ┌──────────────┬─────┴────────┐
        │              │              │
  id.<domain>    exchange.<domain>  app.<domain>
        │              │              │
   ┌────▼────┐    ┌────▼────┐   ┌─────▼──────┐
   │keycloak │    │ adapter │   │ daher-ui   │   <- the REAL images/code, via extends:
   └────┬────┘    └────┬────┘   └────────────┘
        │              │
        └──────┬───────┘
          ┌─────▼─────┐
          │openelis-db│   <- demo's OWN lightweight Postgres (name reused
          └───────────┘      deliberately so extends:-copied depends_on/
                              hostnames resolve with zero overrides)

   NOT present at all: openmrs-*, openelis-frontend/webapp/fhir, mariadb
   -> nothing in this project's dependency graph can ever wait on them.
```

**Mechanism: Compose `extends:`, not duplication.** `keycloak`/`adapter`/`daher-ui`/`caddy` are
`extends:`ed verbatim from `docker-compose.yml` — the demo runs the identical images, entrypoints,
and application code a real deployment runs, so a regression in the real service definitions is
also a regression here. Two `extends:` semantics were confirmed empirically (not assumed) while
building this, and are load-bearing for anyone editing either compose file:
- `extends:` forces Compose's interpolation pass to validate every `${VAR:?}` across the **entire**
  base file, not only the extended service's own — `demo/native/demo.env` therefore carries a full
  dummy-value block for `OMRS_*`/`OE_*` variables this project never actually uses, mirroring this
  repo's own CI `ci-dummy` convention.
- `extends:` copies a service's own `volumes:` list but **not** the base file's top-level named-
  volume declaration — `docker-compose.native-demo.yml` therefore redeclares `adapter-audit:` in
  its own top-level `volumes:` section.

**Reset safety (P0):** the demo's own `adapter` service carries `ADAPTER_DB_NAME=
dhealth_native_demo` (never the real deployment's default) and `ADAPTER_DEMO_ENVIRONMENT=1` (a key
`docker-compose.yml` does not have at all), and `demo/native/db-init/init-tenant-databases.sh`
creates a marker table no Alembic migration ever creates. `demo_seed.py`'s reset path refuses to
run unless all three hold — see `PROJECT_STATE.md`'s DH-DEMO-001 entry for the full mechanism and
its real-PostgreSQL qualification.

**Evidence boundary, stated plainly. [Corrected 2026-09-20, DH-PATIENT-001 — the paragraph this
replaces said this topology "has **not** been observed actually booting anywhere" and called the
`native-demo-boot` CI job "this topology's first real-boot attempt". That attempt has since
happened and succeeded, so the old text is now false and is corrected rather than left standing.]**

- **Real native Compose boot: qualified in CI.** The `native demo — real boot (DH-DEMO-001)` job
  succeeded in run `35390037354` at `a5992bd37c40f1296955f15692a84560f7c7b3f0` (all 10 jobs green;
  verified directly against the GitHub API during DH-PATIENT-001, not carried over from a report).
  This topology does boot and reach healthy on a clean runner.
- **Real Keycloak interactive login: not yet qualified.** The stack boots with real Keycloak, but
  no human has completed an interactive browser login against it.
- **Real browser golden journey: not yet qualified.** No manual or Playwright walkthrough of the
  Clinic → Exchange → Lab → Patient journey has been performed in a browser.

This repo's own sandbox still cannot pull real container image blobs from the CDN (§2's own build
environment did not have this restriction; this is a later, different sandbox limitation), so the
boot evidence above is CI's, not a local observation. Full decision record and evidence:
`PROJECT_STATE.md`'s DH-DEMO-001 entry.

## 19. DHealth Patient surface (DH-PATIENT-001) — added 2026-09-20

The third native application, and the only one with no authenticated user. It exists because the
MVP transaction does not end when a clinician reviews a result — it ends when the patient can find
out what is happening and whether they need to follow up (`docs/MVP-1.0.md` §4's last step).

```
   Clinic (OIDC, Keycloak)                         Patient (no account, no OIDC)
   ───────────────────────                         ────────────────────────────
   PatientAccessHandoff                            PatientApp
     │  issue / revoke / rotate                      │  reads #access=<token> once
     │  (ExchangeClient, clinician JWT)              │  scrubs it from the address bar
     ▼                                               │  holds it in component memory only
   POST/DELETE/GET                                   ▼
   .../orders/{id}/patient-access                  GET /exchange/v1/patient/order
     │                                               │  (PatientAccessClient, opaque bearer)
     │  raw token returned ONCE ─────────────────────┘
     ▼         (QR + printable sheet, never as visible text)
   PatientAccessGrant  (SHA-256 digest persisted; raw value never stored)
```

**Two credentials, two clients, deliberately not one.** A clinician's Keycloak JWT and a patient's
order-scoped opaque secret are different kinds of authority, so they do not share a code path:
`ExchangeClient` (OIDC) and `PatientAccessClient` (one method, patient endpoints only) are separate
classes. The patient client exposes no marketplace method at all — DH-EX-005's patient lab-selection
endpoints remain served by the backend but are unreachable from this surface by construction
(`docs/MVP-1.0.md` §6, Law 1: the launch product is a connected workflow, not price-shopping).

**Routing.** `App.tsx` returns `<PatientApp />` for `?app=patient` **before** constructing any
`ExchangeAuthProvider`/`ExchangeAuthGate`. A patient is therefore never redirected to Keycloak —
structurally, not by configuration. Clinic and Lab authentication are unchanged.

**Why the credential lives in the URL fragment.** A fragment is not transmitted to the HTTP server
as part of the request, so it cannot reach reverse-proxy access logs, server routing, or a referrer
path. Caddy's `Referrer-Policy: same-origin` helps but is not the control that matters; fragment +
immediate `history.replaceState` scrub is. The credential is never written to `localStorage`,
`sessionStorage`, IndexedDB, a cookie, or a query parameter. The accepted cost: after the scrub, a
refresh has no credential, and the patient is told to scan again.

**What the patient can see, and cannot.** Order status, the assigned laboratory's public contact
detail, the requested tests by human display name, and one server-owned follow-up projection
(`result_status`). **Never** a result value, unit, reference range, observation, release manifest,
diagnosis, interpretation, price, internal identifier, or the reviewing clinician's identity — the
Exchange does not serialize them to this surface at all, asserted against the response's raw JSON.

**Amendment awareness is load-bearing.** `result_status` resolves from the order's *latest released
version* and that version's own `DoctorReview`, never from "the most recent terminal review". When a
version-2 amendment is released, a fresh `PENDING` review is created for it (§17's ADR-048 model),
so the patient's view reverts to awaiting-review — presenting version 1's disposition would be
presenting a clinical judgement about content that has since been superseded.

**No raw-token recovery, ever.** There is no endpoint that returns an existing grant's secret. A
clinician who loses the link must revoke and re-issue, and the UI says exactly that rather than
implying the QR can be rebuilt. Consequently every ambiguous mutation re-reads authoritative
metadata instead of retrying: a blind second issuance would either hit the one-live-grant-per-order
database constraint or silently invalidate a QR already in a patient's hands.

**`ResultDelivery` is untouched.** A patient opening a status page does **not** mean
`ResultDelivery = DELIVERED`; that semantic remains an open product decision (`docs/MVP-1.0.md`
§10), and this surface deliberately does not decide it. No SMS/WhatsApp/email/voice transport was
added — a later transport can carry this same access path.

**Read-access audit gap, documented rather than papered over.** Issuance and revocation are audited
(`patient_access_issued`/`patient_access_revoked`, real `ExchangeIdentity` actors). A patient *read*
is not, because `ExchangeSecurityAuditEvent.actor_identity_id` is a real FK to
`exchange_identities` and a patient has no identity there — recording one would mean fabricating an
actor, which this repository refuses to do (see `exchange/patient_access.py`'s own docstring). If
patient-read auditing is wanted, it needs an actor model designed for it, not a false row.

---

## 20. One staff application (DH-MVP-POLISH-001) — added 2026-09-20

§19 described three native applications. There are now three *surfaces* but only two authenticated
ones, because Clinic and Lab stopped being separate applications. ADR-054 records the decision; this
section records the shape.

```
                       ┌─ /patient#access=…  ──────────────  PatientApp        (no OIDC, §19)
                       │
   App.tsx ── surface ─┼─ /  ·  ?app=staff  ─────────────┐
   (?app, normalized)  │      ?app=clinic  (alias)       ├─  ExchangeAuthProvider
                       │      ?app=lab     (alias)       │        └─ ExchangeAuthGate
                       │                                 │              └─ StaffApp
                       └─ ?app=maternity ───────  AppShell (OpenMRS connector, ADR-042)
                                                    └─ "Ouvrir l'application DHealth" ─┘
                                                       (one-directional, by design)

   StaffApp
     └─ WorkspaceProvider ──────────── GET /exchange/v1/me   ← fetched ONCE, for everything
          │   organizations · activeOrganization · capabilities · generation
          ▼
        StaffWorkspace
          │  masthead (identity, roles, active-establishment select)
          │  nav: Aujourd'hui · Patients · Ordres · Résultats
          │  pending-mutation maps  ← held ABOVE the keyed subtree, so they survive a switch
          ▼
        <div key={activeOrganization.organization_id}>   ← an org switch DISCARDS this subtree
          └─ ClinicProvider / LabProvider   ← projections of the workspace, they fetch nothing
               └─ the existing Clinic and Lab pages, unchanged
```

**Why the providers stayed.** `ClinicProvider` and `LabProvider` each used to call `/me` for
themselves, so the same question — what may this person do? — had two answers that could disagree,
and a user in both a clinic and a laboratory paid for the session twice. They are now adapters over
the one workspace context: no `client` prop, no fetch, and they map the workspace's states onto the
vocabularies their pages already speak (`no_organizations` → `no_clinics` / `no_labs`). Keeping them
preserved the existing Clinic and Lab page tests as *tests of behaviour* rather than forcing a
rewrite that would have re-asserted whatever the new code happened to do.

**Capabilities are a projection of the active membership, not a permission system.** Five booleans —
`canViewPatients`, `canCreateOrders`, `canReviewResults`, `canWorkLabOrders`, `canReleaseResults` —
derived from the active organization's type, status and roles. Each is documented against the backend
gate that actually enforces the same thing (`_require_clinician`, `_require_lab_tech`,
`_require_lab_tech_or_releaser`, and the `LAB_RESULT_RELEASER` check in `authorization.py`). Hiding
a control the server would refuse is a courtesy to the user, never a security boundary; the server
refuses regardless of what the browser rendered. ADR-044 stands: entry authority and release
authority are separate, so `LAB_TECH` alone never sees a release control.

**Why an organization switch remounts instead of refetching.** Every org-scoped surface — queues,
patient lists, order detail, in-flight mutations — would otherwise need its own invalidation, and one
forgotten `if (organizationId !== current) return` is a late response from organization A rendering
under organization B's heading. The active organization's id is therefore the `key` of the content
subtree: React discards the whole thing, and no stale response has a component to land in. The cost
is that unsaved in-subtree state dies on a switch, which is the safe direction for a wrong-facility
hazard. Pending-mutation maps live above the keyed boundary because they are about the person's
intent, not the organization's data.

**Nav renders only what exists.** There is no `Réglages` item: facility setup and administration are
DH-MVP-ADMIN-001's subject, and an item that leads to "Bientôt" teaches users that this product's
navigation cannot be trusted. It appears when the screen behind it does.

**The reference connector keeps its own shell, and shares the token layer.** `?app=maternity` renders
the OpenMRS `AppShell` exactly as before, with one header button into the staff application. There is
no control back: a staff user who needs the connector uses the URL, because putting the
reference-connector track inside the staff product's navigation would undo the separation ADR-042/043
exist to maintain. Leaving the connector is not a logout — unmounting the Exchange auth provider
drops in-memory token state and never calls Keycloak's end-session endpoint. Both shells resolve the
same `--color-*` tokens, so retargeting the product's primary colour retargets the connector's too;
that is accepted (one product, one palette) and noted so a future divergence is a deliberate act
rather than a surprise.

**Session lifetime and typed values.** A successful silent token refresh does not remount anything,
so a technician's typed-but-unsaved result values survive a token expiring while the panel is open. A
*failed* refresh sets auth status `error`, at which point `ExchangeAuthGate` renders its own notice
instead of the tree below it and the entry form unmounts with the drafts in it — before any redirect.
That path cannot be bridged without persisting clinical values outside the component, which
`docs/MVP-1.0.md` and this ticket both forbid, so the per-field marker names both losing events
instead of implying the work is safe.
