# Current State — Daher Health

**Assessed:** 2026-08-17 · **Target:** pilot-ready by 2026-09-01
**Method:** inspection of the actual repository and running environment, not the plan.

---

## 1. Honest summary

**Build steps 1 and 3 now run, and running them produced multiple findings the syntax check could not.**
Step 1 (MariaDB + OpenMRS O3) is verified through REST and FHIR **and by Liban himself in the SPA**.
Step 3 (PostgreSQL + OpenELIS) is verified: three co-tenant databases, 375 base tables in the
fully migrated OpenELIS `clinlims` schema, the webapp on HTTPS, and a FHIR server answering over
mutual TLS. Every image is digest-pinned.

The **DH-008 fail-closed SSO plumbing also runs and is runtime-verified**: the existing Keycloak
realm reconciles idempotently to OpenMRS OIDC and OpenELIS SAML; official OpenMRS OAuth2 Login
1.5.0 is installed in the persistent volume; and disabled mode preserves local authentication
without persisting its client secret. SSO is not active on real staff, and the real-staff one-login
acceptance demonstration is not complete — but a genuine synthetic-identity one-login round trip
**has** now been performed and proven end to end (2026-08-26, ADR-017F closure), on a disposable
environment, not the real facility. The native role/provider **types** are decided (ADR-017); the
`clinician` type was narrowed 2026-08-26 (ADR-017F: `Provider` + `Daher Clinician Admission`, not
`Organizational: Nurse`). What is still missing is which staff hold them, which is a fact from the
birth centre rather than a design choice (BLOCKER-5).

**DH-003 now runs and is empty-volume verified:** French backend properties, the `Maternité`
admission ward, both decided bed types, independent demo-patient/content controls, and a strictly
additive bed seed. Real bed counts remain zero placeholders pending facility input; the current
throwaway stack retains 3 delivery + 4 postpartum beds created only for lifecycle verification.

**DH-010 now runs as well:** the adapter's transport-neutral safety skeleton migrated its real
PostgreSQL database and is healthy with a durable unique ledger, dead-letter table, private audit
volume, transient-only retry policy, and dependency-aware readiness. It does not move FHIR data yet.
The unique claim protects concurrent delivery, but it does not by itself resolve a crash after an
outbound write and before `processing` becomes `completed`; DH-025 now makes that reconciliation
an explicit P0 gate before DH-011 transport activation.

**DH-025's recovery core now runs and is runtime-verified.** The adapter holds a PostgreSQL
advisory fence for its full lifetime; a second process cannot enter recovery or future transport.
Its stable opaque correlation and typed coordinator distinguish an existing target write, proven
absence, and ambiguity; only proven absence may retry. Twenty-seven tests and a rollback-only live
database probe cover concurrent claims and crashes on both sides of the write. The remaining gate
is concrete: DH-011 must attach/query this correlation on the selected OpenELIS resource. No target
resource or clinical message exists yet, so that binding is unverified rather than inferred.

**DH-011E's OpenMRS order poller now runs in tests and is deliberately not activated (2026-08-17).**
The accepted watermark design is implemented: a durable position bootstrapped once from the server's
own clock (ADR-015), a window bounded at both ends, deduplication by `(type, id, versionId)`, a
completeness check against the bundle's `total`, and **client-side ordering** — because probing found
that OpenMRS `fhir2` 4.2.0 accepts `_sort` and silently ignores it. 94 tests and five deliberate
controls. Its output is a transport-neutral `SourceOrderEvent` that names no target, and **no loop
runs it**: BLOCKER-6 has not decided how an order reaches OpenELIS, so there is nothing to deliver
to and no order has moved.

**DH-011B's full spike settled the ingestion question and found the correlation cannot bind at all.**
Authorized by Liban and run with synthetic data only. Confirmed: OpenELIS's business FHIR providers
write its **own domain tables** — synthetic Patient and Practitioner creates produced real
`clinlims.patient` and `clinlims.provider` rows — and `ServiceRequestProvider` calls the same
`SamplePatientEntryService` the human workflow uses. So Option F is not a façade, and it is the
strongest of the five candidates.

Blocked on two things an engineer must not decide: a **numeric facility site code** (accessions are
`%013d` under `SITEYEARNUM`, the prefix is alphabetic `DEV01`, so every order is refused), and the
**adapter correlation, which is discarded on ingestion** — OpenELIS replaces a client-supplied
identifier with its own. ✅ **Corrected 2026-08-17:** exact identifier *search* does work (a real
identifier returns 1; a wrong system with the right value returns 0), so DH-025's contract **is**
expressible and only persistence of the incoming identifier is missing. The first write-up said the
contract was inexpressible, which was wrong in the expensive direction — it would have justified a
larger OpenELIS patch than the evidence supports. Bug 19 adds a third constraint:
`201 Created` is returned even when the persist fails, so read-back is the only trustworthy check.
No `sample`, `analysis` or `electronic_order` was ever created, so laboratory workflow state and
cancellation remain unverified.

**DH-027B closed an orphan in facility creation (2026-08-17):** the host was claimed before the facility record was inserted, so a failed insert left that host permanently marked occupied by a facility that never existed. Compensation is now conditional on the exact facility id — it cannot evict a live facility — and the audit log records `FAILURE` with whether the rollback actually happened. 17 failure-injection tests across both the in-memory and SQLite backends.

**DH-027D made fleet health observed rather than assumed (2026-08-20).** The control plane can now
truthfully answer "is this facility's stack healthy *right now*", separately from "what lifecycle
phase is it in". Previously `/status` fabricated its answer — zero counts with a **fresh
`checked_at`**, implying a check that had never happened. A real check now runs two read-only
compose commands through the existing agent, derives health from parsed service states rather than
the command's exit code, and is persisted durably. A STATUS report carrying no evidence, or evidence
that fails validation, is stored as **UNKNOWN, never HEALTHY** — health cannot be asserted by
omission. Run against the live stack it first reported `degraded, 10/11, caddy unhealthy` — a real fault,
surfaced rather than hidden. **Bug 20 was then diagnosed and fixed (2026-08-20)**, and the same parser
now reports `healthy, 11/11`. ⚠️ No scheduler exists: an observation is only as fresh
as the last request, and the portal shows its age for exactly that reason.

