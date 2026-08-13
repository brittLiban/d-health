# Project State — Daher Health

**Updated:** 2026-08-11 · **Phase:** Step 1 of 8 ✅ done · ADR-005 evidence awaiting review
**Target:** pilot-ready 2026-09-01 · **Not** clinically live (ADR-012)

Single agent, one ticket at a time. Update this file before ending a session.

---

## Current phase

Step 1 (MariaDB + OpenMRS O3) **boots and works**. Images are pulled and digest-pinned; a test
patient was registered and retrieved through REST and FHIR R4, and survived `down`/`up`. Running
it exposed five defects that `docker compose config` could never have caught — see
`CURRENT_STATE.md` §3a. Two remain open as flagged decisions (BLOCKER-2, DH-019).

## Ticket status

| Status | Tickets |
|---|---|
| ✅ Done | DH-001, **DH-002** (Liban confirmed 2026-08-11), **DH-019** (verified on an empty volume) |
| 🟢 Built + verified, awaiting Liban's own look | **DH-005** (PostgreSQL + OpenELIS) |
| 🟡 Awaiting Liban's review | **DH-009a** + **DH-009b** (ADR-005 evidence, both legs) |
| 🆕 Raised while building | DH-020 (Postgres public-schema grant), DH-021 (public keystore private key) |
| 🔨 In progress | DH-017 (README — now covers step 1 fully, incl. boot timing and the demo-patient warning) |
| 📋 Ready | DH-019 (admin password wiring — new, from DH-002) |
| ⏸ Waiting on dependencies | DH-003 … DH-018 |

## ▶ START HERE (next session)

