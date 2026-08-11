# Project State — Daher Health

**Updated:** 2026-08-11 · **Phase:** Build step 1 of 8 (**runs; verified via API, awaiting Liban**)
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
| ✅ Done | DH-001 |
| 🟡 Verified by agent — **awaiting Liban's own run** | **DH-002** |
| 🔨 In progress | DH-017 (README — now covers step 1 fully, incl. boot timing and the demo-patient warning) |
| 📋 Ready — **do this next** | DH-009 (spike; needs no Docker) |
| 🆕 New, from DH-002 | DH-019 (admin password wiring) |
| ⏸ Waiting on dependencies | DH-003 … DH-018 |

## ▶ START HERE (next session)

1. **Get Liban's own run of DH-002 done first — it is the only thing standing between DH-002 and
   ✅.** Give him the URL, the timing warning, and the four verification steps in `README.md`.
   Everything so far went through `curl`; the O3 single-page app has never been opened in a
   browser, so the SPA, the French default locale, and the registration form are unproven.
2. **Put BLOCKER-2 in front of him at the same time** — the distro creates 50 fictional patients
   on first boot, and `OMRS_ADD_DEMO_DATA=false` does not stop it. It is not urgent for a local
   stack; it is a hard gate before the pilot VM is built. It shapes DH-003.
3. **Then DH-009** (ADR-005 spike). It needs no Docker, it gates the longest pole after SSO, and
   the stack can stay up while it is worked.

**If you restart the stack:** a cold boot is ~50 minutes, a warm boot ~38 seconds. Never
`docker compose down -v` — that destroys the volumes and the evidence.

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
| 1 | Register test patient in OpenMRS | 🟡 done via REST/FHIR by the agent; **not yet demonstrated to Liban in the SPA** |
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

### DH-002 · First pull, digest pinning, verify OpenMRS · P0 · 🟡 VERIFIED BY AGENT — awaiting Liban
**Worked:** 2026-08-11.

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

### DH-019 · Wire the OpenMRS admin password through compose · P0 · 📋 READY
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

### DH-009 · Spike: resolve ADR-005 (Subscriptions vs. polling) · P0 · 📋 READY
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

### DH-005 · PostgreSQL + OpenELIS standalone · P0 · ⏸ (needs DH-002)
OpenELIS webapp, FHIR, frontend, DB image; drop upstream's `certs`/`proxy` (Caddy handles TLS).
Add Keycloak + adapter databases and roles to the same Postgres (ADR-001).

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
