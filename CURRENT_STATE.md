# Current State — Daher Health

**Assessed:** 2026-08-14 · **Target:** pilot-ready by 2026-09-01
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
without persisting its client secret. SSO is not active and its one-login acceptance demonstration
is not complete: native role/provider mapping is a human authorization decision (BLOCKER-5).

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
form-verified, but its human browser workflow is unexercised until Caddy (DH-016); SSO activation
and one-login proof, maternity clinical forms, adapter transport, notifications, complete
backup/restore, and public TLS remain incomplete. The maternity ward/bed foundation itself is
complete. Human decisions are collected under **NEEDS LIBAN** in `PROJECT_STATE.md`.

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
| Adapter | **running, healthy, DH-010 + DH-025 core verified** — ledger/dead-letter/retry/audit plus process-lifetime recovery fence and fail-closed crash coordinator; concrete OpenELIS correlation binding and all FHIR transport remain absent |
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
| SSO | **Plumbing runtime-verified; activation unverified** | OpenMRS OIDC module is registered/stopped in disabled mode; OpenELIS is configured for SAML; Keycloak client reconciliation is idempotent. BLOCKER-5 prevents role mapping, activation, and one-login proof. |
| `docs/ARCHITECTURE.md` | Current | §3's stale "MariaDB 11.4" row corrected to 10.11 at DH-002. |

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
foundation; the watermark and poller themselves remain DH-011.

### BLOCKER-3 — DH-011 activation and cancellation contract · 🗓 TWO LEGS OPEN

The polling algorithm is accepted, and the **first-sync boundary is now decided (ADR-015): no
historical backfill, watermark starts at deployment time.** Two clinical/scope choices remain: the
real requester identity attached to orders in OpenELIS, and the laboratory-side state transition
for a `ServiceRequest.replaces` discontinuation. Upstream's reference bridge explicitly leaves panel
support and discontinuation incomplete. Both wait on the birth-centre meeting; no default was
inferred and no order transport was activated.

### BLOCKER-4 — OpenELIS Horiba Micros60/Pentra60 analyzer imports · ✅ CLOSED BY SCOPE (ADR-016)

v1 is manual result entry only — no analyzer, HL7, or ASTM interface; DH-023 is deferred to v1.1.
The webapp remains reachable and both upstream analyzer CSVs still fail on the same unique
description, **left exactly as upstream ships them**: each transaction rolls back, the live table
has zero duplicate descriptions, and the endpoints answer. ADR-016 does not authorize disabling,
renaming, repairing, or log-suppressing those tests. No test or mapping has ever been changed.

### BLOCKER-5 — SSO native authorization mapping · 🛑 OPEN

The protocol/client plumbing is verified, but `clinician`, `lab-tech`, and `sys-admin` still need
explicit mappings to native roles in each application, plus an OpenMRS-provider decision. Those
choices grant clinical/laboratory access and affect attribution, so SSO remains fail-closed off.
Exact evidence and the requested matrix are in `PROJECT_STATE.md`.

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

## 5b. Multi-facility provisioning — started, not working

A provisioning API and portal (`provisioning/`) began 2026-08-15 as a **deliberate schedule trade**:
it is being built before DH-011's transport chain, so the golden end-to-end flow will very likely
not be demonstrated by 1 September. That is a recorded decision, not a slip.

**What exists:** per-facility secret generation, closed API schemas, and the structural
no-clinical-data guarantee (46 tests, control-verified). `FACILITY_ID` now namespaces the clinical
stack.

**What exists now (159 tests):** per-facility secret generation, closed API schemas, the facility
env writer, a hash-chained audit log, scoped auth, rate limiting, the deploy-host allowlist, and the
FastAPI surface enforcing all of it over HTTP. The deploy transport is decided — **ADR-022,
pull-based host agent, outbound-only**, with the compose file living on the facility host so the API
sends parameters and never a spec.

**What does not exist:** the host agent and the portal. **Nothing provisions a real facility
today** — `POST /facilities` returns 201 meaning *recorded*, not *running*, and lists what did not
happen. DNS/TLS, the audit log's external destination, and cross-process rate limiting are
deliberately incomplete and each reports its own incompleteness rather than appearing to succeed.

## 6. What is genuinely missing

OpenMRS ANC and partograph forms; prenatal panel; SSO authorization mapping, activation, and proof; adapter
order/result transport; WhatsApp notifier; the
remaining backup layers **and a performed restore drill**; Caddy/TLS; the golden demonstration;
and a README complete enough to hand the stack to another operator.

---

## 7. Risks to 1 September, specifically

| Risk | Severity | Why | Mitigation |
|---|---|---|---|
| **Restore drill gets squeezed last** | **Reduced 2026-08-13** | Was the top risk. A real restore has now been performed and documented, and it found two defects — `pg_restore --clean` silently losing rows over the pre-populated OpenELIS image, and a half-initialised Postgres at exit 127. | Largely mitigated. Residual: the drill must be **re-run** once a lab result exists and SSO is active, and off-site is still unexercised. Budget that re-run rather than assuming DH-015 is closed. |
| SSO across two apps | High | Protocol/client plumbing is verified, but role/provider mapping grants clinical access and cannot be inferred. Public-origin cookies also need DH-016 HTTPS. | Keep fail-closed off; obtain BLOCKER-5's explicit authorization matrix, then activate and demonstrate the full two-app session after Caddy. |
| OpenMRS order polling safety | **High** | DH-025's recovery core and crash tests pass, but safety still depends on the concrete OpenELIS write carrying its correlation and a demonstrated exact target lookup. | Bind and demonstrate correlation attach/query in DH-011 before activation; multiple matches or unavailable lookup remain ambiguous and fail closed. |
| O3 first-boot slowness | Medium | Liquibase on an empty database takes minutes; easy to misread as a hang and start "fixing" a working system. | Generous `start_period` already set. Watch logs, don't restart. |
| WhatsApp credentials never arrive | Medium | Real risk given none exist today. | Already handled by design: mock now, `.env`-only swap, ticket stays `BLOCKED`. Does not block anything else. |
| Clinical sign-off not obtained | **High** | Gates *clinically live*, not pilot-ready — but a pilot with no sign-off path is a pilot that cannot proceed to use. | Get the order set and partograph fields in front of the clinical lead as soon as they are drafted, in week 1, not week 3. |
| Version drift on moving tags | Medium | `3.7.x` is a branch tag; a rebuild could silently change the application. | Pin digests at DH-002, immediately after first pull. |

**Schedule reality:** 22 days for eight build steps, with roughly three of them (SSO, adapter,
restore drill) historically prone to consuming multiple days each. There is no slack for
rework caused by skipping verification. Verify each step before moving on, as the build prompt
requires.