**Bug 18 fixed — the agent now polls while a job executes (2026-08-20).** `execute` used to run
inline inside the poll loop, so a `provision` blocked polling for up to 5400s and anything queued
in that window was refused as stale once the loop resumed (900s replay window, ADR-022). `execute`
now runs on a background thread; `status` (read-only) always runs, including alongside an in-flight
mutating job, while every mutating verb shares one slot so at most one runs at a time — a second
one claimed mid-flight is refused immediately rather than racing two `docker compose` invocations
against the same project. `JobValidator` still runs synchronously before dispatch; the staleness
window itself is unchanged. Fixing this exposed a real gap in DH-027C's lifecycle table: concurrent
polling makes it possible for a duplicate `PROVISION` to be claimed and refused while the real
attempt is still running, and the table previously treated any non-`succeeded` report as `DEGRADED`
— which would have mislabeled a healthy facility, permanently, since that row only fires from
`PROVISIONING`. `next_facility_state` now only degrades `PROVISION` on a genuine `failed` report;
`refused` is a no-op, matching every other row. 11 new tests, 464 provisioning tests total (453
before), Ruff clean. Control-tested, three deliberate breaks on a throwaway copy: disabling the
busy-refusal check fails 3; restoring the old binary lifecycle row fails 2; reverting `poll_once`
to call `execute` inline again — reproducing bug 18 exactly — fails 6 of 7 (the one unaffected
test doesn't depend on concurrency). Not exercised against a real ~50-minute boot — proven at the poller/queue
level, not against real `docker compose` timing.

**DH-027G wired backup configuration through to the real path (2026-08-21).**
`BackupSettings`/`render_env` (ADR-025) were fully built, but `ComposeExecutor._provision()` never
passed `backup=` to `render_env` — a real provisioning run wrote no backup section regardless of
host configuration. Closed: the operator's passphrase and off-site destination now reach the agent
through its own process environment at bootstrap (same manual step as `AGENT_CREDENTIAL`), and
`ComposeExecutor` forwards them. `BACKUP_NOW` now checks first and answers honestly — `not_configured`
without ever attempting the command on an unconfigured host, `success`/`failed` from a real attempt
otherwise — the same convention DH-027D's `STATUS` already uses. `/facilities/{id}/status`'s
`backup_state` was **hard-coded** to `never_run`; it now reflects the latest such report, persisted
durably (`SqliteBackupStore`). The control plane still never receives the passphrase or bucket name
— only a three-value outcome and a reason. 44 new tests, 508 provisioning tests total (464 before),
Ruff clean. Control-tested, four deliberate breaks: disabling the not-configured refusal fails 5;
malformed evidence reported as success instead of fail-closed failure fails 2; reverting the
`_provision()` wiring (the exact original defect) fails 1; reverting the portal's risk banner to not
recognise the new state fails 1. ⚠️ Still not covered: the nightly scheduler inside the backup
container runs independent of the job queue, so `backup_state` reflects the last *requested* check,
not necessarily last night's real backup, until something reads `last-run.status` into a report —
deliberately not built in this ticket. Not exercised against a real S3-compatible target or a real
`docker compose run --rm backup` — DH-014 already owns that verification boundary (local path
verified, off-site unexercised) and this ticket proves the wiring reaches it, not backup mechanics.

**DH-027H closed that exact gap: STATUS now observes the nightly scheduler's own evidence
(2026-08-21).** `backup/scheduler.sh` runs inside the backup container, independent of the job
queue — before this, the control plane only ever learned about backup from an explicitly-issued
`BACKUP_NOW`, so a facility could show a green manual test from last week while the real nightly
backup had silently failed every night since. `STATUS` now also reads
`${BACKUP_OUTPUT_DIR:-/backup}/last-run.status` — the exact command `backup/README.md` already
documented for a manual check — via one more fixed, read-only `docker compose exec` (no new job
verb; unconfigured hosts skip it entirely, same as `BACKUP_NOW`'s existing check). Parsing is
strict and fail-closed (`daher_agent/backup_observation.py`): anything malformed, absent, or
future-dated beyond a 60s tolerance becomes `NEVER_RUN`, never `SUCCESS`. The reported timestamp
is the backup's own run time parsed from the marker, not when the agent happened to check.
`backup.sh`'s status write is now atomic (temp file + rename) rather than truncate-in-place, since
the control plane reads it now, not just a human running `cat` by hand. Because two independent
paths (manual and scheduled) can now report evidence out of order, `SqliteBackupStore`/
`InMemoryBackupStore` reject a report older than what they already hold — real newer evidence,
good or bad, always wins; a stale replay cannot regress the fleet's displayed state. 41 new
tests, 549 provisioning tests total (508 before), Ruff clean. Control-tested, four deliberate
breaks: the parser always returning SUCCESS fails 2; removing the out-of-order guard from both
stores fails 5; trusting the exec command's exit code over the marker's actual content fails 5;
a successful observation wrongly setting the restore-drill date fails 2. ⚠️ Not exercised against
a real nightly run — proven against the documented command and file format with synthetic marker
text. DH-014's off-site boundary is unchanged.

**DH-027C gave facilities a real lifecycle (2026-08-17):** nothing ever set `RUNNING` or
`DEGRADED`, so every facility stayed `PROVISIONING` forever. One explicit transition table now
advances state, driven only by an agent report about work it actually ran — never by a job being
queued. `RUNNING` means "the agent reported `docker compose up` returned 0", which given this stack's
health-gated dependency graph is more than "containers created" but is still one claim at one moment,
not continuous health. That remains DH-027D.

**DH-027E made decommission real (2026-08-17):** the endpoint set `DECOMMISSIONED` on request and
queued nothing, so the fleet reported a facility as stopped while its stack was still serving. There
is now a `DECOMMISSIONING` state, a STOP job queued to the host that actually holds the claim, and the
terminal state is reached only on that agent's successful report. The host claim is deliberately
**not** released — the patient database is still on that host.

**DH-027F made facility env writes atomic (2026-08-17):** the writer used `O_TRUNC`, so a death
mid-write left a torn env file — a live facility's credentials, on the rotation path — and there was
no `fsync`, so a power cut could leave a partial file after a successful-looking write. Now
write-temp-then-rename with contents fsynced before the rename. ⚠️ Rotation *rollback* is still open:
the write is atomic, so the file is never torn, but a failed `up` afterwards leaves the new file in
place. Restoring the old one is a credential-custody question, not a code detail.

**CI now exists (DH-027M, 2026-08-17) and currently gates 678 tests.** Until this landed, every
verification claim in `PROJECT_STATE.md` depended on a human remembering to run something.
`.github/workflows/ci.yml` runs the adapter (94), provisioning (549), seed (16) and form-safety
(19) suites plus Ruff, compose
interpolation on both profiles, shell/JSON/XML/CSV syntax, image pinning, a tracked-secret scan, the
`.env`-untracked check, and relative-link resolution. Four guards were control-tested by breaking
them, and running the checks found **three** defects in the checks themselves — the third being that
the secret scan flagged the workflow's own dummy values, which is how the first GitHub run failed.
**All five jobs are now green on GitHub in 23 seconds** (run `32031078854`), so this is demonstrated
rather than written. The ~50-minute clinical cold boot is deliberately excluded.

**DH-024's fresh-deployment audit closed three false-green paths.** The OpenMRS demo-patient
property had two competing writers and injected a second 50-patient batch into this throwaway
volume despite the local switch returning to `off`; the derived image now removes only upstream's
competing trigger. Direct first-boot `on/on` also reset the trigger with zero patients, so a
read-only metadata preflight now enforces the demonstrated two-stage opt-in path. PostgreSQL
readiness no longer goes green against its temporary init server.
OpenELIS's documented `DEFAULT_PW` is ignored, so a verified post-bootstrap bcrypt rotation now
gates the frontend. Both current and empty-volume OpenELIS login tests accept the configured secret
and refuse `adminADMIN!`.

**DH-008's 2026-08-13 quality pass corrected two false-green paths before documentation was
updated.** Keycloak initially tried to import partial mapper JSON as complete realms; the fragments
now live outside the import directory. The OpenMRS OMOD initially existed only in the image
distribution layer and therefore did not reach an existing data volume; the runtime wrapper now
installs the exact release atomically. Existing-realm reconciliation is idempotent, fresh realm
import passed in an isolated database, all service endpoint/authentication checks passed, and the
final stack has zero restarts/OOM kills. SSO remains intentionally disabled pending BLOCKER-5.

**Post-commit quality audit 2026-08-13:** all local image paths rebuilt, both Python suites passed
again (adapter 14, maternity 5), all Compose/static/document checks passed, and the normal stack
returned to ten running services plus six successful setup jobs. Authentication, both FHIR
metadata paths, OIDC/SAML metadata, adapter service credentials, all nine PostgreSQL tenancy
cases, aggregate persistence, UTC+3, host-port confinement, Keycloak fresh import, repeated
reconciliation, and persisted-realm restart all passed. The log/code review expanded BLOCKER-4 to
both shipped Horiba CBC imports and exposed DH-025's in-flight crash ambiguity. No clinical
message is transported today, so the DH-025 defect has not affected a patient or test record.

**Post-decision quality audit 2026-08-13.** After ADR-015 and ADR-016 landed, the full check ran
again: both Compose profiles, all tracked shell/JSON/XML syntax, Git integrity/whitespace, and the
untracked-`.env` check passed; every image is digest-pinned once the locally built `daher-health/*`
images are resolved to their pinned bases. Adapter `pip check`/Ruff/**27 tests** and maternity
**5 tests** passed. Ten services run with six setup jobs at exit 0 and **zero restarts or OOM kills
across sixteen containers**. Configured OpenMRS and OpenELIS logins pass while `Admin123` and
`adminADMIN!` are refused; both FHIR metadata paths answer, OpenELIS FHIR only over mutual TLS and
correctly rejecting a missing client certificate; all nine PostgreSQL tenancy cases hold; every
container is `EAT+0300`; only `127.0.0.1:8080` is published; the adapter's ledger and dead-letter
tables remain empty; and OAuth 2.0 Login 1.5.0 is registered with `started=false`. SSO stays
fail-closed and no clinical message has moved. Probe-method notes and one corrected false alarm are
in `PROJECT_STATE.md`.

**What running it cost, and what it exposed** (details in §3a):

1. The gateway could not reach the backend at all — it hard-codes upstream hostnames.
2. The backend healthcheck reported **healthy 8 seconds in**, while the application was unusable.
3. `OMRS_CONFIG_ADD_DEMO_DATA` is inert in this image.
4. **The distro created 50 fictional demo patients** — the hazard `.env.example` warned about,
   reached through a mechanism nobody had identified. See BLOCKER-2 in `PROJECT_STATE.md`.
5. A cold boot takes **≈49 minutes**, nearly all of it importing 23 OCL concept packages.

Step 3 added four more (DH-005's ticket entry): `certs` cannot be dropped as the architecture
claimed; the webapp needs a Docker secret or its context dies while Tomcat still reports success;
the FHIR container ignores `JAVA_OPTS`; and its FHIR endpoint requires **mutual TLS**.

**The honest boundary:** substantial buildable work remains. OpenELIS local-admin authentication is
form-verified, but **no human has ever driven its browser workflow** — Caddy now gives it a host
route (DH-016), so that check is possible and simply has not been done. SSO activation and one-login
proof, maternity clinical forms, adapter transport, notifications, off-site backup and a re-run
restore drill, and publicly trusted TLS all remain incomplete. The maternity ward/bed foundation
itself is complete. Human decisions are collected under **NEEDS LIBAN** in `PROJECT_STATE.md`.

**BLOCKER-10 / ADR-030's "Terminer l'admission" (UI-015F, 2026-08-28): closed — code complete and
live-proven, not yet committed, reviewed, or deployed to the real facility.** A Daher-owned OpenMRS
module and its React frontend (all eight required UI states — ACTIVE/CONFIRMATION/SUBMITTING/ENDED/
ALREADY_ENDED/AMBIGUOUS/UNAVAILABLE/ERROR — individually tested) both run and are proven against the
disposable stack: live rollback proof (an external DB trigger, not a code change), live
true-concurrency proof (a real race behind a thread barrier, exactly one audit row written), three
live success cases with exact recorded DB values, exhaustive authorization proof (privileges
*enumerated*, not name-checked — exactly 9/3, zero roster change), a real CSRF-scoping control
(a sibling module's own servlet path still denied), and a payload-injection control (attacker-supplied
fields silently ignored). 236 UI tests and 18 module JUnit tests passing, full lint/typecheck/build/
secret-scan/`git diff --check`/`docker compose config` clean, all re-confirmed against the same
from-source Docker rebuild (module + the CSRFGuard WAR patch, both baked in at build time) as the
prior pass, since no backend Java changed this pass. **This is code-complete, not facility-live** —
nothing here has been committed, pushed, reviewed by Liban, or deployed to the real Daher birth
centre; no real patient or staff record was ever touched. One disclosed, deliberately-deferred item
remains: a genuinely-fresh-database boot was attempted with a real controlled pair (this image vs. a
pre-ADR-030 image, both against independently fresh databases) and found both hitting the identical
`stockmanagement` Liquibase changeset at the identical duration — confirming the slow phase is a
shared-platform characteristic, not this module's — so the specific "does the audit type provision
exactly once from true scratch" check is carried as `DH-017` evidence debt (ADR-029 precedent), not
treated as a blocker. Full evidence in `PROJECT_STATE.md`'s BLOCKER-10 (now ✅ CLOSED) and
`DECISIONS.md`'s ADR-030 (now `ACCEPTED / IMPLEMENTED`).

---

## 2. What is running

| Service | State |
|---|---|
| MariaDB | **running, healthy, verified** — 10.11.18, digest-pinned |
| OpenMRS backend | **running** — boots, migrates, serves REST + FHIR R4; configured admin password works; official OAuth2 Login 1.5.0 is registered and deliberately stopped by `SSO_ENABLED=false` |
| OpenMRS frontend | **running** — container healthy; Liban registered a test patient in the SPA |
| OpenMRS gateway | **running** — routes to backend and frontend after the alias fix |
| OpenMRS demo preflight | **one-shot job exited 0** — default patients-off path is inert; opt-in mode requires initialized `OpenMRS ID` metadata before backend startup |
| OpenMRS maternity config | **one-shot job exited 0** — French backend metadata, admission ward, both bed types, additive-only bed lifecycle verified; committed counts are `0/0` placeholders |
| PostgreSQL | **running, healthy, verified** — 14.4 (OpenELIS's image), 3 co-tenant databases; DH-020 denies all cross-tenant connections |
| OpenELIS webapp | **running, healthy, with configuration warnings** — serves HTTPS 8443; configured admin form login passes and `adminADMIN!` is refused; SAML properties are present but disabled; no browser workflow performed; upstream Horiba Micros60 and Pentra60 CBC imports abort on a duplicate target description (BLOCKER-4) |
| OpenELIS admin password | **one-shot job exited 0** — runs after genuine webapp readiness, updates exactly one admin bcrypt hash, verifies it, and gates the frontend |
| OpenELIS FHIR | **running** — `GET /fhir/metadata` → 200 over mutual TLS, refused without a client certificate. HAPI 6.6.0, FHIR 4.0.1. Now presents DH-021's locally generated certificate (valid to 2028-11-16), not upstream's public and already-expired one |
| OpenELIS frontend | **running; internal HTTP verified** — no browser login or workflow exercised; it has no host route until Caddy (DH-016) |
| Keycloak | **running, healthy, realm + DH-008 clients verified** — OpenMRS OIDC, OpenELIS SAML; existing-realm reconciliation passed twice; pilot session lifetimes approved (ADR-013) |
| Keycloak SSO config | **one-shot job exited 0** — non-deleting client/mapper reconciliation; idempotency exercised twice |
| Adapter | **running, healthy, DH-010 + DH-025 + DH-011E built and verified** — ledger/dead-letter/retry/audit, process-lifetime recovery fence, fail-closed crash coordinator, and now the OpenMRS order poller with its durable watermark (94 tests, migration `0002_watermarks` applied live). ⚠️ **The poller is not activated**: no compose service or `app.py` path starts it, and there is no delivery implementation because BLOCKER-6 is undecided. No order has moved |
| WhatsApp notifier | not written |
| Backup | **not running in the current stack** — deliberately behind Compose's `backup` profile until approved production settings exist. The local path is verified end-to-end, and **a restore has now actually been performed** (DH-015, `backup/restore-drill.md`): archive decrypted, all seven manifest hashes re-verified, restored into an isolated scratch stack, and patient `10001MJ` read back through the OpenMRS REST API. Off-site upload remains unverified; the drill archive and passphrase were destroyed afterwards. Two acceptance legs (a lab result, an SSO login) remain unexercised, so DH-015 is **performed, not done** |
| Caddy | **running, healthy — DH-016 built and locally verified** — subdomain routing for `emr`/`lab`/`id`, security headers, and a TLS-**verified** hop to the OpenELIS API using DH-021's exported CA (control-tested: an unrelated CA yields 502 `unknown authority`). Uses Caddy's internal CA under `BASE_DOMAIN=localhost`; public issuance still needs the real domain/DNS |

⚠️ **This local stack contains 100 fictional demo patients** plus two deliberate test patients (one
registered via the API, one by Liban in the SPA). It is a throwaway verification stack and must
never be confused with a clean instance. The second 50-row batch was the evidence that exposed
DH-024's competing-property bug; no patient rows were deleted during the repair.

**Build steps 1 and 3 are done; the DH-003 maternity foundation, Keycloak provider, and DH-008
protocol plumbing are built and verified.** The clinical forms in the larger build step 2 remain
DH-004 and require human sign-off. SSO activation/one-login proof and the remaining steps are not
complete.

---

## 3. What is configured but unverified

| Artefact | State | What "unverified" still means here |
|---|---|---|
| `docker-compose.yml` | **Booted and corrected** | Healthchecks, the `mariadb` CLI name, `${VAR:?}` guards, backend↔db handshake, DH-003 dependency gate, and normal one-command startup are exercised. The OpenMRS healthcheck now also gated a genuine empty-volume DH-003 boot. |
| `.env.example` | Used to boot | `OMRS_ADMIN_PASSWORD` is wired and **verified on an empty volume** (DH-019). OpenELIS's narrower password alphabet is documented; its configured secret is verified on current and empty volumes (DH-024). The old local credentials were rotated. Local `.env` is valid Compose syntax and mode `0600`. |
| Image digests | **Pinned from real pulls as each component landed** | Dates and exact values are recorded in `docs/ARCHITECTURE.md` §12. Reproducible now. |
| O3 SPA in a browser | **Exercised by Liban 2026-08-11** | He registered a patient through the SPA. French is configured and verified in frontend env + backend properties, but the visible browser language was **not** reported either way. |
| OpenELIS UI | **Authentication form exercised; browser workflow not exercised** | Configured admin login succeeds and the published default is refused on current and empty volumes. Its frontend still has no host route until Caddy (DH-016). |
| OpenELIS FHIR *delivery* | **Advertised, not proven** | Its CapabilityStatement offers `Subscription` with `create`, but no subscription has been created and no delivery observed — see ADR-005 Q4. |
| SSO | **Plumbing and synthetic one-login proof runtime-verified; real activation unverified** | OpenMRS OIDC module is registered/stopped in disabled mode; OpenELIS is configured for SAML; Keycloak client reconciliation is idempotent. Role mapping is decided, `clinician` narrowed 2026-08-26 (ADR-017F); a synthetic-identity one-login round trip (disposable environment) is proven. BLOCKER-5's staff-roster gap prevents real activation and a real-staff one-login proof. |
| **DH-011E order poller** | **Unit-verified against injected transport; never run against live OpenMRS** | 94 tests and five controls prove the cycle's safety properties — ordering, completeness, dedupe, halt-before-advance. What is *not* proven: a real poll against the running server, and any delivery at all. The bounded-window and `_sort` behaviours it relies on **were** probed live before the code was written. Deliberately unwired, so "not activated" is a property of the deployment, not a promise. |
| **Migration `0002_watermarks`** | **Applied live and inspected** | Table, columns, and the unique `(direction, resource_type)` index exist in the real adapter database; 0 rows, because nothing polls. |
| `docs/ARCHITECTURE.md` | Current | §3's stale "MariaDB 11.4" row corrected to 10.11 at DH-002; §6 and §16 brought current 2026-08-17. |

## 3a. What running it actually exposed

| # | Finding | Evidence | Status |
|---|---|---|---|
| 1 | Gateway 502 on every request — its nginx template hard-codes `http://backend:8080` and `http://frontend`, which do not resolve to our service names | `backend could not be resolved` in the gateway log; `/etc/nginx/templates/default.conf.template` in the image | **Fixed** — network aliases, chosen over renaming services or hand-editing upstream's config (ADR-010 reasoning) |
| 2 | Backend healthcheck reported `healthy` 8s into the first boot, while Liquibase ran and the gateway 502'd | `docker compose ps` vs. a 502 from the same instant | **Fixed** — explicit `= 200` test on the REST session endpoint. `curl -f` was the root cause: it passes on the 302 that URL returns during boot |
| 3 | `OMRS_CONFIG_ADD_DEMO_DATA` is not read by the image | Absent from the `OMRS_*` names in `startup-init.sh` / `startup.sh` | Documented as inert in compose and `.env.example`; **not** removed |
| 4 | **50 fictional demo patients created on first boot; later a second 50-row batch appeared despite `off`** | Initial DH-002 count 51; DH-024 aggregate count 102 = 100 rows by the demo creator + two deliberate patients. The deployed tree showed upstream `value=50` and our `value=0` competing for the same property | **Fixed at DH-024** — the pinned derived image removes only upstream's trigger block, leaving the mounted `on/off` file as sole writer. An `off` restart held 102→102; no patient rows were deleted |
| 5 | Cold boot ≈ 49 min, dominated by 23 OCL concept-package imports | Deployment start 06:36:49Z → first HTTP 200 at 07:26:08Z | Documented; healthcheck timings derived from it |
| 6 | No OpenMRS admin password variable; image defaults to the published `Admin123`, applied **first boot only** | `OMRS_ADMIN_USER_PASSWORD=${OMRS_ADMIN_USER_PASSWORD:-Admin123}` in `startup-init.sh` | **Fixed and verified on an empty volume — DH-019** |
| 7 | Existing throwaway OpenMRS volume still accepted `Admin123` because its container/DB predated DH-019 | Authenticated session probes against both configured and published passwords | **Fixed in live state 2026-08-13** — rotated through OpenMRS's password API; container recreated with current Compose env; configured secret passes and default is refused |
| 8 | OpenELIS startup repeatedly aborts both `horiba-micros60-cbc.csv` and `horiba-pentra60-cbc.csv` while attempting to give different rows the existing unique description `LYM#(Whole Blood)` | Repeated `test_desc_uk` violations followed by failed test-editor bridging. Live table has zero duplicate descriptions, so each transaction rollback protects uniqueness but both analyzer imports are incomplete | **BLOCKER-4 — Human Decision Required: Yes.** Do not alter laboratory tests until Liban confirms whether either analyzer is in facility scope |
| 9 | OpenELIS PostgreSQL health went green against the temporary `initdb.d` server; hardening then hit connection refused during its deliberate shutdown | Reproduced from empty volumes: `pg_isready` passed, hardening exited 2, final server started afterward | **Fixed and empty-volume verified — DH-024.** Health also requires PID 1 to be `postgres`; hardening exited 0 on the exact from-zero graph |
| 10 | OpenELIS ignores `DEFAULT_PW`; fresh volumes still accepted published `adminADMIN!` | Actual CSRF/form login on both old and empty volumes; configured secret refused before repair | **Fixed and empty-volume verified — DH-024.** Post-webapp one-shot rotates/verifies bcrypt; frontend waits for exit 0; configured login passes and default is refused |

Known upstream oddities observed and deliberately not chased, recorded so they are not later
mistaken for new breakage: `AddressConfigurationLoader` logs an ERROR that the distro's own
`addressConfiguration.xml` "appears invalid" and skips it; and the gateway config carries a
`/grafana → grafana:3000` block for a service this stack does not run (relevant at DH-016 — it
should not be exposed publicly). The pinned OpenMRS base itself runs as UID 1001 while its
root-owned Tomcat `conf` directory lacks `Catalina/localhost`, so Tomcat logs a non-blocking SEVERE
when it cannot create that deployment directory; the same permissions are present in the untouched
upstream image and REST/FHIR still pass. Keycloak startup/restart makes a failed PostgreSQL
`aurora_version()` compatibility probe before continuing healthy against ordinary PostgreSQL.
OpenELIS also logs that the optional Odoo test-product mapping CSV is absent; this stack has no
Odoo integration and v1 billing is out of scope, so no mapping was invented. Treat these as known
non-blocking startup behaviours only while the corresponding live endpoints continue to pass.

---

## 4. Blockers

**Blocker numbering lives in `PROJECT_STATE.md`.** This section summarises; that file is the
source. (Earlier revisions of this file numbered blockers independently, which produced two
different "BLOCKER-2"s. Fixed here — do not renumber in this file again.)

### BLOCKER-1 — Docker daemon unreachable · ✅ RESOLVED 2026-08-10

`libanbritt` was not in the `docker` group. Resolved by `usermod -aG docker $USER` plus a
logout/reboot. Docker has since pulled images and run the full stack, so this is confirmed
resolved by use, not just by inspection.

**Still live as a question for the pilot VM:** `docker` group membership is effectively root on
the host. Decide it deliberately there rather than by habit.

### BLOCKER-2 — The O3 distro creates 50 fictional demo patients · ✅ RESOLVED + HARDENED DH-024

`OMRS_DEMO_PATIENTS` now selects an explicit `off` (default) or `on` Initializer overlay at the
distribution source. The derived image removes upstream's competing trigger, so this overlay is
the sole writer. This is separate from the inert `OMRS_ADD_DEMO_DATA` variable and from
`OMRS_DEMO_CONTENT=on|off`. Patient generation requires one completed content-on/patients-off boot
first because upstream runs its generator before first-boot metadata initialization. A database
preflight rejects unready `patients=on` states. The isolated two-stage path produced exactly 50
patients, one creator, and one reset trigger. Neither control deletes stored data.

### (Not numbered) — ADR-005 · ✅ ACCEPTED 2026-08-12 · adapter skeleton unblocked

Both legs have primary evidence (DH-009a, DH-009b), and Liban approved the custom adapter with a
configurable 15-second OpenMRS order poll, inclusive watermark, and durable ledger. OpenELIS runs a
full HAPI server that advertises `Subscription`, but delivery and corrected-result behavior still
must be demonstrated before trusting the result path. DH-010 built and runtime-verified the safety
foundation, and DH-011E has since built and verified the watermark and poller themselves
(2026-08-17) — **unactivated**, because BLOCKER-6 leaves them with nothing to deliver to. One
implementation addition to the accepted design is recorded in `WATERMARK-DESIGN.md`: the poll window
is bounded at **both** ends, because an open-ended top makes the completeness check fire on healthy
traffic.

### BLOCKER-3 — DH-011 activation and cancellation contract · ✅ CLOSED BY DECISION 2026-08-14

All three legs are decided: **first sync** (ADR-015 — no historical backfill, watermark starts at
deployment time), **requester** (interim 2026-08-14 — a controlled "Pilot Test Clinician" replaces
upstream's sample until the meeting names the real one), and **cancellation** (ADR-018 — revoke if
the laboratory has not accepted or started the test, leave in-progress work for manual handling).

**DH-011 is therefore no longer blocked on decisions**, which makes it the critical path. What
remains is implementation plus two *verification* gates: DH-025's correlation must be attached to
the concrete OpenELIS resource, and ADR-018's "accepted or started" must map to a **verified**
OpenELIS status rather than an assumed one. No poller or clinical order has been activated.

### BLOCKER-4 — OpenELIS Horiba Micros60/Pentra60 analyzer imports · ✅ CLOSED BY SCOPE (ADR-016)

v1 is manual result entry only — no analyzer, HL7, or ASTM interface; DH-023 is deferred to v1.1.
The webapp remains reachable and both upstream analyzer CSVs still fail on the same unique
description, **left exactly as upstream ships them**: each transaction rolls back, the live table
has zero duplicate descriptions, and the endpoints answer. ADR-016 does not authorize disabling,
renaming, repairing, or log-suppressing those tests. No test or mapping has ever been changed.

### BLOCKER-6 — how an order actually enters OpenELIS · 🛑 OPEN 2026-08-16

Found by probing before writing DH-011's transport code. OpenELIS **pulls** orders from a remote
FHIR source it is configured to trust (`remote.source.uri`, currently empty, polled every 120s) — it
does not accept a push into its own FHIR store, which is where every document here implicitly
assumed the adapter would write. Its `electronic_order`, `sample` and `analysis` tables are all at
**0 rows**: the laboratory has never received an order.

There are now **five** candidates (A–D, plus F added 2026-08-16 as ADR-026). They change what the
adapter *is* — one removes the ledger/audit boundary ADR-005 made load-bearing for patient safety,
one turns the adapter into a FHIR server, one adds a second system storing patient data — so this is
a human decision, recorded in `PROJECT_STATE.md` with the evidence.

**Option F is fully spiked as of 2026-08-17 (DH-011B, authorized by Liban) and is the strongest
candidate — but it cannot be completed here.** The business providers write OpenELIS's **own domain
tables**: synthetic Patient and Practitioner creates through the supported FHIR surface produced real
`clinlims.patient` and `clinlims.provider` rows, and `ServiceRequestProvider` calls the same
`SamplePatientEntryService` the human sample-entry workflow uses. It is **not** a FHIR façade over the
generic store, which was the question worth answering.

**Two gates remain, neither an engineering choice.** (1) Every ServiceRequest is refused with
`Generated lab number is invalid`: `acessionFormat = SITEYEARNUM` formats accessions as `%013d` —
thirteen all-numeric digits — while the configured prefix is upstream's alphabetic `DEV01`, so a
**numeric facility site code** is needed (NEEDS LIBAN #7). Supplying an accession does not help; the
value is always server-generated. (2) The adapter correlation **does not survive**: our identifier is
discarded (`external_id` NULL) and search is delegated to `http://fhir.openelis.org:8080`, which this
stack does not serve — so a submitted identifier and a never-submitted control **both return
`total: 0`**. DH-025's 0/1/2+ contract is therefore inexpressible through this surface. → **DH-011D**.

Also note bug 19: `PatientProvider` returns **`201 Created` even when the persist fails**, so any
target implementation must confirm by read-back, never by status code.

⚠️ **Never reached, and not claimed:** no `sample`, `sample_item`, `analysis` or `electronic_order`
row was created, so native laboratory workflow state, the OpenELIS UI's view of an incoming order, and
cancellation behaviour all remain **unverified**.

⚠️ **This throwaway stack now also contains two synthetic OpenELIS records**, both created through
the supported FHIR surface by DH-011B and deliberately retained rather than deleted: patient `id=2`
`Dhspike Zzsynthetic` (`fhir_uuid b010ea84-…`) and provider `id=2` `Dhprac Zzsynthetic`
(`fhir_uuid bac7daf3-…`). Both also exist in the standalone HAPI store. No sample, analysis or
electronic_order exists; laboratory tests (177 active) and `site_information` (121 rows) were not
modified, and no database row was written or deleted directly.

### BLOCKER-5 — SSO native authorization mapping · ✅ ROLE TYPES DECIDED 2026-08-14 (ADR-017), clinician row narrowed 2026-08-26 (ADR-017F) · ⏳ NAMES PENDING

The protocol/client plumbing is verified, and the role *types* are now decided. **The `clinician`
mapping was narrowed by ADR-017F (2026-08-26, superseding ADR-017's original `clinician` row,
preserved unchanged as history): `clinician` → `Provider` + `Daher Clinician Admission` — not
`Organizational: Nurse`.** `Organizational: Nurse` was rejected because its inheritance chain
(`Organizational: Nurse` → four `Application:` roles → `Privilege Level: High`) closed over 302 of
the system's 345 privileges, versus the 9 ADR-017D deliberately approved for admission — including
`Delete Patients`, `Delete Users`, `Manage Roles`, and `Manage Global Properties`, none proven needed
by any Daher workflow. This narrow identity is live-proven end to end (fresh-identity first-login
OIDC, exactly 2 roles/9 privileges, `currentProvider` non-null, the canonical admission contract,
and a `Manage Roles` negative-privilege control) — full evidence in `DECISIONS.md`'s ADR-017F entry.
`lab-tech` → `Reception`, `Results`, `Validation`; `sys-admin` → `Organizational: System
Administrator` + `Global Administrator` are unchanged by this.

**Only the staff names remain**, and they need the birth-centre meeting. Until real people are
attached to those types, SSO stays fail-closed off and the one-login acceptance test against real
staff is deliberately unperformed — turning it on would grant clinical and laboratory access to an
unreviewed set of accounts. (The one-login proof *has* now been performed against synthetic
identities on a disposable environment, closing the design/mechanism risk; what remains is the real
roster, a fact from the birth centre, not a design or mechanism gap.) **Bug #23 is now fixed,
automated-tested, and disposable-live-demonstrated**: the `openmrs` client's `oidc-usermodel-client-
role-mapper` (claim `roles`) shipped with no `usermodel.clientRoleMapping.clientId`, Keycloak's
all-clients form — it relayed every client role a user held on *any* client, not just `openmrs`. This
was proven exploitable, not inert: a synthetic user holding only a foreign client's role named
exactly `Provider` (no `openmrs` `Provider` grant) produced a `roles` claim containing `Provider`,
indistinguishable from a genuine grant by ADR-017E2's exact-string check, and — critically — OpenMRS
itself would have created a `currentProvider` from that. The fix adds
`usermodel.clientRoleMapping.clientId: "openmrs"` to the mapper, in both the fresh-realm import
(`daher-realm.json`) and the existing-realm reconciliation fragment
(`keycloak/mappers/openmrs-client-roles-mapper.json`) — Keycloak-native, no oauth2login/Java change,
ADR-017E2's exact-match logic untouched. Live-proven end-to-end on a disposable Keycloak 26.7.1 +
`openmrs-adr017f`: post-fix, a user with only the foreign `Provider` role plus `openmrs` → `Daher
Reception` authenticated with `roles: [Daher Reception]`, `currentProvider: null`, and **zero**
Provider database rows — the escalation path is closed. Full evidence, both provisioning paths, all
negative/mixed/composite/realm-role controls, and the CI static guard are in `DECISIONS.md`'s Bug #23
entry. **Bug #25 is fixed, automated-tested, and disposable-live-demonstrated**:
oauth2login now uses the authoritative committed User returned by OpenMRS's daemon creation call
instead of rereading through its old `REPEATABLE-READ` transaction snapshot; two fresh clinicians
and one fresh Reception identity authenticated on their first callbacks with no duplicates or
partial state. Exact evidence is in `PROJECT_STATE.md`'s Known Bugs table and `DECISIONS.md`'s Bug
#25 implementation record. A third issue found during this same pass, **Bug #24, is closed**: what
first looked like a first-login provisioning defect was this
session's own synthetic test data (an email using the non-existent `.invalid` TLD, correctly
rejected by OpenMRS's own email validator) — no platform defect, no partial database writes on the
failure, and first-login provisioning is confirmed clean (fresh identity, idempotent repeat logins)
once the test data was corrected.

---

## 5. Known placeholders — deliberate, not gaps to fill

**These are decided placeholders. An agent that "helpfully" fills them in is violating §5 of the
sprint spec, not being useful.**

| Placeholder | Status | Who resolves |
|---|---|---|
| Maternity bed counts | `.env` config, additive-only seed (ADR-007). Committed defaults are placeholders, marked as such. | Liban, from the birth centre |
| WhatsApp credentials | None held. Mock build; ticket stays `BLOCKED` not `DONE` (ADR-003). | Liban |
| Prenatal lab order set | **Mechanism built and verified; panel is deliberately EMPTY.** Neither system ships one to copy, so its contents must be composed — that is the clinical lead's decision. `docs/CLINICAL-SIGNOFF.md` lists the 37 installed lab sets for them to tick; nothing is pre-selected. | Clinical lead |
| Partograph form fields | Not yet written. Data-entry only, no decision support (ADR-006). | Clinical lead |

---

## 5a. What changed 2026-08-13/14

DH-015's restore drill was **performed** (partial coverage). DH-021 replaced the OpenELIS keystore,
whose private key was public *and whose certificate had already expired unnoticed*. DH-016's Caddy
proxy serves all three subdomains locally over HTTPS with a **verified** hop to the OpenELIS API.
DH-017's README now covers the whole stack. DH-006 and DH-004 gained their mechanisms — an
additive-only order-set seed shipping an **empty** panel, and an **executable** ADR-006 guard that
refuses any form performing clinical interpretation. Two false greens were closed: the OpenELIS
healthcheck that reported healthy through a total outage, and one inside the new form guard itself.

**Nothing above chose any clinical content.** The panel is empty, no form schema is committed, and
both sign-off requests are in `docs/CLINICAL-SIGNOFF.md`.

## 5c. What changed 2026-08-16/17 — the order path, half built and now waiting on a decision

Four commits, and the shape of the ticket changed twice because probing came before coding.

**Known bug 17 fixed** — the provisioning executor had one 900-second timeout for every verb, which
was shorter than a single healthcheck's grace period in the compose file it runs (`openmrs-backend`
declares `start_period: 1800s`). The first genuine facility provisioning would have been reported
`failed` while the stack came up correctly. Now per-verb, asserted against the real compose file.
It left **bug 18** open by decision rather than weakening ADR-022's replay window.

**BLOCKER-6 raised** — every document here said "the adapter routes the order to OpenELIS" without
naming a mechanism, and the mechanism they implied turns out not to be the one OpenELIS documents. It
**pulls** from a remote FHIR source. Five candidates are recorded; none is selected, because the
choice changes what the adapter *is* and one option adds a second system storing patient data.

**`_sort` correction** — the accepted watermark design's pseudocode relied on server-side ordering.
`fhir2` 4.2.0 accepts `_sort` and ignores it: ascending, descending and a nonsense key returned
byte-identical order over 50 distinct resources. Corrected in `WATERMARK-DESIGN.md` §3 by appending,
not by rewriting history.

**Option F spiked (DH-011B)** — OpenELIS's own webapp serves business FHIR providers that create
genuine native laboratory rows. Two constraints came with it: `ServiceRequest` is not
`identifier`-searchable there, and `PatientProvider` returns `201` on a failed persist.

**DH-011E built** — the OpenMRS half of the order path, verified under five controls, deliberately
unactivated.

**What this session did not do, on purpose:** select a BLOCKER-6 option, write a probe order into the
laboratory to force an answer, grant itself OpenELIS permissions to make a spike pass, change the
accession configuration, delete the synthetic patient it created, restart Caddy to clear bug 20, or
wire up the poll loop to look finished.

## 5b. Multi-facility provisioning — works end to end, on a stand-in

A provisioning API and portal (`provisioning/`) began 2026-08-15 as a **deliberate schedule trade**:
built before DH-011's transport chain, so the golden end-to-end flow will very likely not be
demonstrated by 1 September. That is a recorded decision, not a slip.

**What works, demonstrated across four containers:** signing into the portal and submitting a form
records a facility, queues a job, and the host's agent claims it over TLS, validates it locally, and
**actually runs `docker compose`** — containers come up, namespaced by `FACILITY_ID`, with secrets
the agent generated on its own host and never transported. 360 tests. State is durable (SQLite), so
a control-plane restart no longer loses the fleet.

**⚠️ Proven against a minimal stand-in compose file.** No OpenMRS, OpenELIS, Keycloak or adapter has
ever booted from a provisioning job, and the ~49-minute first boot is unexercised on that path. The
executor's single 900-second timeout — shorter than one healthcheck's grace period in the compose
file it runs — was fixed 2026-08-16 with per-verb bounds asserted against that file (known bug 17,
371 tests). That removes a certain false failure; it does not turn the stand-in into a real boot.

**Two design flaws this work found in itself:**

- **Rotation would have broken every facility** (ADR-024). Rewriting the env file would present new
  passwords to databases provisioned once by `initdb`, claim an admin-password change that never
  happened, and make every prior backup undecryptable. Rotation is now scoped, and refuses the rest
  with what is actually required.
- **The backup key would have died with the host** (ADR-025). Generating it on the machine being
  backed up means losing that machine — the disaster backups exist for — makes every off-site
  archive permanently undecryptable. The operator now supplies it at bootstrap.

**Still open:** public DNS/TLS (needs the domain), and a real clinical boot through this path.

## 6. What is genuinely missing

Derived from `PROJECT_STATE.md`'s ticket table rather than maintained by hand — an earlier revision
of this section still listed the restore drill as missing after it had been performed. If the two
disagree, the ticket table is right and this section is stale.

**Nothing built at all:**

| Ticket | Missing |
|---|---|
| DH-011 / DH-012 | The adapter's order and result transport. 🛑 **Blocked on BLOCKER-6** — the mechanism by which an order reaches OpenELIS is not what the design assumed. **The OpenMRS-side half is now built** (DH-011E: poller, durable watermark, cohort completeness, client-side ordering — 94 tests, five controls) but **deliberately not activated**, because there is nothing to deliver to. What is missing is the target leg, the correlation binding (DH-011C), and the mappings (DH-011G/H/I) |
| DH-013 | The WhatsApp notifier. Not written, and gated on credentials nobody holds (ADR-003) |
| DH-018 | The golden end-to-end demonstration — 1 of 8 steps |

**Mechanism built, contents deliberately empty — these need a person, not an engineer:**

| Ticket | State |
|---|---|
| DH-004 | Partograph/ANC forms. The ADR-006 guard is executable and refuses any form that interprets; **no form schema is committed** |
| DH-006 | Prenatal panel. Seeded additively and ships with **0 members**, by design. `docs/CLINICAL-SIGNOFF.md` is the handover sheet |
| DH-008 | SSO activation and the one-login proof. Plumbing verified; role *types* decided (ADR-017); staff **names** pending |

**Built but not finished — the boundary is the point:**

| Ticket | Done | Not done |
|---|---|---|
| DH-014 | Local encrypted path verified end to end | **Off-site upload never exercised** |
| DH-015 | A restore was **actually performed** 2026-08-13 | Two acceptance legs (a lab result, an SSO login) could not run because that data does not exist yet. Must be re-run |
| DH-016 | Routing, local HTTPS, verified TLS hop to OpenELIS | Publicly trusted certificates — needs the real domain and DNS |
| DH-017 | README covers the whole stack | Never walked on a genuinely fresh VM |
| DH-025 | Recovery core runtime-verified | Correlation not yet bound to a concrete OpenELIS resource (a DH-011 gate) |
| DH-027 | Provisioning works end to end, and a stack starts from a job | **Against a stand-in compose file.** No clinical stack has ever booted this way |

---

## 7. Risks to 1 September, specifically

| Risk | Severity | Why | Mitigation |
|---|---|---|---|
| **Restore drill gets squeezed last** | **Reduced 2026-08-13** | Was the top risk. A real restore has now been performed and documented, and it found two defects — `pg_restore --clean` silently losing rows over the pre-populated OpenELIS image, and a half-initialised Postgres at exit 127. | Largely mitigated. Residual: the drill must be **re-run** once a lab result exists and SSO is active, and off-site is still unexercised. Budget that re-run rather than assuming DH-015 is closed. |
| SSO across two apps | **Reduced 2026-08-14** | Protocol/client plumbing is verified and the role *types* are decided (ADR-017), so the design question is answered. What is left is naming the staff who hold each type — a fact from the meeting, not a design problem. Caddy now provides the HTTPS origins the browser session needs. | Keep fail-closed off until the names exist, then activate and demonstrate the full two-app session. Budget the demonstration itself: it has never been performed. |
| OpenMRS order polling safety | **High** | DH-025's recovery core, crash tests, and now DH-011E's poller/watermark all pass under five deliberate controls — but safety still depends on the concrete OpenELIS write carrying its correlation and a demonstrated exact target lookup, and **DH-011B found that the business FHIR server does not support `identifier` search on `ServiceRequest` at all**. So the correlation cannot bind where the design assumed. | Resolve BLOCKER-6, then bind and demonstrate correlation attach/query in DH-011C before activation; `Specimen` is identifier-searchable and worth testing before DH-011D's patch. Multiple matches or an unavailable lookup remain ambiguous and fail closed. |
| **Trusting an upstream success code** | **High** | New 2026-08-17. OpenELIS's `PatientProvider` returns `201 Created` when its native persist fails (bug 19), with no `Location` header. An adapter that believed it would write `completed` into the ledger for an order that never existed — silent loss, at scale, looking entirely healthy. | Any target implementation **verifies by read-back**, never by status code. The rule is recorded in the poller's own module docstring and in the known-bugs table so it survives this session. |
| O3 first-boot slowness | Medium | Liquibase on an empty database takes minutes; easy to misread as a hang and start "fixing" a working system. | Generous `start_period` already set. Watch logs, don't restart. |
| WhatsApp credentials never arrive | Medium | Real risk given none exist today. | Already handled by design: mock now, `.env`-only swap, ticket stays `BLOCKED`. Does not block anything else. |
| Clinical sign-off not obtained | **High** | Gates *clinically live*, not pilot-ready — but a pilot with no sign-off path is a pilot that cannot proceed to use. | Get the order set and partograph fields in front of the clinical lead as soon as they are drafted, in week 1, not week 3. |
| Version drift on moving tags | Medium | `3.7.x` is a branch tag; a rebuild could silently change the application. | Pin digests at DH-002, immediately after first pull. |

**Schedule reality:** 22 days for eight build steps, with roughly three of them (SSO, adapter,
restore drill) historically prone to consuming multiple days each. There is no slack for
rework caused by skipping verification. Verify each step before moving on, as the build prompt
requires.
