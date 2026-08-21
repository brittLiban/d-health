# Provisioning API and portal

Creates and manages **facility deployments** of the clinical stack. Hospitals and labs are
independently provisionable; neither implies the other.

> ## ⚠️ This system never touches clinical data
>
> It provisions and monitors: creates facilities, checks health, tracks backup status and image
> versions. It does **not** read, display, derive, or aggregate anything originating from a patient
> record — no patient counts, no cross-facility dashboards, no results, not one summary stat.
>
> A change that would require otherwise is a hard stop, `Human Decision Required: Yes`. That is not
> a style preference: it is the line between a fleet-management tool and a reportable incident.
> See **ADR-021**.

## Status

**The API, portal, agent and Compose executor all exist and have been demonstrated end to end —
against a stand-in compose file.** No OpenMRS, OpenELIS, Keycloak or adapter has ever booted through
this path; that is DH-027J and remains the Provisioning V1 gate.

`POST /facilities` registers a facility record and queues a provisioning job, reporting what did
*not* happen in `incomplete_steps`. **A 201 means "recorded and queued", not "running"** — DNS
provisioning does not exist, and the response says so rather than leaving a caller to infer success.

| Endpoint | State |
|---|---|
| `GET /health` | Works. Unauthenticated, reveals nothing |
| `POST /facilities` | Records a facility; **deploys nothing** |
| `GET /facilities` | Works |
| `GET /facilities/{id}/status` | Works. Reports **lifecycle state and observed infrastructure health separately** (DH-027D). Counts and health come from the last real STATUS check; `checked_at` is `null` and health is `unknown` when none has been reported |
| `POST /facilities/{id}/decommission` | Soft only. Sets `decommissioning`, queues a STOP; `decommissioned` only on the agent's confirmed report (DH-027E) |
| `POST /facilities/{id}/rotate-credentials` | Rotates TLS + client secrets; **refuses the rest** (ADR-024) |

### ⚠️ The poll loop is WRITTEN, NOT DEMONSTRATED

`daher_agent/poller.py` is unit-tested against injected transport and executor. What is **not**
proven on one machine is the claim the design rests on — *polls a separate host over a network and
refuses hostile input over the wire*. That needs two hosts, or two containers on separate networks
standing in for them. Until that exists, this is written code, not verified behaviour, and the same
distinction this repo applies to the WhatsApp mock and off-site backup applies here.

What **is** tested: the loop stays alive. An agent that crashes on hostile input has been switched
off by the attacker, and a host that is not polling is also not getting backups or updates. So an
unreachable API, a malformed payload, a smuggled `image` param, an executor that raises, and a
reporter that raises are each survived — and a hostile job is refused *and the next legitimate job
still runs*.

A refusal is reported as `refused`, never `failed`. The boundary working must not read as the
deployment breaking.

### The executor — the last command boundary

`executor.py` is where a parameter finally reaches a command, so five rules hold absolutely:

1. **Never a shell.** Every invocation is an argv *list* with `shell=False`. There is no string
   for a metacharacter to live in, which removes command injection as a *category* rather than
   filtering for it.
2. **The compose file is fixed and local**, from this host's configuration and never from the
   job. An issuer who could name a compose file could name any container spec — and Docker
   access is root access. That one rule is what keeps ADR-022's walkthrough true.
3. **The facility id is validated a third time**, immediately before it becomes an argument.
4. **Secrets are generated here**, on the host. The job carries none; the API has never held any.
5. **`stop`, never `down`.** `down` removes containers and networks and is one flag from removing
   volumes. A test asserts no verb can ever emit `--volumes` or `-v`.

Tests assert the **exact argv**, not that the call succeeded — a test checking only the return
value would pass identically whether the command was a safe list or a shell string with the
facility id interpolated into it. Both guards are control-verified: removing the executor's own
validation makes a hostile id reach the command and fails the suite; switching to `shell=True`
fails it too.

### The agent's job validation — the actual boundary

ADR-022's residual risk is stated plainly: *a breached API can enqueue a job with hostile
parameters.* `daher_agent/validation.py` is the only thing standing in the way, so it treats
**every field as untrusted regardless of the job having arrived authenticated.** "The API already
validated it" is the reasoning that would make that module decorative — the API's checks protect
against mistakes, these protect against the API itself.

Five refusals, each written as an attack in the tests:

1. A job not addressed to this host.
2. A verb outside the fixed set.
3. A parameter failing local re-validation — including twelve hostile `facility_id` values
   (`; rm -rf /`, `$(whoami)`, backticks, pipes, newlines) that would otherwise reach a Compose
   project name.