1. **DH-009a needs Liban's review before any adapter code exists.** ADR-005 forbids building the
   poller until the watermark design has been read. `docs/WATERMARK-DESIGN.md` is written and
   built into nothing. The two questions that actually need his judgement are §9.1 (poll interval
   — a clinical-workflow question, not a technical one) and §9.3 (polling being the only available
   trigger is new information bearing on ADR-004's `labonfhir` deferral).
2. **BLOCKER-2 also needs him** — the distro creates 50 fictional patients on first boot, and
   `OMRS_ADD_DEMO_DATA=false` does not stop it. Not urgent locally; a hard gate before the pilot
   VM is built, and it shapes DH-003.
3. **Then DH-003 or DH-005**, whichever he prefers. DH-005 (PostgreSQL + OpenELIS) additionally
   unblocks DH-009b, the untouched half of ADR-005.

**If you restart the stack:** a cold boot is ~50 minutes, a warm boot ~38 seconds. Never
`docker compose down -v` — that destroys the volumes and the evidence.

---

## ▶▶ NEEDS LIBAN — the decision list

Everything an agent could not decide alone, in one place, roughly in the order it starts costing
time. Nothing below is urgent for the local stack; several are hard gates before the pilot VM
exists. Each links to the detail rather than repeating it.

| # | Decision | Why it isn't mine | Blocks |
|---|---|---|---|
| 1 | **The 50 fictional demo patients** — the distro creates them on first boot, and `OMRS_ADD_DEMO_DATA=false` does not stop it. Keep the distro's config as shipped, or restrict Initializer/OCL to the non-demo set? | Concept-dictionary + patient-data surface. Stripping the `-demo` tree is the "obviously right" tidy-up AGENTS.md §5 exists to stop, and it may remove the lab-test concepts DH-006 needs | BLOCKER-2 → DH-003, and the pilot VM's first boot |
| 2 | **Poll interval for the adapter** — how long may a prenatal order sit before the laboratory sees it? | A clinical-workflow question, not a technical one | `WATERMARK-DESIGN.md` §9.1 → DH-010 |
| 3 | **Does voiding an order need to reach the laboratory?** If a voided order never advances `meta.lastUpdated`, the LIS keeps working an order the EMR considers cancelled | Patient-safety behaviour, and it may need a reconciliation sweep rather than a watermark | `WATERMARK-DESIGN.md` §6 → DH-010 |
| 4 | **Revisit `labonfhir` (ADR-004)?** It was deferred partly because we'd own the trigger either way. Owning it now demonstrably means *polling* | A recorded decision; ADRs are not re-litigated by an agent | ADR-005 review → DH-010 |
| 5 | **OpenELIS default requester identity** — upstream ships a sample clinician ("Felix Dr. Mauritius") as the default order requester. Left unmodified rather than invented | Clinical content. Never invented (AGENTS.md) | DH-006 / go-live |
| 6 | **Facility identity** — country, state, district, city, postal code, facility id are empty, exactly as upstream ships them | The birth centre's real details are yours | `openelis-config/properties/common.properties` |
| 7 | **Maternity bed counts** — committed defaults are placeholders (ADR-007) | Facility numbers | DH-003 |
| 8 | **Prenatal panel + partograph fields** — untouched, both need the clinical lead | Hard gate for *clinically live* (ADR-012) | DH-004, DH-006 |

**Read-and-approve, rather than decide:** ADR-005's evidence section in `DECISIONS.md`, then
`docs/WATERMARK-DESIGN.md`. ADR-005 forbids building the poller until you have read it, so DH-010
is parked on your review, not on effort.

**Two security items I fixed without asking** (disabling exposure needs no permission; restoring it
would): upstream's `mail.bcc`/`mail.from` default of a third-party gmail address is blanked, and
every published default credential found so far is now guarded so it fails loudly instead of
shipping — OpenMRS admin (DH-019), the OpenELIS DB roles, and OpenELIS's `DEFAULT_PW`.

---

## Blockers

### BLOCKER-1 — Docker daemon unreachable · ✅ RESOLVED 2026-08-10

**Ticket:** DH-002
**Was:** `libanbritt` was not in the `docker` group; every docker command failed with
`permission denied` on `/var/run/docker.sock`.
**Response recorded:** Liban ran `sudo usermod -aG docker $USER`. Verified in `/etc/group`
(`docker:x:973:libanbritt`). Group membership attaches at login, so it required a logout/reboot
and a Claude Code restart to take effect — done 2026-08-10.
**Note for the pilot VM:** the same choice recurs there with higher stakes. `docker` group
membership is effectively root on the host. Decide it deliberately, not by habit.

### BLOCKER-2 — The O3 distro loads its **demo** concept configuration on boot · **Human Decision Required: Yes**

**Ticket:** DH-003 (surfaced during DH-002; does **not** block DH-002)
**Raised:** 2026-08-10

**Observed, not inferred.** Booting the stock backend image, the container's Initializer
configuration tree contains two OCL package directories:

- `/openmrs/data/configuration/ocl/referenceapplication/` — 2 packages
  (`CIELImmunizationContent`, `PD`)
- `/openmrs/data/configuration/ocl/referenceapplication-**demo**/` — 21 packages, ~6 MB zipped,
  including `BasicLabTests`, `DiagnosesStarterKit` (3.7 MB), `BasicDrugs`, `CIELAllergySet`,
  `procedures`, and also `DemoPrograms`, `DemoQueueConcepts`, `GenericDemoForm`, `IPD-DEMO`

All of them import on every boot (`openconceptlab_import` rows, one per package). Alongside them,
`configuration/concepts/referenceapplication-demo/*.csv` loads a further ~700 concept rows.

**Why this is a human decision and not an agent's.** This is *concept dictionary content* — the
medical terminology the EMR offers clinicians. Three things are tangled together and only a human
should separate them:

1. Most of it is legitimate unmodified reference content (CIEL-derived lab tests, diagnoses,
   drugs) — exactly what the non-negotiable "reference defaults only" rule *wants*.
2. Some of it is explicitly demo scaffolding (`GenericDemoForm`, `DemoQueueConcepts`, `IPD-DEMO`).
3. It arrives from a directory named `-demo`, so "is this facility-appropriate clinical content or
   is it sample data?" cannot be answered from the directory name alone.

Stripping the `-demo` tree would be the "obviously right" tidy-up — and that instinct is precisely
what §5 of the sprint spec exists to stop. Removing it may also remove the lab test concepts that
DH-006's prenatal panel needs to map to. **Not touched.**

### ⚠️ It is NOT metadata only — the distro creates 50 fictional PATIENTS

An earlier note in this entry said no demo patients were created. **That was wrong**, and the
correction matters more than the original entry. The count was taken mid-boot; the demo patients
are created in the last seconds before the app answers.

**Observed after the boot completed:** `SELECT COUNT(*) FROM patient` = 51 — the one deliberate
test patient plus **50 fictional people** (Betty Williams `100000Y`, Michael Turner `100006J`,
Susan Lopez `100001W`, …), all `date_created 2026-08-11 10:24:46`.

**Mechanism, located exactly:**

- The image bundles `referencedemodata-2.6.1.omod` in `/openmrs/data/modules/`.
- It is driven by the OpenMRS **global property**
  `referencedemodata.createDemoPatientsOnNextStartup` — now `0`, having fired once on this boot.
- **`OMRS_CONFIG_ADD_DEMO_DATA=false` did not prevent this**, consistent with that variable being
  inert (see the compose comment). The real control is a global property this repo does not set,
  in a module this repo did not choose to install.

This is the exact hazard `.env.example` warned about in words — fictional people in the same
tables as real ones — reached through a mechanism nobody had identified. It is *not* a reason to
panic: this is a local throwaway verification stack. It **is** a hard gate on the pilot instance.

**Not acted on, deliberately.** Deleting patient rows and disabling a bundled module are both
outside DH-002, and one of them touches patient tables. Setting the global property belongs in
DH-003's Initializer configuration, where it applies from first boot — which is the only moment
that helps, since the property self-clears after firing.

**Needs a decision before DH-003:**
1. Keep the distro's configuration as shipped, or restrict Initializer/OCL to the non-demo
   `referenceapplication` set (weighing that DH-006's prenatal panel may need `BasicLabTests`).
2. How demo patient creation is suppressed on the pilot: global property via Initializer, removing
   the module from the distro, or both — and how that is **verified** on a fresh boot rather than
   assumed.

_No blockers open against DH-002._

---

## Golden end-to-end flow — status

The one flow that makes Day 1 mean anything (sprint spec §8). Demonstrated directly to Liban, not
reported as passing.

| # | Step | Status |
|---|---|---|
| 1 | Register test patient in OpenMRS | ✅ Liban registered a patient in the O3 SPA himself, 2026-08-11 |
| 2 | Same SSO session into OpenELIS, no second login | ⏸ |
| 3 | Place prenatal panel lab order in OpenMRS | ⏸ |
| 4 | Order appears in OpenELIS worklist | ⏸ |
| 5 | Enter result in OpenELIS | ⏸ |
| 6 | Result appears in OpenMRS chart | ⏸ |
| 7 | WhatsApp notification fires (mock OK for Day 1; real send is the `DONE` gate) | ⏸ |
| 8 | Audit log shows full round trip, **no duplicates on retry/restart** | ⏸ |

---

## Tickets, in dependency order

Priority: **P0** = required for pilot-ready · P1 = if time allows · P2 = post-pilot.
🔒 = pinned P0, immune to being cut under deadline pressure (sprint spec §2).

### DH-001 · Record decisions as ADRs · P0 · ✅ DONE
Move the reasoning that existed only in conversation into `DECISIONS.md`. Twelve ADRs, including
ADR-005 (the open Subscriptions/polling question) and ADR-004 (the `labonfhir` deferral).
**DoD:** ✅ ADRs committed; `CURRENT_STATE.md`, `PROJECT_STATE.md`, `AGENTS.md` created.

### DH-002 · First pull, digest pinning, verify OpenMRS · P0 · ✅ DONE
**Worked:** 2026-08-11. **Confirmed by Liban 2026-08-11** — he registered a patient through the O3
single-page app himself and reported it working. That was the one criterion an agent could not
close.

**Precision about what his run covered:** he confirmed registration works. He did **not** report
on the SPA's display language, so the French-locale question below is still open — it is a DH-003
item either way.

**Acceptance criteria, individually:**

| Criterion | Result |
|---|---|
| Every image pinned by digest | ✅ Four repository digests pulled and recorded (compose + ARCHITECTURE §12). All four tags existed as listed; no substitutions |
| OpenMRS reachable on `${OMRS_LOCAL_PORT}` | ✅ after fixing the gateway's hard-coded upstream hostnames |
| Admin login works | ✅ via REST (`/ws/rest/v1/session`, `authenticated: true`). **Not** via the SPA |
| Test patient registered and retrieved | ✅ `10001MJ — Test-DH002 Verification`; found by name, by identifier, and via FHIR R4. **Registered through the API, not the registration form** |
| `down` && `up` returns to a working state, data intact | ✅ same patient, same UUID; no duplication (patients 51, concepts 4,259, OCL items 27,319 all identical); warm boot 38s |
| Healthchecks actually pass rather than merely being defined | ✅ — and the backend's was **found lying** and rewritten. See `CURRENT_STATE.md` §3a |
| DoD: verification steps in `README.md` | ✅ including the ≈50-minute first-boot warning and the demo-patient warning |

**Why this is not ✅ DONE.** Every check ran through `curl`. The O3 single-page app has never been
opened in a browser, and the sprint spec's own verification for step 1 is *"open OpenMRS, log in,
register a test patient"* — performed by Liban, not reported to him. Closing this ticket requires
his run.

**Surfaced here, tracked elsewhere:** BLOCKER-2 (50 demo patients — human decision) and DH-019
(admin password). Neither blocks this ticket; both block the pilot instance.

### DH-019 · Wire the OpenMRS admin password through compose · P0 · ✅ DONE 2026-08-11
**Verified the only way it can be**: on a throwaway scratch project with an empty volume, a full
cold boot, then `admin` + the configured password → `authenticated: true`, and `admin`/`Admin123`
→ refused. The live stack was never touched, and the scratch stack was destroyed afterwards.
**Raised by:** DH-002, 2026-08-11.
**Problem.** The backend image sets the `admin` password from `OMRS_ADMIN_USER_PASSWORD`,
defaulting to the published value `Admin123`
(`OMRS_ADMIN_USER_PASSWORD=${OMRS_ADMIN_USER_PASSWORD:-Admin123}` in the image's
`startup-init.sh`; `OMRS_CONFIG_ADMIN_USER_PASSWORD` overrides it). The compose file passes
neither, so every instance built from this repo today ships with a known admin password.
**The sharp edge:** the password is written into the database **when OpenMRS initialises an empty
schema, and only then**. Setting it after the first boot does nothing. So this must land *before*
the pilot VM is first brought up, not before go-live.
**Requirements:** pass `OMRS_CONFIG_ADMIN_USER_PASSWORD` from `.env` (`OMRS_ADMIN_PASSWORD`,
already documented in `.env.example`); guard it with `${VAR:?}` so an empty value fails loudly
rather than silently falling back to the default; document the post-first-boot recovery path
(manual password change) in the runbook.
**Acceptance:** a fresh stack with a generated password refuses `Admin123` and accepts the
configured one. Verified on an empty volume, since that is the only case where it applies.

### DH-009a · Spike: ADR-005, **OpenMRS leg** · P0 · 🟡 EVIDENCE READY — awaiting Liban's review
**Worked:** 2026-08-11, against the running DH-002 stack rather than from documentation.

**Answer: neither event-driven option exists in the O3 3.7.x distro as we deploy it.**
- FHIR `Subscription` → HTTP 404, `HAPI-0302: Unknown resource type`; absent from the
  CapabilityStatement's 24 advertised types; `ServiceRequest` returns 200 as a control.
- `event-4.0.0`'s ActiveMQ broker is `vm://localhost` (in-JVM), the module ships an empty
  `moduleApplicationContext.xml`, and 61616 is refused. A separate adapter container cannot reach
  it. (A bare `tcp://` string exists in the jar; no way to expose it was found — "none found" is
  recorded as exactly that, not as "none exists".)
- Polling is viable: `_lastUpdated` is advertised **and verified applied** on `ServiceRequest`,
  `DiagnosticReport`, `Observation`, `Task`.

**Evidence** is appended to ADR-005 in `DECISIONS.md` — **status deliberately left `OPEN`**, since
that ADR's acceptance is "reviewed by Liban personally". **The watermark design is written and
built into nothing**: `docs/WATERMARK-DESIGN.md`, as ADR-005 requires.

**What Liban is being asked to read**, in order: ADR-005's evidence section, then
`WATERMARK-DESIGN.md` §4 (why `ge` and not `gt` — the choice that trades duplicates for never
losing a result), §6 (the void-visibility gap), §7 (the timezone constraint), and §9.3
(polling being the only trigger is new information bearing on ADR-004's `labonfhir` deferral).

**Not done here, by instruction:** no poller was written. ADR-005 forbids building before review.

### DH-009b · Spike: ADR-005, **OpenELIS leg** · P0 · ⏸ (needs DH-005)
The reverse direction — how OpenELIS tells us a result is ready — **could not be touched**:
OpenELIS is not deployed. DH-009a's "polling" answer covers the OpenMRS leg **only** and must not
be read as covering this one. Same standard applies: primary evidence, and if the answer is polling,
the result-leg watermark design gets reviewed before it is built.

### DH-009 · (superseded by DH-009a / DH-009b above) · P0
**Objective:** answer, with primary evidence, how the adapter learns an order/result exists.
**Depends on:** nothing — this is source and documentation reading. **Do it early**; it gates the
longest pole after SSO, and it does not need a running stack.
**Requirements:** read the `fhir2` module source and docs for the 3.7.x line; determine whether
outbound FHIR Subscriptions are supported and stable; do the same for OpenELIS's outbound result
direction. **Present quoted source with file paths — a conclusion alone is not acceptable output**
(ADR-005). If the answer is polling, write the watermark design (keying, advancement, restart
mid-window, back-dated records, failure mode when wrong) for human review **before** building it.
**Acceptance:** ADR-005 updated from `OPEN` to a decision, with evidence, reviewed by Liban.

### DH-003 · OpenMRS maternity config as code · P0 · ⏸ (needs DH-002)
Initializer config: French locale list + default, Maternité ward, bed types (delivery /
postpartum), additive-only bed seed driven by `MATERNITY_*_BEDS` (🔒 ADR-007). Confirm whether
Initializer covers bed domains; if not, an idempotent REST seed — and record which, in
`ARCHITECTURE.md` §4.
**Acceptance:** fresh boot produces the ward and beds with no admin clicking; changing a count in
`.env` + restart adds beds; **lowering a count deletes nothing**.

### DH-004 · ANC + partograph forms · P0 · ⏸ (needs DH-003) · **Human Decision Required: Yes**
Data-entry only. 🔒 No alert lines, no action lines, no prolonged-labour flagging (ADR-006).
Fields from unmodified WHO/OpenMRS reference defaults, marked *needs sign-off from clinical lead*.
**Cannot be marked DONE without a recorded human response**, even when technically complete.

### DH-005 · PostgreSQL + OpenELIS standalone · P0 · 🟢 BUILT AND VERIFIED 2026-08-12
Five services added, all digest-pinned. Verified by running, not by `compose config`:

| Check | Result |
|---|---|
| Three databases as co-tenants (ADR-001) | ✅ `clinlims`, `keycloak`, `adapter`, correct owners |
| Tenant isolation | ✅ keycloak cannot reach `adapter`; adapter cannot read OpenELIS clinical data. ⚠️ one gap left open on purpose — DH-020 |
| OpenELIS schema provisioned | ✅ 167 tables in the `clinlims` schema |
| Webapp serving | ✅ HTTPS 8443 (its HTTP connector does not listen) |
| FHIR server serving | ✅ `GET /fhir/metadata` → 200, **over mutual TLS** |
| Frontend container | ✅ running, **not yet exercised** — it had no route until Caddy (DH-016) |

**Four things running it exposed** (none catchable by a syntax check):
1. `certs` **cannot** be dropped as ARCHITECTURE §2 claimed — Tomcat aborts without the keystore.
2. The webapp reads its Spring config from a **Docker secret**; without it the WAR deploys, the
   context dies with `FileNotFoundException: /run/secrets/common.properties`, and Tomcat still
   reports a clean startup — a failure that looks like success in `docker compose ps`.
3. The FHIR container ignores `JAVA_OPTS` (bitnami replaces it); `CATALINA_OPTS` works. With the
   properties unsubstituted, **both** connectors stayed down while the log said "Server startup in".
4. The OpenELIS FHIR endpoint requires **mutual TLS** — see ADR-005 Q5. Load-bearing for DH-011/012.

**Not done here:** the frontend is unexercised, and no OpenELIS login has been performed. Both need
either Caddy (DH-016) or a temporary port, and neither is required by this ticket.

### DH-020 · Restrict PUBLIC on the OpenELIS database · P1 · 📋 READY
Raised at DH-005. The `adapter` role can connect to `clinlims` and create objects in its `public`
schema, because PostgreSQL grants that to `PUBLIC` by default and our init hook only revokes on the
two databases it creates. **It cannot read clinical data** (`permission denied for schema clinlims`),
so this is hardening, not an exposure. Left alone deliberately: OpenELIS keeps 3 objects in that
schema, revoking risks breaking the laboratory system, and doing it unsupervised as a side effect of
another ticket is the wrong way to touch a clinical database.

### DH-021 · Replace the shipped keystore · P1 · 📋 READY
Raised at DH-005. `itechuw/certgen` generates nothing — it ships **pre-built** keystores inside a
public image, so the private key protecting the internal 8443 connectors is publicly downloadable,
and its passwords are the published `kspass`/`tspass`. Acceptable only while nothing outside the
Docker network speaks to 8443. Interacts with ADR-005 Q5: the adapter authenticates with a client
certificate from this same keystore, so replacing it is a coordinated change, not a drop-in.

### DH-006 · Prenatal panel order set + LOINC mappings · P0 · ⏸ (needs DH-005) · **Human Decision Required: Yes**
Reference panels as a starting point, as code. Clinical sign-off gate — draft early (week 1) so
the clinical lead has time.

### DH-007 · Keycloak + realm import · P0 · ⏸ (needs DH-005)
`--import-realm`, committed realm JSON based on the OpenMRS HIS distro's, secrets from `.env`,
French login theme, adapter service account.

### DH-008 · SSO wiring · P0 · ⏸ (needs DH-003, DH-005, DH-007)
**Requires joint understanding of: Keycloak realm config, OpenMRS auth, OpenELIS auth.** Not
split — a working realm and a working OpenMRS that don't authenticate against each other is the
exact failure this ticket exists to prevent. Includes the documented break-glass local admin.
**Acceptance:** one login, both apps, no second prompt.

### DH-014 · Backup, both engines · P0 🔒 · ⏸ (needs DH-005)
`mariadb-dump` + `pg_dump` (all three DBs) + adapter audit volume + OpenMRS data volume +
Keycloak realm export → encrypted → S3-compatible. **Scheduled deliberately here, in parallel
with SSO, not after the adapter** — it only needs databases, and putting it last is how pinned
work gets squeezed (`CURRENT_STATE.md` §7).

### DH-015 · Restore drill, performed and documented · P0 🔒 · ⏸ (needs DH-014)
Restore into a scratch stack; verify a known test patient, a known lab result, and a working SSO
login. **"The dump ran" is not this ticket.** A restore that only covers PostgreSQL would return a
working lab system and no patient records — that check is explicit in `backup/restore-drill.md`.
**DoD:** drill actually performed, output recorded, dated.

### DH-010 · Adapter skeleton: audit ledger, idempotency, retry · P0 · ⏸ (needs DH-009)
Processed-message ledger keyed on stable source event ID with a unique constraint; append-only
structured audit log on a named volume; exponential backoff with jitter; dead-letter table. Two
separate mechanisms by design — the ledger answers "did we do this", the audit answers "what did
we do, to whom, when".

### DH-011 · Adapter: order path OpenMRS → OpenELIS · P0 · ⏸ (needs DH-010)
### DH-012 · Adapter: result path OpenELIS → OpenMRS · P0 · ⏸ (needs DH-011)
Record observed `ServiceRequest` / `DiagnosticReport` shapes here → triggers the ADR-004 revisit.

### DH-013 · WhatsApp notifier against mock · P0 · ⏸ (needs DH-012) · 🔒 gate
French default + English templates; content-free ("results are ready", never values). Notifier is
a module inside the adapter (ADR-002). **Status on completion of the mock work is `BLOCKED`, not
`DONE`** (ADR-003).

### DH-016 · Caddy, TLS, subdomains · P0 · ⏸ (needs DH-008)
Last, deliberately — not fighting certificates while debugging applications. Removes the
loopback port from step 1.

### DH-017 · README for a fresh Ubuntu 24.04 VM · P0 · 🔨 IN PROGRESS
Started 2026-08-10 — covers project purpose, status warning, doc map, prerequisites, `.env` setup,
and step-1 run + verification. Grows with each subsequent step.
**DoD:** someone else could deploy a second instance without asking Liban anything. Not there yet —
it currently documents step 1 only, and says so.

### DH-018 · Golden end-to-end demonstration · P0 · ⏸ (needs DH-013, DH-015)
All eight steps of §8, demonstrated live to Liban, including the retry/restart no-duplicates check.

### P1 / P2
P1: monitoring, digest-pinning automation, `docs/RUNBOOK.md` depth, `docs/CLINICAL-SIGNOFF.md`
tracking. P2: graphical partogram plotting recorded values only (still needs the ADR-006
conversation), RapidPro, Orthanc/PACS, multi-facility.

---

## Critical path

```
BLOCKER-1 (human) → DH-002 → DH-005 → DH-007 → DH-008 ──┐
                       │                                 ├→ DH-018
DH-009 (start now) → DH-010 → DH-011 → DH-012 → DH-013 ─┘
                       │
        DH-014 → DH-015  (run parallel with SSO, not after)
```

Longest poles: DH-008 (SSO) and DH-010–012 (adapter, shape depends on DH-009).

## Known bugs

| # | Bug | Status |
|---|---|---|
| 1 | Gateway could not resolve `backend`/`frontend` — hard-coded upstream names in its nginx template | Fixed (network aliases) |
| 2 | Backend healthcheck reported healthy 8s into a 49-minute boot | Fixed (explicit `= 200` on the REST session endpoint) |
| 3 | `OMRS_CONFIG_ADD_DEMO_DATA` inert in this image | Documented, not removed |
| 4 | 50 fictional demo patients created on first boot | **Open — BLOCKER-2**, human decision |
| 5 | Admin password defaults to the published `Admin123` | **Open — DH-019** |

## Notes for next session

- See **▶ START HERE** near the top of this file for the immediate next action.
- Read `DECISIONS.md` before touching anything. ADR-005 is the live open question; ADR-003,
  ADR-006, ADR-007 are pinned and not to be "simplified".
- **Step 1 has now genuinely run** — but "verified through the API" and "demonstrated to a human
  in the UI" are different claims, and only the first is true. Keep them apart in what you report.
- `docker compose config` passing still means only that the syntax parses. Every one of the five
  findings above survived that check.
- Digests are recorded from a real pull (ARCHITECTURE §12). **Never fabricate a digest.**
- Timing: cold boot ≈49 min, warm ≈38s. Never `docker compose down -v`.
- Nothing here authorizes real patient data. Pilot-ready ≠ clinically live (ADR-012). This local
  stack now holds 50 fictional patients, which makes that distinction concrete rather than
  theoretical.
