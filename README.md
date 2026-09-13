# Daher Health

**DHealth is an interoperable diagnostic network** for providers, laboratories, and patients.
Facilities may run DHealth's own native software or integrate their existing EMR/LIS through a
connector — never both maintained by hand for the same transaction. Full product definition,
the five product surfaces, the golden journey, and four non-negotiable product laws (no forced
marketplace, no double entry, faster than what it replaces, no digital-literacy requirement for
care): **[`docs/MVP-1.0.md`](docs/MVP-1.0.md)**, decided in `DECISIONS.md`'s **ADR-043**. This
section gives the short version and points to current evidence; it does not duplicate that
document.

### Current: the DHealth Exchange backend

**DHealth is a digital lab order + result exchange** connecting doctors/clinics, patients, and
independent laboratories — reachable through DHealth's own Provider/Lab portals or through an
existing EMR/LIS via a connector (DECISIONS.md's ADR-042, 2026-09-08). This is a **pivot in
product direction**, not a rewrite: the maternity/birth-centre clinical stack described below —
OpenMRS + OpenELIS + Keycloak + the adapter — keeps running exactly as built and becomes the
**first reference connector track** rather than the product's definition. Nothing below is deleted
or inaccurate; read it as the connector-track foundation the new Exchange builds on. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §17 for the Exchange architecture and
[`PROJECT_STATE.md`](PROJECT_STATE.md)'s DH-EX-001 through DH-EX-012A entries for what exists so far
(this file gives the short version below; `docs/MVP-1.0.md` §8 is the authoritative current-vs-MVP
status table).

Current backend workflow shape, at a glance:

```text
order
  → selected/assigned lab (patient-selected or provider/facility-assigned)
  → accepted
  → explicit collection milestone
  → processing
  → results entered
  → explicit authorized release
  → COMPLETED
  → provider review
```

One thing this shape does **not** yet resolve, disclosed rather than glossed over: correcting or
amending a result — before or after release — has no supported path today (`DECISIONS.md`'s ADR-045,
open) — covered in the DH-EX-012/DH-EX-012A paragraphs below. The result data model itself
(`LabOrderItem → LabResult` container `→ LabObservation[]`, 0..N observations per ordered item) was
confirmed by real field evidence not to be a single scalar per item, and is now implemented
(ADR-046/ADR-047, DH-EX-012C) — see the paragraph below DH-EX-012A.

The Exchange **core domain/persistence layer** (DH-EX-001), the **organization onboarding, OIDC
authentication, and multi-tenant authorization boundary** (DH-EX-002), the **lab directory,
synthetic test catalog, lab offerings, DJF pricing, and whole-order lab eligibility backend**
(DH-EX-003), **DHealth-native Provider lab-order creation** (DH-EX-004), **secure patient order
access + patient lab selection** (DH-EX-005), and the **selected lab's own assigned work queue and
accept/reject/start-processing workflow** (DH-EX-006) are all committed, pushed, and CI-qualified —
DHealth's backend can now carry an authenticated clinician's provider-scoped lab order (exact
patient reference, exact synthetic DHealth tests, canonical idempotency key) from
`AWAITING_LAB_SELECTION` through an opaque, order-scoped patient bearer grant, a patient's
exact-price-consent lab choice, an immutable frozen price snapshot, to `SELECTED`, and into the
selected lab's own work queue, where a `LAB_TECH` may accept, reject, or start processing an
accepted order (`SELECTED`→`ACCEPTED`/`REJECTED`, `ACCEPTED`→`IN_PROGRESS`) — with secure
multi-organization identity, tenant isolation, patient/OIDC auth kept structurally separate, and a
transactionally-authoritative security-mutation audit throughout.

**DH-EX-007 (lab result submission and completion) is also committed and pushed to
`origin/master`** (merged via PR #1; migration head `0010_exchange_results_workflow`). Per commit
`30c5212`'s own message: 605 passed / 42 skipped locally, real-PostgreSQL result-concurrency
qualification passed. **Superseded in one specific respect by DH-EX-012, noted below rather than
silently left stale:** DH-EX-007 originally moved the order straight to `COMPLETED` the instant
every item had a result, with no separate authorization step — that automatic-completion behavior
no longer exists (see DH-EX-012 immediately below). The rest of DH-EX-007 stands: the selected
lab's `LAB_TECH` still records one immutable result per ordered item once the order is
`IN_PROGRESS`, and the ordering provider still retrieves completed results and records a terminal
review disposition. The patient-facing path still never returns a result value.

**Correction (DH-EX-007D2, 2026-09-12): exact-SHA CI has since been independently observed.**
Authenticated GitHub API access (via the repository's own stored git credential, not `gh`) confirmed
run `34649375844` for merge commit `d3a72d5c` — `event=push`, `branch=master`,
`status=completed`, `conclusion=success`, 9/9 jobs `success` — and run `34656008030` for the
DH-EX-007D documentation-closure commit `a17ebad` with the identical result. **DH-EX-007 is
exact-SHA CI-qualified.** This proves the test suite and static checks passed in CI; it proves
nothing about real Keycloak, a live/browser demonstration, production deployment, or clinical
readiness — those remain separately unproven, below.

**DH-EX-009 through DH-EX-012A extend the backend well past DH-EX-007**, each committed, pushed,
CI-qualified, and (where a migration was involved) real-PostgreSQL-qualified — full evidence in
`docs/ARCHITECTURE.md` §17 and each ticket's own `PROJECT_STATE.md` entry, summarized here:

- **DH-EX-009** adds a provider-scoped patient-reference *binding* API (exact source-identity
  get-or-create) — narrower than full patient lookup/registration, but the first route that lets a
  provider create a patient reference at all, rather than requiring one to already exist.
- **DH-EX-010/DH-EX-010A/DH-EX-010A1** add a **provider/facility-assigned lab routing path**
  alongside DH-EX-005's original patient-selection path — a clinician can assign a lab directly, with
  no forced price-marketplace step, closing ADR-043's launch-option (a) gap — plus the access-grant
  and downgrade-safety fixes that path required.
- **DH-EX-011** adds an explicit, order-level **sample/specimen collection milestone**
  (`LabSampleCollection`, `POST .../record-collection`) — a single fact-of-recording step required
  before processing may start, not full specimen management (no container/volume/site/adequacy
  modeling exists).
- **DH-EX-012 (ADR-044)** replaces DH-EX-007's automatic completion with an **explicit, authorized
  release step**: recording a result — even the last one — now only ever reports readiness
  (`ready_for_release`); a separate `LAB_RESULT_RELEASER` role (additive to `LAB_TECH`, the same
  human may hold both) must explicitly call `POST .../release-results` before an order reaches
  `COMPLETED` and a provider can read anything.
- **DH-EX-012A (ADR-045, OPEN)** audited the resulting gap — today's result model is insert-only
  with no correction or amendment path at all, before or after release — and recorded a decision
  packet distinguishing the two problems. **Neither is implemented; ADR-045 remains open.**
- **DH-EX-012C (ADR-047, IMPLEMENTED, 2026-09-13)** replaces the one-scalar-per-item result model
  with `LabOrderItem → LabResult` (a per-item container) `→ LabObservation[]` (zero, one, or many
  observations per item), the shape real field evidence (DH-FIELD-001, ADR-046) required. A new
  `POST .../items/{item_id}/observations` records any number of observations; a new
  `POST .../items/{item_id}/complete-result-entry` is the one explicit signal an item's entry is
  finished — **never inferred from how many observations exist**, and an item may never complete
  with zero observations (Liban's accepted resolution of ADR-047's one open question). The existing
  `POST .../items/{item_id}/result` remains as an atomic single-observation convenience call for
  the simple case. Provider/lab-inspection reads now return nested observations per item. Real-
  PostgreSQL qualified, including migration upgrade/downgrade-guard evidence and four concurrency
  scenarios. ADR-045's correction/amendment work remains paused independently — see
  `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` for the generalized field findings behind this change (no
  real patient data) and `PROJECT_STATE.md`'s DH-EX-012C entry for full evidence.

Real Keycloak integration remains a deployment/pilot qualification gate, not yet run. The Provider
Portal, Lab Portal, and WhatsApp/SMS integration are **still under construction, not yet built** —
this is the backend foundation those are built on, not a patient-facing marketplace and not the MVP
itself. OpenMRS/OpenELIS remain supported, running exactly as built, as the first reference
connector track.

### Intended: DHealth Clinic / DHealth Lab / DHealth Patient / DHealth Connect

`docs/MVP-1.0.md` names four more product surfaces beyond the Exchange backend above: **DHealth
Clinic** (native provider software), **DHealth Lab** (native laboratory software), **DHealth
Patient** (the assisted-digital patient layer), and **DHealth Connect** (the EMR/LIS integration
layer). **None of the four is built as a named application today.** The Exchange backend above
already implements much of what a future DHealth Clinic/Lab would call over HTTP; DHealth Patient
and DHealth Connect have no code of their own yet. Full current-vs-MVP gap table:
`docs/MVP-1.0.md` §8.

---

### Reference connector track: OpenMRS + OpenELIS (maternity/birth-centre stack)

The remainder of this README describes the pre-pivot maternity/birth-centre system in detail. It
is real, running, historical-and-current infrastructure — the first reference connector track
under DHealth Connect's model (ADR-042) — not the definition of DHealth's overall product.

A health information system for a birth delivery centre and its laboratory in Djibouti.
OpenMRS (patient records, maternity care) + OpenELIS (laboratory) + Keycloak (one login for both)
+ a small adapter that routes lab orders and results between them, all in Docker containers behind
one `docker-compose.yml`.

> ### ⚠️ Status: infrastructure foundation runs. Not for use with real patients.
>
> **Step 1 (MariaDB + OpenMRS O3)** is API-verified and Liban registered a test patient in the SPA.
> **Step 3 (PostgreSQL + OpenELIS)** is running with its schema and mutual-TLS FHIR endpoint
> verified; configured admin authentication is form-verified, but its browser workflow has not
> been exercised. Analyzer interfaces are **out of scope for v1** — results are entered manually
> (ADR-016). **DH-008 SSO protocol plumbing** runs fail-closed: Keycloak has verified OpenMRS OIDC
> and OpenELIS SAML clients, but staff-role mapping is a human authorization decision and SSO
> remains disabled. **DH-010's adapter safety skeleton** is healthy, **DH-025's crash-recovery
> core** is runtime-verified including before/after-write crash tests, and **DH-011E's OpenMRS order
> poller and durable watermark** are built and verified (94 tests) — but **nothing is activated and
> no order has moved.** How an order reaches OpenELIS is an open decision (BLOCKER-6): probing found
> the laboratory *pulls* from a remote FHIR source rather than accepting a push, and it has never
> received an order. **DH-016's reverse proxy** serves all three subdomains over HTTPS locally;
> publicly trusted certificates still need the real domain and DNS. **DH-021** replaced the
> upstream keystore, whose private key was public *and whose certificate had already expired*.
>
> **A restore has actually been performed** (DH-015) — archive decrypted, restored into an isolated
> stack, and a patient read back through the API. It is *partial coverage*, not done: see
> [`backup/restore-drill.md`](backup/restore-drill.md). Off-site backup is still unexercised.
>
> Still missing: clinical forms, the prenatal order set, order/result transport, notifications,
> SSO activation, and the end-to-end demonstration.
>
> A **multi-facility provisioning API and portal** started 2026-08-15 (`provisioning/`) as a
> deliberate schedule trade ahead of the transport chain. It now works end to end: signing into the
> portal and submitting a form provisions a facility, and a stack **actually starts** from that job
> on a real Docker daemon. ⚠️ That was proven against a **minimal stand-in compose file** — no
> OpenMRS, OpenELIS, Keycloak or adapter has ever booted this way. Public DNS/TLS also remains
> unconfigured. It is a separate system that **never touches clinical data**.
>
> First boot takes **≈50 minutes** and looks like a hang. Read the timing note before running it.
>
> The target state is **pilot-ready**, which is not the same as *clinically live*. Clinically live
> additionally requires the birth centre's clinical lead to sign off on the prenatal lab order set
> and the partograph form fields. **Nobody — human or agent — treats pilot-ready as authorization
> to put real patient data through this system.**
>
> Honest current assessment: [`CURRENT_STATE.md`](CURRENT_STATE.md).

---

## What this system does, and does not do

**Does:** register patients · record clinical observations and labour monitoring data · manage
maternity beds · place lab orders and route them to the laboratory · route results back to the
patient chart · notify the patient that results are ready · authenticate staff once across both
applications · back everything up nightly to off-site storage.

**Does not:** interpret results, score risk, triage, recommend treatment, or generate any clinical
output whatsoever. Every value displayed is a value a human entered. This is a **record-and-route**
system, deliberately, and that boundary is enforced in the project's rules rather than left to
judgement.

French is the primary language throughout — it is Djibouti's working medical and administrative
language, not a localisation afterthought.

---

## Where to look

Documentation hierarchy for a new reader or agent — read roughly top to bottom, then dive into a
component doc only once you're touching that component:

| File | What it is |
|---|---|
| [`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) | Rules for anyone (human or AI) working in this repo — read first |
| [`docs/MVP-1.0.md`](docs/MVP-1.0.md) | **The DHealth MVP 1.0 product contract** — product family, golden journey, the four product laws, current-vs-MVP gaps (ADR-043) |
| [`DECISIONS.md`](DECISIONS.md) | Architecture decision records — why things are the way they are |
| [`PROJECT_STATE.md`](PROJECT_STATE.md) | Tickets, blockers, critical path, session handoff notes |
| [`CURRENT_STATE.md`](CURRENT_STATE.md) | What actually works vs. what is merely written |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full design: topology, data flow, versions, build order (maternity/connector-track detail, plus §17 for the Exchange) |
| [`docs/FIELD-DIAGNOSTIC-WORKFLOW.md`](docs/FIELD-DIAGNOSTIC-WORKFLOW.md) | Generalized findings from real Djibouti diagnostic-workflow artifacts (no patient data) — the evidence behind ADR-046/ADR-047's multi-observation result domain (DH-EX-012C) |
| [`backup/README.md`](backup/README.md) | Backup operation, verification boundary, and status checks (maternity/connector-track component) |
| [`backup/restore-drill.md`](backup/restore-drill.md) | The performed restore, the **correct** restore procedure, and what it does not yet cover |
| [`provisioning/README.md`](provisioning/README.md) | Multi-facility provisioning API + portal — **a separate system that never touches clinical data** |
| [`docs/CLINICAL-SIGNOFF.md`](docs/CLINICAL-SIGNOFF.md) | The clinical lead's handover sheet for the maternity/connector-track stack. Inventories what it can already order; **nothing is pre-selected** |
| [`docs/WATERMARK-DESIGN.md`](docs/WATERMARK-DESIGN.md) | How the adapter will learn an order exists, written **before** any transport code (ADR-005 required it) — maternity/connector-track component |
| [`docs/UI-001-CLINICAL-UX.md`](docs/UI-001-CLINICAL-UX.md) | Earlier maternity/OpenMRS clinical-UI research — historical/component-specific, not the DHealth Clinic/Lab/Patient UX specification |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | CI — gates the full test suite plus Ruff, compose parsing, image pinning, secret and link checks (exact count drifts too often across suites to keep current here — see the latest CI run) |

---

## Deploying more than one facility

A second facility is a second isolated stack — **never a shared database**. `FACILITY_ID`
namespaces the Compose project and therefore every container, volume and network.

⚠️ `FACILITY_ID` is an identity, not a display name. Compose derives volume names from it, so
changing it later does not rename anything — it points the stack at fresh empty volumes and
orphans the patient database while appearing to start normally. Set it once, before the first `up`.

A second facility can be created **through the provisioning portal** — sign in, submit a form, and
the facility's host brings its stack up. It can also still be done by hand: copy `.env`, fresh
secrets, a distinct `FACILITY_ID`. See [`provisioning/README.md`](provisioning/README.md).

⚠️ **Not yet proven with the real clinical stack.** The provisioning path has started containers
from a job, but against a minimal stand-in — the ~49-minute OpenMRS first boot is unexercised on
that path, and public DNS/TLS is unconfigured.

How it works (ADR-022): facility hosts run a small agent that **polls outbound**
for jobs addressed to itself, so **no clinical host exposes an inbound port**. The compose file
lives on the facility host and the API sends only parameters — because Docker access is root access,
and an API that could hand a host an arbitrary container spec would be root on every clinical VM.
Each host generates its own credentials locally, so the API never holds a facility's database
passwords.

Enrolling a new host is deliberately manual and always will be: a host cannot poll for work until it
has an identity, and that first credential cannot be delivered automatically without recreating the
problem it solves.

## Prerequisites

- Ubuntu 24.04 LTS (a fresh VM is the assumed target)
- Docker Engine with the Compose plugin
- 4 vCPU / 16 GB RAM / 60 GB disk is the intended pilot sizing
- Your user must be able to reach the Docker daemon:

  ```bash
  sudo usermod -aG docker $USER   # then log out and back in
  docker info                     # should print server details, not "permission denied"
  ```

  Note that membership in the `docker` group is effectively root on that machine. On a server,
  decide that deliberately rather than by habit.

## Configuration

```bash
cp .env.example .env
chmod 600 .env
```

Then edit `.env` and fill in every value marked `CHANGE ME`. Generate passwords rather than
inventing them:

```bash
openssl rand -base64 30
```

**`.env` is gitignored and must never be committed.** `.env.example` carries placeholders only,
and every variable is documented there with a comment the moment it is introduced.
Keep `.env` mode `0600`: it contains database, application, and OIDC client credentials. The
2026-08-13 quality audit found the local file at `0664` and corrected it to `0600`.

The compose file uses `${VAR:?}` guards, so a missing required value fails loudly at startup
instead of silently falling back to a default password.

### ⚠️ An empty value is not the same as "left at a safe default"

DH-021 found this the expensive way. `OE_KEYSTORE_PASSWORD` and `OE_TRUSTSTORE_PASSWORD` were
**empty** in a working `.env`, and compose read them as `${OE_KEYSTORE_PASSWORD:-kspass}` — so an
empty value silently selected upstream's *published* password while the variable looked configured.
Both are now `:?` required and must be generated:

```bash
openssl rand -base64 32
```

If you are copying an older `.env` forward, fill these in; `docker compose up -d` will refuse to
start until you do. That refusal is the feature.

### Required values at a glance

Everything below must have a real value before the first `docker compose up -d`:

| Variable | Notes |
|---|---|
| `OMRS_DB_PASSWORD`, `OMRS_DB_ROOT_PASSWORD` | MariaDB application and root |
| `OMRS_ADMIN_PASSWORD` | ⚠️ **first boot only** — see below |
| `OE_DB_PASSWORD`, `OE_DB_SUPERUSER_PASSWORD`, `OE_POSTGRES_PASSWORD` | three *separate* PostgreSQL credentials |
| `OE_ADMIN_PASSWORD` | narrower alphabet — letters, digits, and `* $ # !` only |
| `OE_KEYSTORE_PASSWORD`, `OE_TRUSTSTORE_PASSWORD` | DH-021; never `kspass`/`tspass` |
| `KEYCLOAK_DB_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD` | |
| `KEYCLOAK_OPENMRS_CLIENT_SECRET`, `KEYCLOAK_ADAPTER_CLIENT_SECRET` | |
| `KEYCLOAK_EXCHANGE_API_CLIENT_SECRET` | DH-EX-008; a *different* realm (`dhealth-exchange`) and client — never reuse the two secrets above |
| `ADAPTER_DB_PASSWORD` | |
| `BASE_DOMAIN` | `localhost` for local work; the real domain on the pilot VM |

OpenELIS password policy is narrower than a general password generator: its admin password must
use only letters, numbers, and `* $ # !`, must be at least seven characters, and must contain at
least one of those four special characters. `.env.example` includes a compliant generation command.
DH-024 verified that the image ignores its documented `DEFAULT_PW`; a post-bootstrap one-shot now
rotates and verifies the database hash before the OpenELIS frontend may start.

### ⚠️ The OpenMRS admin password is a first-boot-only setting

The O3 backend image defaults the `admin` account to the well-known published password
`Admin123`, and it writes that password into the database **the first time OpenMRS initialises an
empty schema**. Changing it afterwards in `.env` does nothing — the account already exists.

DH-019 now passes required `OMRS_ADMIN_PASSWORD` through
`OMRS_CONFIG_ADMIN_USER_PASSWORD`; compose refuses to start if it is missing. This was verified on
an empty volume: the configured password authenticated and `Admin123` was refused. On a database
created before DH-019 landed, `.env` cannot rotate the existing account—change it manually in
OpenMRS.

### SSO is fail-closed until the authorization matrix is approved

`SSO_ENABLED=false` is deliberate. The technical path is built and exercised: OpenMRS uses OIDC
through official OAuth2 Login 1.5.0; OpenELIS uses its native SAML path (ADR-014); and the one-shot
Keycloak client reconciliation works for existing databases as well as fresh imports. The applications
consume their own exact native role names, not the high-level Keycloak labels. Mapping
`clinician`, `lab-tech`, and `sys-admin`, and deciding who is an OpenMRS provider, grants access to
clinical records and laboratory functions. That matrix is BLOCKER-5 and requires Liban's approval.

For break-glass/local authentication, keep or set:

```bash
SSO_ENABLED=false
docker compose up -d
```

Then verify the configured local administrator before investigating Keycloak. With SSO disabled,
the OpenMRS runtime file contains only `oauth2.enabled=false` and is mode `0600`; the OIDC client
secret is not persisted there. Rotate a local credential after emergency use. Do not set
`SSO_ENABLED=true` until BLOCKER-5 is resolved and DH-016 provides the public HTTPS origins needed
for the browser session demonstration.

## Running the current foundation

```bash
docker compose up -d
docker compose logs -f openmrs-backend
```

This starts the currently configured foundation, not the backup scheduler. Backup is deliberately
behind the `backup` profile until its encryption key and approved off-site destination exist:

```bash
docker compose --profile backup up -d backup
```

Do not enable that profile merely to make the container list look complete. DH-014 still requires
production key escrow and approved off-site settings. DH-015's restore **was performed**
2026-08-13, but with partial coverage — it must be re-run once a lab result and an SSO login exist
to check against.
Once those settings are provisioned, set `COMPOSE_PROFILES=backup` in `.env`; the normal
one-command `docker compose up -d` then includes the scheduler.

### ⏱ The first boot takes about 50 minutes. This is normal. Do not restart it.

Measured on 2026-08-11, 4 vCPU / 30 GB: **49 minutes** from `up -d` to the first HTTP 200.
A **subsequent** boot on the same volumes took **38 seconds**.

Almost all of that first-boot time is the O3 distribution importing 23 Open Concept Lab packages
(~6 MB zipped, the largest containing 2,186 concepts and 13,472 mappings). While that runs:

- the log goes quiet for long stretches — this is **not** a hang;
- port 8080 is not listening yet, so the browser will refuse to connect;
- after ~30 minutes `docker compose ps` may show the backend as **unhealthy**. Nothing restarts
  it, and it recovers on its own. Restarting it throws away the import and starts over.

To watch real progress rather than guess, count what has actually landed:

```bash
docker exec daher-health-openmrs-db-1 sh -c \
  'mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" openmrs \
   -e "SELECT (SELECT COUNT(*) FROM openconceptlab_import) imports,
              (SELECT COUNT(*) FROM openconceptlab_item) items;"'
```

`imports` climbs towards 23 and `items` towards ~27,000. If both are increasing, it is working.

Wait for readiness with an explicit check rather than by eye:

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:8080/openmrs/ws/rest/v1/session)" = "200" ]; do sleep 15; done; echo READY
```

Then open <http://127.0.0.1:8080/openmrs/spa>.

That port binds to `127.0.0.1` only. On a remote VM, reach it through an SSH tunnel rather than
opening a firewall port:

```bash
ssh -L 8080:127.0.0.1:8080 user@your-vm
```

## Reaching the system through the reverse proxy (DH-016)

Caddy is the **only** container that publishes host ports. Everything else — both databases, both
FHIR endpoints, Keycloak, the adapter — stays on the internal Docker network, which is what makes
"not publicly routed" a fact rather than an intention.

Routing is by **subdomain, not path prefix** (ADR-008):

| URL | Serves |
|---|---|
| `https://emr.<BASE_DOMAIN>` | OpenMRS O3 (via the distro's own gateway, ADR-010) |
| `https://lab.<BASE_DOMAIN>` | OpenELIS — SPA at `/`, API at `/OpenELIS-Global/*` |
| `https://id.<BASE_DOMAIN>` | Keycloak |

⚠️ **OpenELIS's SPA and API must share one origin.** The built bundle calls `/OpenELIS-Global/...`
as a *relative* path, and its own nginx serves static files only. Splitting those across two
hostnames breaks every API call the SPA makes. Do not "tidy" that into separate vhosts.

### With `BASE_DOMAIN=localhost` (local work)

Caddy issues certificates from its **internal CA**. Browsers will warn — that is expected and is
not a misconfiguration. This proves routing, origins, and cookie behaviour. It proves **nothing**
about public certificate issuance.

Set the host ports so they do not collide with anything already running:

```bash
CADDY_HTTP_PORT=8081
CADDY_HTTPS_PORT=8443
```

Then reach it at `https://emr.localhost:8443`. `*.localhost` resolves to loopback on most systems;
if yours does not, add entries to `/etc/hosts`.

### On the pilot VM, with a real domain

1. Point `emr`, `lab`, and `id` A/AAAA records at the VM.
2. Set `BASE_DOMAIN` to the real domain and `CADDY_HTTP_PORT=80`, `CADDY_HTTPS_PORT=443`.
3. Open **only** 80 and 443 on the firewall. Port 80 is required for ACME.
4. Add an `email` line to the global block in [`caddy/Caddyfile`](caddy/Caddyfile) so certificate
   expiry warnings reach a human.

⚠️ **Do not add an empty `email` value.** Caddy fails to *parse* the whole file if `email` is
present with no argument, which takes the entire proxy down rather than degrading. That is why the
directive is absent rather than blank.

Publicly trusted certificates have **not** been demonstrated — that needs the real domain and DNS,
which the facility has not yet provided.

### Verifying step 1

**What has been verified, and by whom.** The REST/FHIR and persistence checks below were exercised
with `curl` on 2026-08-11. Liban then opened the single-page app and registered a test patient,
closing DH-002. DH-003 later verified French in both frontend environment and backend properties;
Liban has not yet reported whether the rendered UI visibly opens in French.

1. **Log in.** Open <http://127.0.0.1:8080/openmrs/spa> with `admin` and the password configured
   before that database's first boot. Existing pre-DH-019 volumes retain their existing password.
2. **Register a test patient**, then search for them and open the chart.
3. **Check the French default.** The frontend has `SPA_DEFAULT_LOCALE=fr`; DH-003 also sets
   backend `default_locale` and `reporting.defaultLocale` to `fr`, with `fr` first in the allowed
   list. The configuration is verified; the visible browser result still needs your look.
4. **Confirm persistence:**
   ```bash
   docker compose down          # NEVER `down -v` — that deletes the volumes and the patient
   docker compose up -d         # ~38 seconds on an already-populated volume
   ```
   Then search for your patient again. It should still be there.

The same checks from the command line, if you want them:

```bash
# does the API authenticate?
curl -s -u "admin:<configured-password>" http://127.0.0.1:8080/openmrs/ws/rest/v1/session

# is a given patient retrievable by identifier, and through FHIR R4?
curl -s -u "admin:<configured-password>" "http://127.0.0.1:8080/openmrs/ws/rest/v1/patient?q=<IDENTIFIER>"
curl -s -u "admin:<configured-password>" "http://127.0.0.1:8080/openmrs/ws/fhir2/R4/Patient?identifier=<IDENTIFIER>"
```

### ⚠️ Demo patients are an explicit first-boot option

`OMRS_ADD_DEMO_DATA=false` **does not prevent this** — that variable was verified inert at DH-002.
The actual control is `OMRS_DEMO_PATIENTS=off|on`, implemented at the distribution's Initializer
source. It defaults to `off`, the only appropriate value for an instance that may hold real patient
records. `on` deliberately creates 50 fictional people and is for training/demo instances only.

Choose this before first boot. The module zeroes its property after firing and Initializer leaves
an unchanged file alone, but switching an existing database from `off` to `on` changes that file's
checksum and can inject 50 fictional patients. Switching to `off` never removes existing patients.
The original local verification volume predates DH-024's sole-writer correction and now contains
100 fictional patients from two 50-row demo batches, plus two separate deliberate test patients.
It is a throwaway test stack, not a clean pilot database; no patient rows were deleted during the
fix.

⚠️ **OpenELIS in this local stack also holds one synthetic patient** — `id=2`,
`Dhspike Zzsynthetic`, created by DH-011B's ingestion spike on 2026-08-17 and retained rather than
deleted, consistent with never removing patient rows to tidy up. No sample, analysis or
`electronic_order` exists, and no laboratory test or configuration was modified. Same rule applies:
this is a verification stack, never to be confused with a clean instance.

`OMRS_DEMO_CONTENT=on|off` is separate. Its default `on` preserves the O3 reference distribution
as shipped, including `BasicLabTests` and other vocabulary that later mapping may need. `off`
selects a locally built backend target whose exact digest-pinned upstream base omits only the
`referenceapplication-demo` source directories. It does not delete concepts or other metadata
already imported into an existing database, and it is not a substitute for clinical sign-off.

Demo patients require a deliberate two-stage setup because the upstream generator runs before its
`OpenMRS ID` metadata is available on a fresh database:

1. Boot once with `OMRS_DEMO_CONTENT=on` and `OMRS_DEMO_PATIENTS=off`; wait for healthy.
2. Change only `OMRS_DEMO_PATIENTS=on` and run `docker compose up -d` again.

DH-024's isolated proof created exactly 50 patients with one creator and reset the sole trigger to
zero. A database preflight rejects first-boot `on/on`; the backend separately rejects
content-off/on. Neither invalid state can silently reset the trigger with zero requested patients.

### Maternity ward and bed counts

`Maternité`, the delivery/postpartum bed types, and the backend French properties are loaded
without admin clicking. Set `MATERNITY_DELIVERY_BEDS` and `MATERNITY_POSTPARTUM_BEDS` in `.env`,
then run `docker compose up -d`. The committed `0/0` values are deliberate placeholders pending
the birth centre's real counts.

The seed is strictly additive (ADR-007): raising a count adds missing beds; lowering it does
not remove, retire, move, or edit anything. If existing ward/type/bed metadata conflicts with the
committed definition, the setup job exits non-zero and the frontend dependency gate stays closed.

## Verifying the whole stack

Run these after `docker compose up -d` settles. Each one asserts something an application must
actually be doing — not that a port is open. **This distinction has bitten this project three
times**, so prefer these to eyeballing `docker compose ps`.

```bash
# 1. Everything up, nothing restarting or OOM-killed.
docker compose ps
for c in $(docker ps -a --filter name=daher-health --format '{{.Names}}'); do
  printf '%-42s restarts=%s oom=%s\n' "$c" \
    "$(docker inspect -f '{{.RestartCount}}' $c)" \
    "$(docker inspect -f '{{.State.OOMKilled}}' $c)"
done
```

```bash
# 2. OpenMRS authenticates with YOUR password and refuses the published one.
docker exec daher-health-openmrs-backend-1 curl -s \
  -u "admin:<your-password>" \
  'http://localhost:8080/openmrs/ws/rest/v1/session?v=custom:(authenticated)'   # authenticated:true
docker exec daher-health-openmrs-backend-1 curl -s \
  -u "admin:Admin123" \
  'http://localhost:8080/openmrs/ws/rest/v1/session?v=custom:(authenticated)'   # authenticated:false
```

```bash
# 3. OpenELIS application context is genuinely up.
#    A 302 from / proves only that Tomcat is listening — the Spring context can be
#    completely dead and still return it. Assert the login form instead.
docker exec daher-health-openelis-webapp-1 sh -c \
  'curl -ksS https://localhost:8443/OpenELIS-Global/LoginPage | grep -c _csrf'   # non-zero
```

```bash
# 4. Database tenant isolation — all nine cases. Each role reaches its own
#    database and is refused by the other two. Use -h: without it, psql may
#    connect over a trusting local socket and tell you nothing about passwords.
docker exec -e PGPASSWORD="$OE_DB_PASSWORD" daher-health-openelis-db-1 \
  psql -h openelis-db -U clinlims -d keycloak -c 'SELECT 1'    # must FAIL
```

```bash
# 5. The proxy serves all three vhosts, and the OpenELIS API leg is TLS-verified.
docker exec daher-health-caddy-1 sh -c '
  for u in emr.$BASE_DOMAIN lab.$BASE_DOMAIN id.$BASE_DOMAIN; do
    wget -qO- --no-check-certificate --server-response --spider https://$u/ 2>&1 | grep -m1 HTTP/
  done'
```

```bash
# 6. Adapter readiness (asserts the response body, not just a 200).
docker exec daher-health-adapter-1 python -c \
  "import json,urllib.request; print(json.load(urllib.request.urlopen('http://localhost:8000/health/ready')))"
```

## Backup and restore

```bash
docker compose --profile backup run --rm \
  -e BACKUP_ENCRYPTION_PASSPHRASE="<your-passphrase>" \
  -e BACKUP_REQUIRE_OFFSITE=false \
  backup /usr/local/bin/daher-backup
```

⚠️ **Keep the passphrase.** An encrypted archive whose key you did not retain is not a backup. A
prior verification run had to delete its own archive for exactly this reason.

**Read [`backup/restore-drill.md`](backup/restore-drill.md) before attempting a restore.** It
documents two traps found by actually performing one:

- `pg_restore --clean --if-exists` over OpenELIS's pre-populated database produced **213 errors
  including failed row inserts** — silent data loss behind a run that looked successful. Drop and
  recreate each database first.
- That drop-and-recreate then **discards tenant isolation**, because a fresh PostgreSQL database
  grants `CONNECT` to `PUBLIC`. Re-run the hardening and re-verify all nine cases afterwards. The
  restore is not complete until that passes.

## Not yet present

OpenMRS ANC and partograph forms, the prenatal order set, SSO authorization/activation and the
one-login proof, adapter order/result transport, WhatsApp notifications, off-site backup, and the
end-to-end demonstration. Sequence and status: [`PROJECT_STATE.md`](PROJECT_STATE.md).

Partially done, and the boundary matters:

- **Adapter order path** — the OpenMRS half is **built and verified but not activated** (DH-011E:
  poller, durable watermark, cohort completeness, client-side ordering, 94 tests). Nothing runs it,
  because **how an order reaches OpenELIS is an open decision** (BLOCKER-6). The laboratory *pulls*
  from a remote FHIR source rather than accepting a push, and it has never received an order. Five
  candidate designs are recorded; none is chosen.
- **Restore drill** — performed, but two acceptance legs (a known lab result, an SSO login) could
  not be exercised because neither exists yet. It must be re-run. `backup/restore-drill.md` §6.
- **Reverse proxy** — routing and local HTTPS verified; publicly trusted certificates need the real
  domain and DNS.
- **Analyzer interfaces** — deliberately **out of scope for v1** (ADR-016). Lab technicians enter
  results by hand, as they currently do on paper. HL7/ASTM integration is a v1.1 question. The
  shipped Horiba imports are left untouched and still log errors on boot; that is known, not new
  breakage.

Deliberate placeholders — **not gaps to be quietly filled**: maternity bed counts, WhatsApp
credentials (none held yet), the prenatal lab order set, and the partograph form fields. The last
two need clinical sign-off; the first two need real values from the facility.

---

## Security notes

- All patient data stays in this stack's own databases, on the facility's own VM. No patient data
  is sent to any third-party AI/ML service.
- The only outbound patient-adjacent traffic is a content-free WhatsApp notification ("your results
  are ready" — never result values) and the encrypted nightly backup.
- Databases, FHIR endpoints, and the adapter are never publicly routed; only the three application
  subdomains are, and only over HTTPS. Caddy is the sole container publishing host ports.
- Every image is version-pinned. No `:latest` tags anywhere.
- Every published default credential found so far is actively refused, and that refusal is tested:
  OpenMRS `Admin123` (DH-019), the OpenELIS database roles (DH-020), and OpenELIS `adminADMIN!`
  (DH-024). Each test includes a deliberately-wrong control, because a check that passes for the
  wrong reason is worse than no check.
- The internal 8443 keystore is generated **on your deployment** (DH-021). It replaced upstream's
  pre-built keystore, whose private key ships inside a public image and whose certificate had
  already expired — while nothing on that path validated expiry. The proxy *verifies* that
  certificate rather than skipping verification, so the hop from the public edge to the clinical
  backend is authenticated.
- PostgreSQL hosts three databases as co-tenants, and each role is refused by the other two
  databases. Co-tenancy is a deployment convenience, not a relaxed trust boundary.

## Licence

Not yet determined.