4. A stale or replayed job, so a captured one cannot be banked and fired later.
5. **Provisioning over a facility that already exists here.** The API has the same check, but a
   breached API can skip its own — this one it cannot reach.

**A job carries parameters, never a spec.** `jobs.py` has no field that can name an image, a
command, a volume, or a compose file, and a test asserts that for every field of both models. If
that test ever fails, ADR-022's blast-radius walkthrough has stopped being true — control-verified
by adding an `image` param and watching it fail with exactly that reasoning.

### Deploy transport (ADR-022)

Chosen: **pull-based host agent, outbound-only**. Facility hosts poll for jobs addressed to
themselves; **no clinical host exposes an inbound port**. Not built yet.

The constraint that shaped it: **Docker access is root access**, so the question is never "can an
attacker get a shell" but "can an attacker cause an arbitrary container spec to run". Hence the
rule — **the compose file lives on the facility host; the API sends parameters, never a spec.**

Two consequences already implemented:

- **`deploy_target` is resolved, not supplied.** A create request names a `host_id` an operator
  registered; supplying an address is rejected by the closed schema. Otherwise a breached portal
  could point provisioning at any address it chose.
- **An occupied host is a hard conflict.** Reusing one would overwrite a live env file and attach a
  new facility to an existing patient database — the same shape as the `FACILITY_ID` default trap,
  arriving through a request that looks entirely routine.

And one that changes the blast radius most: **the API does not generate facility credentials.** The
agent generates them locally when it pulls its job, so a breached API host does not hold — and has
never held — any facility's database passwords.

What *is* real: scoped authentication, rate limiting, and an audit record of every action —
**including denials and failures** — written by this service from what it actually did.

Two things are **deliberately incomplete and must not be reported as working**:

- **DNS/TLS** — no domain or DNS provider exists (NEEDS LIBAN #15). Built against a
  provider-agnostic interface, left unconfigured, and reports `dns_configured: false`.
- **Audit log external destination** — the requirement is a log that survives the API host being
  rebuilt or tampered with. The interface will exist before the destination does.

✅ **Known bug 17 is fixed (2026-08-16): the timeout is now per verb.** A single 900-second bound
was shorter than a real OpenMRS first boot (~49 minutes), so the first genuine provisioning run
would have been reported as *failed* while the stack was coming up correctly.

`up --detach` does not return once containers exist — this compose file gates the frontend, Caddy,
the adapter and every setup job behind `service_healthy` on `openmrs-backend`, whose healthcheck
alone declares `start_period: 1800s`. So the verbs that wait on that graph (`provision`, `start`,
`rotate_tls`) get 5400s, `backup_now` gets its own bound because it scales with stored data, and
`status`/`stop` stay tight at 900s. Both long bounds are settable per host
(`STACK_TIMEOUT_SECONDS`, `BACKUP_TIMEOUT_SECONDS`) because facility hardware differs.

The bound is checked **against the real compose file** rather than asserted as an opinion: a future
healthcheck with a longer grace period fails the test suite instead of the first deployment.
Control-verified three ways — putting 900s back fails the two guards by name, wiring `provision` to
the fast bound fails the per-verb tests, and running without the compose file mounted **fails**
rather than skipping.

✅ **Known bug 18 is fixed (2026-08-20): the poll loop no longer blocks on a running job.**
`execute` now runs on a background thread, so `poll_once` returns as soon as a job is dispatched
and the loop keeps calling `fetch` on its normal cadence — a job issued while a `provision` is up
is validated promptly instead of only after the provision returns. `status` is read-only and always
runs, including alongside an in-flight mutating job; every other verb shares one **mutating** slot,
so at most one runs at a time — a second one claimed mid-flight is refused immediately (`"busy"`,
reported the same way a validation refusal is) rather than racing two `docker compose` invocations
against the same project. `JobValidator` still runs synchronously, before dispatch, with the same
900s window — ADR-022's replay protection was not touched, and does not need to be: the fix is that
the loop stops being blocked long enough to hit it in the ordinary case. `executor.py`,
`validation.py`, `jobs.py` and `queue.py` are all unchanged.

⚠️ **The backup bound is unmeasured.** No backup has ever been timed against a real clinical
database, and DH-014's off-site leg is still unexercised. It is set above the fast bound only so a
data-sized job does not inherit a bound chosen for `ps`; replace it with a measured value.

Both report their incompleteness rather than appearing to succeed. A provisioning step that
silently "succeeds" against nothing is a false green, and this project has found five of those.

## Why the API cannot leak clinical data

Three layers, weakest to strongest:

1. **Closed schemas** — every model sets `extra="forbid"`, so a clinical field cannot be attached
   downstream by a dict that happens to carry one.
2. **A vocabulary test** — `tests/test_no_clinical_data.py` walks every model and fails the build if
   a field is named for a patient, encounter, result, specimen, diagnosis and so on.
3. **No clinical database credentials exist in this service at all.** It talks to deployment and
   health surfaces, never to `openmrs` or `clinlims`.

The third is the one that matters. The first two catch mistakes; the third makes the mistake
unreachable — it does not depend on anyone continuing to be careful.

**This is control-tested, not assumed.** Adding a `patient_count` field to `FacilityStatus` makes
two independent tests fail with the ADR-021 explanation. If you change the guard, re-run that
control — a security check nobody has tried to defeat is worth very little.

## Layout

```
provisioning/
  src/daher_provisioning/
    secrets.py       per-facility credential generation
    models.py        facility records + closed API schemas
    facility_env.py  renders and writes a facility's .env
    audit.py         tamper-evident hash-chained audit log
    auth.py          scoped credential authentication
    ratelimit.py     per-caller token bucket
    repository.py    facility record storage
    hosts.py         registered deploy hosts (the allowlist)
    jobs.py          the job vocabulary shared with the agent
    enrolment.py     single-use host enrolment tokens
    queue.py         per-host job routing
    network.py       source allowlist (defence in depth)
    store.py         SQLite persistence for the control plane
  src/daher_portal/
    security.py      login, CSRF, password hashing
    app.py           the server-rendered console
    templates/       base, login, facilities
  src/daher_agent/
    validation.py    the agent's local job validation — the real boundary
    poller.py        the poll loop (⚠️ WRITTEN, NOT DEMONSTRATED)
    executor.py      runs compose — the last command boundary
    app.py           the FastAPI surface
  tests/
    test_secrets.py           generation, uniqueness, redaction
    test_no_clinical_data.py  the structural guarantee
    test_facility_env.py      rendering, permissions, compose sync
    test_audit.py             recording, tamper detection, no secrets
    test_auth.py              scopes, credential handling, rate limiting
    test_app.py               the boundary, enforced through HTTP
    test_hosts.py             the allowlist and the occupied-host refusal
    test_agent_validation.py  every refusal, written as an attack
    test_enrolment.py         single-use, expiry, host binding
    test_queue_and_poller.py  routing, and the loop staying alive
    test_network.py           the allowlist, and the header it must not believe
    test_executor.py          the exact argv, not 'it worked'
    test_store.py             restart survival, end to end
    test_portal.py            what must NOT happen in a browser
```

## The facility env file

`render_env()` produces everything a facility's stack needs; `write_env()` writes it at mode
`0600`, created restricted from the outset rather than chmod-ed afterwards — a world-readable
window, however brief, is a window in which every credential is readable.

**It refuses to overwrite an existing env file.** A facility's databases are initialised with the
credentials from their first boot. Rewriting the file with fresh secrets does not rotate anything:
it leaves a running stack whose passwords no longer match its own databases, and an
`OMRS_ADMIN_PASSWORD` that was only ever applied on first boot. Rotation is a separate, deliberate
operation, and `overwrite=True` exists for it — not as a retry convenience.

### The sync test

`test_renders_every_variable_the_real_compose_file_requires` parses the actual
`docker-compose.yml` for `${VAR:?}` guards and asserts the renderer produces all of them. When
someone adds a required variable to the clinical stack, **that test fails** rather than a facility
failing at `docker compose up` after it has already been provisioned.

It is control-tested: removing one variable from the renderer makes it fail, naming the exact
variable. Run it with the compose file mounted:

```sh
docker run --rm \
  -v "$PWD/docker-compose.yml:/docker-compose.yml:ro" \
  -v "$PWD/provisioning/src:/app/src:ro" \
  -v "$PWD/provisioning/tests:/app/tests:ro" \
  daher-provisioning-test pytest -q
```

## Observed infrastructure status (DH-027D)

The control plane answers **two different questions** and never merges them:

| Field | Question | Source |
|---|---|---|
| `state` | What lifecycle phase is this facility in? | Requests and confirmed agent reports over time |
| `health` | What did the **last real check** see? | One STATUS observation |
| `checked_at` | When was that check? | `null` when none has ever been reported |

A facility can be lifecycle `running` while its stack is `degraded` right now. One badge for
both is how a dashboard reports a stack that has been down all day.

**How a check is made.** The API queues a `STATUS` job; the facility's agent claims it and runs
two read-only commands through the fixed local compose file — `docker compose config --format
json` and `docker compose ps --all --format json` — then reports a normalised result. The
control plane never touches the host (ADR-022).

`config` is consulted as well as `ps` for two reasons: `ps` can only show containers that
*exist*, so a declared-but-absent service would be invisible; and `config` respects active
profiles, so a facility without off-site backup is not marked degraded for lacking a backup
container it was never meant to run.

**Health values.** `healthy` · `degraded` · `unknown`. `unknown` is **not** a synonym for
healthy and is shown as a warning: never checked, the agent could not run the check, the output
was unreadable, or the report failed validation.

**A zero exit code is not health.** `docker compose ps` returns 0 while printing an `exited` or
`unhealthy` service, so the verdict comes from parsed service states. A long-running service
that is missing, unhealthy, or exited — **including exited(0)** — degrades the deployment. A
one-shot setup job that exited(0) does **not**: that is its correct terminal state. Setup jobs
are identified from the compose file's own `depends_on: service_completed_successfully`, not
from a hand-maintained list, and not from restart policies — every service in the clinical
compose file declares one, including the one-shots.

**Health cannot be asserted by omission.** A STATUS report with no infrastructure evidence, or
with evidence that fails schema validation, is persisted as `unknown` with the reason. A buggy
or compromised agent cannot report `healthy=true` and bypass the service-state schema.

**Infrastructure only.** Service names, container states, counts, an image reference, a
timestamp. Never a patient count, encounter, result, specimen or anything else derived from a
patient record — `tests/test_no_clinical_data.py` walks these models and is control-verified
against them.

⚠️ **Freshness is displayed, not enforced.** There is no scheduler: a STATUS job is queued when
something asks for one, so an observation is only as fresh as the last request. The portal shows
its age ("31s ago", "3 h ago", "never checked") rather than a green badge that stays reassuring
indefinitely. How often a fleet should be polled is an operations decision, not one made here.

⚠️ **Only the latest check is kept.** A dashboard needs what is true now; a history of container
states is a metrics problem and building one here would turn the control plane into a telemetry
platform for no current need. The audit log still records that each check happened.

## Enrolling a facility host — the one manual step

A host cannot poll for work until it has an identity, and that first credential cannot be delivered
automatically without recreating the problem it solves. So enrolment is manual, permanently:

1. Provision a bare VM.
2. Obtain a **single-use, short-lived enrollment token** from the API.
3. Run the bootstrap command on the VM with that token.
4. The agent generates its own long-lived credential locally and registers its host id. The
   enrollment token is consumed and cannot be reused.
5. From then on the agent polls outbound. **No inbound port is ever opened.**

**The property that makes the manual step worth it:** the agent generates its own long-lived
credential locally and sends **only the hash**. The provisioning API never sees an agent's secret,
not even during enrolment — so a breached API host cannot impersonate any facility agent, past or
future. Passing a secret where the hash belongs is refused, so the dangerous wiring is not
available even by mistake.

### Configuring the host agent

Every variable `daher_agent.runner` reads, documented here because `main()` was previously the only
record of them:

| Variable | Default | What it does |
|---|---|---|
| `PROVISIONING_URL` | *required* | The API base URL. HTTPS with verification on — the agent follows **no redirects**, because being pointed elsewhere by whoever answers is not something an outbound-only agent should honour |
| `AGENT_CREDENTIAL` | *required* | This host's long-lived credential, generated **on this host** at enrolment. The API holds only its hash |
| `HOST_ID` | *required* | This host's identity. The agent refuses any job not addressed to it — that refusal is the real boundary, not the transport |
| `FACILITY_ROOT` | `/var/lib/daher/facilities` | Where facility env files live. A facility "exists here" if its env file does, asked of the filesystem rather than of the API |
| `COMPOSE_FILE` | `/opt/daher/docker-compose.yml` | The clinical stack compose file. ⚠️ **Fixed and local, never from a job.** An issuer who could name this could name any container spec, and Docker access is root access (ADR-022) |
| `POLL_SECONDS` | `5.0` | Poll interval |
| `BASE_DOMAIN` | `localhost` | Rendered into the facility env file |
| `STACK_TIMEOUT_SECONDS` | `5400` | Bound for verbs that wait on the compose health graph (`provision`, `start`, `rotate_tls`). Raise it on slower hardware — the default is sized to a ≈49-minute cold boot measured on 4 vCPU |
| `BACKUP_TIMEOUT_SECONDS` | `3600` | Bound for `backup_now`, which scales with stored data. ⚠️ **Unmeasured** — replace it with a real figure once a backup has been timed against a real database |

`BACKUP_ENCRYPTION_PASSPHRASE` is deliberately **not** in this list and is never generated by the
agent: a key that must survive the loss of a machine cannot be created on that machine (ADR-025).
The operator supplies it at bootstrap.

Three refusals close the ways a leaked token would otherwise still be useful: it is **single-use**
(a replay is refused even inside its lifetime), **short-lived** (a token that reaches a shell
history expires on its own), and **bound to one host** (a token for `host-a` cannot enrol `host-b`,
so it cannot introduce an attacker-controlled host into the fleet). Minting for an
already-enrolled host is refused rather than silently replacing a live agent's credential.

Steps 1–3 do not disappear no matter how automated the rest becomes. Describing this system as
"no manual steps" would be false, so it is written down instead.

Until a host has enrolled, it can be registered but **not claimed** — assigning a facility to a
host whose agent has never bootstrapped is refused rather than queued.

## Backup wiring — and the key that must not live on the host (ADR-025)

The build prompt asks that a new facility gets a scheduled backup automatically. Implementing it
found two problems.

**The passphrase would have lived only on the machine being backed up.** ADR-022 has the agent
generate a facility's credentials locally — which, applied to `BACKUP_ENCRYPTION_PASSPHRASE`,
produces archives encrypted with a key that dies with the host. Lose that host, which is the exact
disaster backups exist for, and every off-site archive is permanently undecryptable. That is not a
backup; it is an archive nobody can open, which is worse because it looks like one.

So the passphrase is **supplied by the operator at host bootstrap**, never generated by the agent.
Bootstrap is already a manual on-host step, so this adds no ceremony — and the key exists in two
places by construction. A test asserts the generator does **not** produce it.

**A scheduler that cannot succeed is worse than none.** DH-022 removed the backup service from the
default Compose path because an unconfigured scheduler restart-loops; auto-enabling it everywhere
would recreate that at fleet scale. Quieting it with `BACKUP_REQUIRE_OFFSITE=false` would be worse
still — local-only archives beside the data they protect, shown as working backups.

So backup is scheduled **only when both a passphrase and an off-site destination exist**. When they
don't, the scheduler is absent and the facility reports having no backup — and the portal shows it
as **"⚠ not protected"** with a banner, because a facility with no backup is one disk failure from
losing patient records, not a neutral "nothing yet".

Scheduled is still never presented as verified: the restore-drill date sits beside the backup state
in the fleet list, and a facility that has never had one says `never`.

### The wiring that was still missing (DH-027G, 2026-08-21)

Everything above was true of `BackupSettings`/`render_env` in isolation, but `ComposeExecutor`
never called either with the operator's configuration: `_provision()` called `render_env(...)`
with no `backup=` argument at all, so a real provisioning run wrote an env file with no backup
section regardless of what an operator had configured on the host. Closed by:

**The operator's configuration reaches the agent through its own process environment**, read once
at startup — the same place `AGENT_CREDENTIAL` already comes from, because both are supplied at
the same manual bootstrap step (ADR-025):

| Variable | What it does |
|---|---|
| `BACKUP_ENCRYPTION_PASSPHRASE` | Operator-supplied. Never generated here |
| `BACKUP_S3_BUCKET` | Off-site destination. Both this and the passphrase are required before anything is scheduled |
| `BACKUP_S3_ENDPOINT` | Optional — an S3-compatible endpoint (e.g. MinIO), not AWS-specific |
| `BACKUP_S3_ACCESS_KEY_ID` / `BACKUP_S3_SECRET_ACCESS_KEY` | Optional, for the destination above |
| `BACKUP_RETENTION_DAYS` | Optional, default 30 |

`backup_settings_from_env()` in `daher_agent/runner.py` builds a `BackupSettings` from these (or
`None` if both the passphrase and bucket are absent) and passes it into `ComposeExecutor`, which
now actually forwards it to `render_env` during `PROVISION`.

**BACKUP_NOW now answers honestly, the same way `STATUS` does (DH-027D).** It used to run
`docker compose --profile backup run --rm backup` unconditionally, which — on an unconfigured host
— would just fail loudly with a confusing error from inside `backup.sh`'s own `${VAR:?}` guards.
It now checks first: unconfigured reports `not_configured` (a *correct* answer, not a failure —
`ExecutionResult.succeeded=True`, the same convention `_status` uses for a degraded-but-trustworthy
check) without ever attempting the command; configured runs it for real and reports `success` or
`failed` from the exit code. `/facilities/{id}/status`'s `backup_state` used to be hard-coded to
`never_run` regardless of anything an agent ever reported — it now reflects the last such report,
persisted the same durable way `STATUS`'s observation is (`SqliteBackupStore`, latest-only).

**The control plane still never learns the passphrase or the bucket name** — only the three-value
outcome (`not_configured` / `success` / `failed`) and a short operator-language reason, exactly
like `InfrastructureReport` carries container states rather than raw Docker output.

⚠️ **What this does not do — closed by DH-027H below.** No API endpoint yet queues `STATUS` or
`BACKUP_NOW` on demand; both currently require queuing a job directly (see the demo harness's
`/demo/enqueue-raw`, or `tests/test_backup_status.py`).

### The nightly scheduler was still invisible to the control plane (DH-027H, 2026-08-21)

`backup/scheduler.sh` runs entirely inside the backup container, independent of the job queue,
calling `daher-backup` nightly at `BACKUP_HOUR_UTC`. Before this ticket the control plane had no
way to see the result: `backup_state` only ever moved on an explicitly-issued `BACKUP_NOW`, so a
facility could show a green manual test from last week while the real nightly backup had silently
failed every night since.

**`STATUS` now observes it too, rather than adding a second job verb.** `STATUS` is already the
fleet's one read-only observation seam, running alongside a mutating job (bug 18's status slot).
`_status` now composes the existing infrastructure check with a new `_observe_backup`, which —
for a configured host — runs exactly one more fixed, read-only command:

