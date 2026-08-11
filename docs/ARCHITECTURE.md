# Daher Health — v1 Pilot Architecture

**Status:** REVIEWED — decisions recorded 2026-08-10. Cleared to begin build step 1.
**Scope:** Single-facility pilot — one birth delivery centre + its laboratory, Djibouti.
**Last updated:** 2026-08-10

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

Eleven containers, one `docker-compose.yml`, one `.env`: Caddy, OpenMRS backend, OpenMRS frontend,
MariaDB, OpenELIS webapp, OpenELIS FHIR, OpenELIS frontend, PostgreSQL, Keycloak, the adapter, and
the backup sidecar. (The diagram below collapses the OpenELIS frontend into its webapp box for
legibility.)

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
    ┌───▼──────────────▼───┐                    ┌──────────▼─────────┐
    │   backup (sidecar)   │──► S3-compatible   │ WhatsApp notifier  │──► Meta / BSP
    │   nightly pg + mysql │    object storage  │ (see §8)           │
    └──────────────────────┘                    └────────────────────┘
```

**Note the shape of OpenMRS 3:** it is *not* one container. The O3 Reference Application is a
Tomcat backend (REST + FHIR R4 API, modules, database) plus a separate nginx container serving the
single-page frontend. Both are needed; they are versioned together.

**Note the shape of OpenELIS:** likewise multi-container — the Tomcat webapp, a HAPI FHIR server
that is the actual FHIR endpoint the adapter talks to, and a React frontend. Its upstream compose
also ships a `certs` and `proxy` container; we drop both because Caddy terminates TLS for us.

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
| MariaDB 11.4 LTS | `mariadb` | `openmrs` | The only supported path for OpenMRS + modules |
| PostgreSQL 14.4 (OpenELIS image) | `postgres` | `clinlims` (OpenELIS), `keycloak`, `adapter` | OpenELIS's own image, extended with an init script that also creates the Keycloak and adapter databases and roles |

That is **two engines, two containers** — one more container than the brief, but the spirit of the
brief (small footprint, no per-service DB sprawl) is preserved: Keycloak and the adapter are
co-tenants of the Postgres instance OpenELIS already requires, rather than getting their own.

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
| OpenMRS O3 frontend behaviour (default locale, ward app config) | frontend `config.json` mounted into the frontend container | `openmrs-config/frontend/` |
| Wards, bed types, beds | `bedmanagement` module. **Open question:** Initializer's coverage of bed domains needs verification. If beds are not an Initializer domain, this becomes a first-boot REST seed script, still code, still committed, still idempotent — just a different mechanism. I'll confirm at build time and record the answer here. | `openmrs-config/` or `openmrs-config/seed/` |
| Keycloak realm, clients, roles, French login theme | `--import-realm` with a committed realm JSON. I'll base ours on the OpenMRS HIS distribution's `ozone-realm.json`, which already has working `openmrs` and OpenELIS client definitions, rather than deriving them from scratch. Client secrets come from `.env`, never from the JSON. | `keycloak/realm-config/` |
| OpenELIS lab tests, LOINC mappings, "accept external orders" | OpenELIS stores these in its DB; upstream documents them as admin-UI steps. I'll capture them as a committed SQL/REST seed applied on first boot so they are reproducible. | `openelis-config/` |
| Adapter behaviour | environment variables only | `.env` |

Nothing in this list is "click through the admin UI after deploy". If I hit something that genuinely
cannot be automated, it goes in `README.md` as an explicit numbered post-deploy step and I tell you.

---

## 5. Identity and SSO

Keycloak is the single identity provider. Realm `daher`, clients `openmrs` and `openelis`, both
OIDC confidential clients with secrets injected from `.env`.

- **OpenMRS** authenticates via its OAuth2/OIDC support (`oauth2.properties`, as the HIS distro
  does). OpenMRS roles are mapped from Keycloak realm roles.
- **OpenELIS** authenticates via its OIDC integration against the same realm.
- **The adapter is not a user.** It gets its own Keycloak service account (client credentials
  grant), so its writes into OpenMRS are attributable to a named machine identity in the audit
  trail rather than borrowing a clinician's account.
- **Keycloak login theme is French by default**, English selectable.

Break-glass: a local OpenMRS admin account remains valid so the system is not unusable if Keycloak
fails to start. This is a deliberate tradeoff (one credential outside SSO) and is documented in the
runbook with instructions to rotate it after any use.

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

**Timezone:** every container and both databases run `TZ=Africa/Djibouti` (UTC+3). Clinical
timestamps that silently differ by three hours between the EMR and the LIS are a patient-safety
problem, not a cosmetic one.

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

Nightly containerised cron sidecar:

1. `mariadb-dump` of the OpenMRS database (`--single-transaction`)
2. `pg_dump` of `clinlims`, `keycloak`, and `adapter`
3. The adapter audit-log volume and the OpenMRS data volume (uploaded complex obs, module data)
4. A Keycloak realm export
5. Compress, encrypt, upload to S3-compatible storage, prune beyond `${BACKUP_RETENTION_DAYS}`
6. Write a success/failure marker the runbook tells you how to check

**Both engines. Every time.** A restore drill that only restores PostgreSQL would come back with a
working lab system and no patient records — that failure mode is written into
`backup/restore-drill.md` as an explicit checklist item, not left to be discovered.

Credentials from `.env`. The drill — restore into a scratch stack, verify a known test patient, a
known lab result, and a working SSO login — is performed and its output recorded before this pilot
is called ready. Per your instruction, this is not deferred and not optional.

---

## 11. Clinical content — sign-off boundary

I am not a clinician and neither is this system. Every piece of clinical content in this build —
the prenatal panel test list, ANC form fields, the Labour Care Guide fields, ward/bed labels — is
drawn from **WHO / OpenMRS / OpenELIS reference defaults, unmodified**, and is marked in the
repository as:

> ⚠️ **NEEDS SIGN-OFF FROM CLINICAL LEAD** — reference default, not validated for this facility.

Nothing clinical is invented, extrapolated, or "sensibly adjusted". A `docs/CLINICAL-SIGNOFF.md`
checklist tracks each item and who signed it, and it is unsigned until a clinician signs it.

---

## 12. Pinned versions (draft)

Exact tags are confirmed as each service is built; nothing uses `:latest`. Where an upstream
publishes only a moving branch tag, I pin the **image digest** and record it here with its date.

| Component | Tag | Why |
|---|---|---|
| OpenMRS O3 backend | `openmrs/openmrs-reference-application-3-backend:3.7.x` + digest | Current released O3 line. Upstream publishes only branch tags here, hence the digest pin. |
| OpenMRS O3 frontend | `openmrs/openmrs-reference-application-3-frontend:3.7.x` + digest | Must match the backend line |
| MariaDB | `mariadb:11.4-ubi9` | Current LTS; OpenMRS-supported |
| OpenELIS | `itechuw/openelis-global-2:3.2.1.11` (+ matching `-database`, `-fhir`, `-frontend`) | Latest stable patch on the 3.2.1 line; the OpenMRS HIS distro integrates the 3.2.x line, so FHIR interop is exercised upstream |
| PostgreSQL | `postgres:14.4` (via OpenELIS's DB image) | Pinned by OpenELIS's own image; we do not get to choose it |
| Keycloak | `keycloak/keycloak:26.7.1` | Current stable |
| Caddy | `caddy:2-alpine` → resolved to exact patch | Automatic HTTPS |
| Adapter | built here, `python:3.13-slim` base | — |

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
OpenHIM/OpenCR multi-facility sync · self-service provisioning portal.

Where a v1 choice would need to change for multi-facility, it is noted inline in this document
(§3) — noted, not built.

---

## 15. Open items

**Nothing blocks implementation.** All five questions raised in the first draft were answered on
2026-08-10 and are recorded in §0. What remains open is tracked here and none of it gates the build:

| Item | Status | Needed by |
|---|---|---|
| Real maternity bed counts | Placeholder values in `.env`; changing them is an `.env` edit + restart (§7) | Go-live |
| WhatsApp Cloud API / BSP credentials | Building against mock; swap is `.env`-only. Line item stays marked **incomplete** until real credentials run end-to-end (§8) | Go-live |
| `labonfhir` vs. hand-rolled transport | Deferred by decision; revisit after step 5 of §13 with real payloads in hand (§6) | Before v1.1 |
| Initializer coverage of bed domains | To be confirmed at build step 2; falls back to an idempotent REST seed if unsupported (§4) | Build step 2 |
| Clinical sign-off on all clinical content | `docs/CLINICAL-SIGNOFF.md`, unsigned (§11) | Go-live — **hard gate** |

**Timeline note:** the pilot targets **1 September 2026**. Scope decisions above were taken partly
to keep the surface area small enough to be stable by then. Anything that grows the container count
between now and then should be weighed against that date.