```
docker compose --profile backup exec -T backup cat /backup/last-run.status
```

The service name and path are hard-coded constants in `executor.py`, never derived from a job or
caller input — the same guarantee `compose_file` already has. This automates the exact manual
check `backup/README.md` already documented; it adds no new capability, no new verb, and no
change to the closed job vocabulary (`jobs.py`/`validation.py`/`queue.py` are untouched).

**Parsing is strict and fail-closed** (`daher_agent/backup_observation.py`). `last-run.status` is
`<STATE> <TIMESTAMP>` on one line; anything else — absent, empty, malformed, an unrecognised
state word, an unparseable or future-dated (beyond 60s clock-skew tolerance, reusing
`JobValidator`'s existing constant) timestamp — becomes `NEVER_RUN`, never `SUCCESS`.
`BackupState` gained no new value for this: an absent file, a failed `exec`, and malformed
content are all `NEVER_RUN`, because all three mean the same thing to an operator — no confirmed
evidence — and the distinction lives in `reason`, not the type. **The reported timestamp is the
backup's own run time, parsed from the marker — never the moment the agent happened to check.**

`backup/backup.sh`'s status write is now atomic (temp file + `mv`, not truncate-in-place) — small
and in scope, because the control plane reads this file now, not just a human running `cat`.

**Out-of-order evidence cannot regress what is displayed.** A manual `BACKUP_NOW` and a scheduled
`STATUS` observation can now both report, in either order, regardless of which backup actually
happened more recently. `SqliteBackupStore`/`InMemoryBackupStore` only accept a report whose
`checked_at` is at least as new as what they already hold — the same conditional-update pattern
`SqliteHostStore.claim`/`release` use to avoid a read-then-write race, applied here to a
document-then-write race. Real newer evidence, good or bad, always wins.

For `BACKUP_NOW`, missing/malformed evidence is still `FAILED` (unchanged — that verb's entire
purpose is producing this evidence). For `STATUS`, backup evidence is additive: a mixed-version
agent that has not been upgraded to send it does not make backup look `FAILED` for a check that
simply didn't carry it — the report is skipped, not fabricated.

## Credential rotation — what it will not do (ADR-024)

"Rotate this facility's credentials" is not one operation, and the obvious implementation would
**break the facility it was meant to protect**. Building it surfaced this:

| Credential | Rewriting it in the env file | |
|---|---|---|
| Database passwords | The databases were provisioned once by `initdb`. The stack now presents a new password to a database holding the old one — **every service loses its database** | ❌ refused |
| `OMRS_ADMIN_PASSWORD` | Applied on first boot only (DH-019). Changes nothing, while the file claims otherwise | ❌ refused |
| `BACKUP_ENCRYPTION_PASSPHRASE` | Decrypts existing archives. Changing it without escrow makes **every prior backup unrecoverable** — including the one needed in the disaster that prompted the rotation | ❌ refused |
| TLS keystore/truststore | DH-021's generator finds a store it cannot open and regenerates. Self-healing | ✅ rotated |
| Keycloak client secrets | Reapplied by the idempotent reconciliation job | ✅ rotated |

Each refusal names what is *actually* required — `ALTER USER` inside the engine, a password
change through OpenMRS, escrow of the old passphrase — so it points somewhere instead of just
saying no. The endpoint's response carries `not_rotated` for the same reason: a response listing
only what it rotated would imply the whole set was replaced.

Control-verified: removing the guard makes 13 tests fail, including one asserting a rotation left
`OMRS_DB_PASSWORD` untouched.

## The portal

Server-rendered FastAPI + Jinja2 (ADR-020). **The browser never talks to the provisioning API** —
it talks to the portal, which calls the API server-side with the scoped credential. A test asserts
no rendered page contains the credential, `Authorization`, or `Bearer`, and it is control-verified:
injecting a leak into the layout makes it fail.

**Auth libraries, and why** (the build prompt asked to flag this):

| Concern | Library | Why |
|---|---|---|
| Password hashing | `argon2-cffi` | Argon2id is the current recommendation; the library owns salt, parameters and constant-time verify |
| Sessions | Starlette `SessionMiddleware` (signs with `itsdangerous`) | The other place hand-rolling goes wrong |

⚠️ **No MFA.** A single password protects a control plane that can create and stop clinical
deployments. That is the portal's weakest link, stated here rather than discovered later. The
upgrade is an external IdP via Authlib, which also removes password storage entirely — deliberately
not done now because it adds a hard external dependency to a login path that must work when things
are broken.

Hardening in place: session cookie `https_only` + `SameSite=strict` + HttpOnly; session cleared and
reissued on login so a fixated pre-login cookie is useless; CSRF token on **every** POST including
login; login rate-limited by source; `no-store` on fleet pages so a stale "backup succeeded" can
never be shown as current; and CSP with `script-src 'none'` and `frame-ancestors 'none'`.

The login error never says which half was wrong — "no such user" tells an attacker which half to
keep guessing.

**It shows nothing from a patient record**, and a test enforces that on template *expressions*
rather than prose. An earlier version matched the whole file and failed on the page's own sentence
"never anything from a patient record" — which would have pushed someone to delete an honest
reassurance to satisfy a blunt guard.

## Persistence

SQLite, one file. Facilities, host registrations, agent enrolments, API credentials and the job
queue all survive a restart — previously they were plain dicts, so restarting the control plane
lost the entire fleet and recovery meant re-enrolling every VM by hand.

SQLite rather than a database server because this is one control plane on one host with small
data, and ACID with no daemon to run, patch or back up separately is the right trade. The store
classes are the seam if it ever outgrows that.

⚠️ **No clinical credential is stored here.** Facility secrets are generated on the facility host
and written to its own env file (ADR-022); this database holds identities and **hashes**. A test
asserts no column is named for a password or secret, and another asserts an issued key's secret
is not recoverable from the table.

Two concurrency details worth knowing, both written as conditional updates so a race loses rather
than corrupts:

- **Claiming a host** updates `WHERE facility_id IS NULL`, so two simultaneous creates cannot both
  attach a facility to the same host.
- **Redeeming an enrolment token** updates `WHERE redeemed_at IS NULL`, so single-use survives two
  requests arriving together — not just two arriving in sequence.

`synchronous=FULL` because losing an acknowledged enrolment to a power cut would mean walking back
to that VM.

## Where this service should run (ADR-023)

**Not on the public internet.** The API is firewalled to the portal server's address; the portal
runs on its own domain, separate from any marketing site and from `BASE_DOMAIN`'s facility
subdomains; and **the browser never talks to this API** — it talks to the portal, which calls
here server-side. A scoped provisioning credential in browser JavaScript is one XSS away from
being stolen.

The firewall is the primary control. `network.py` is the **second** one, because firewalls get
edited — usually by someone opening a port to debug and not closing it. Set
`allowed_source_cidrs` and the API refuses anything else and records the attempt.

⚠️ **The check uses the real peer address and never `X-Forwarded-For`.** Any client can set that
header, so believing it would hand over the bypass for free. A test asserts a request carrying
`X-Forwarded-For`, `X-Real-IP` and `Forwarded` all pointing at an allowed address is still
refused. If a reverse proxy is ever put in front, update the allowlist to the proxy deliberately
— do not start trusting a header.

An unparseable or missing peer address is **refused** while enforcing, not allowed. Otherwise any
transport that obscures the peer becomes a bypass.

## Authentication and scopes

The portal authenticates with a scoped credential (`dhp_<id>_<secret>`). The id travels in the
clear so the server compares against exactly one candidate; the secret is **stored hashed**, so a
compromised API host does not yield a usable credential.

SHA-256 rather than bcrypt/argon2 is deliberate: slow hashes exist to frustrate brute force against
*low-entropy human passwords*. These keys carry 256 bits from `secrets.token_urlsafe`, so there is
nothing to brute-force and a slow hash would only make every request slower.

Comparison is constant-time, and **an unknown key id still performs a comparison against a decoy
hash** — returning early would reveal which ids exist purely from how fast the request failed.
Errors name the key id, never the presented secret, because errors reach logs.

| Scope | Grants |
|---|---|
| `facility:create` | Create a facility |
| `facility:read` | List and read status |
| `credentials:rotate` | Rotate a facility's secrets |
| `facility:decommission` | Soft-stop a facility's stack |

**The portal does not hold `facility:decommission` by default.** Stopping a facility is granted
explicitly rather than inherited from a default set. No scope grants shell, Docker socket, or
database access — a test asserts the vocabulary itself contains no such capability.

## Rate limiting

Per-caller token bucket: steady refill with a burst allowance. Creating five facilities in quick
succession is legitimate; five hundred is not, and a fixed window would either permit a damaging
burst at the boundary or refuse ordinary bursty use. The limiter raises rather than returning a
boolean — a limiter whose result can be ignored at the call site is not a limiter.

⚠️ **In-memory and per-process.** It resets when the API restarts, so an attacker who can
crash-loop the service can reset their budget; and it does not coordinate across workers, so N
processes multiply the effective limit by N. Acceptable for a single-process deployment, not
after it scales out. The interface is narrow so a shared backend swaps in without rippling.

## The audit log

Every provisioning action is recorded **by the code that performed it**, including failures. What
the portal claims happened is not evidence; the API's own record is.

Entries are hash-chained: each carries the hash of the previous one, so editing, deleting,
reordering, or truncating the file breaks the chain and `verify_chain()` reports the first damaged
position. All four of those tampering shapes have their own test — a hash chain nobody has tried to
break is a claim, not a property.

⚠️ **Be precise about what this buys.** A hash chain makes tampering **detectable**, not
**impossible**. An attacker with write access can rewrite the whole file and recompute every hash.
Your actual requirement — a log that survives this host being rebuilt — needs an **external
append-only destination, which does not exist yet** and is marked incomplete rather than pretended.
What the chain gives today is that silent selective edits are caught, and it makes the eventual
external destination a shipper rather than a redesign.

⚠️ **Verifying zero entries is not a pass.** `verify_chain()` returns the number of entries
checked, so callers must check the count. A clean return on an empty log after actions were
performed is itself the finding.

Secrets never enter the log. Entries reference credentials by `credential_set_id`, the model is
closed, and a test asserts no field is named for a password, secret, token, or passphrase.

## Secrets

`generate_facility_secrets()` produces all 16 credentials the clinical stack requires. Every
facility gets its own; none is ever reused.

The generators encode constraints learned by **running** the stack, not by reading documentation:

| Constraint | Why it exists |
|---|---|
| OpenELIS admin password uses only letters, digits and `* $ # !` | A general base64 generator produces values OpenELIS silently rejects |
| OpenMRS password needs upper + lower + digit and must not contain `admin` | OpenMRS enforces this and refuses the account otherwise |
| `FACILITY_ID` is lowercase alphanumeric and hyphens | It becomes a Compose project name |
| Keystore passwords are never empty | An empty value used to select upstream's published `kspass` through a `:-` fallback (DH-021) |

⚠️ **`FacilitySecrets` must never be logged, serialised, or returned.** `__repr__` and `__str__`
are overridden to redact, because the default dataclass repr would print every secret into any
stack trace that touched it. That redaction is tested by formatting the object into a log line and
asserting no value appears — not by trusting the override.

Secrets are written once into a facility's env file and referenced by id afterwards. API responses
carry a **reference**, never a value: returning secrets inline would put them in every access log,
proxy log, and browser history between here and the operator.

## Running the tests

```sh
docker build --target test -t daher-provisioning-test provisioning
docker run --rm daher-provisioning-test pytest -q
docker run --rm -v "$PWD/provisioning:/p:ro" -w /p -e RUFF_CACHE_DIR=/tmp/ruff \
  daher-provisioning-test sh -c 'ruff check src tests && ruff format --check src tests'
```

## Related

- **ADR-020** — topology, DNS strategy, implementation stack
- **ADR-021** — trust boundary and the compromise blast-radius walkthrough
- **DH-026 / DH-027** in [`PROJECT_STATE.md`](../PROJECT_STATE.md)
- [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §16
