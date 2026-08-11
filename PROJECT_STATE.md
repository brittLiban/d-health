# Project State — Daher Health

**Updated:** 2026-08-10 · **Phase:** Build step 1 of 8 (written, unverified)
**Target:** pilot-ready 2026-09-01 · **Not** clinically live (ADR-012)

Single agent, one ticket at a time. Update this file before ending a session.

---

## Current phase

Step 1 (MariaDB + OpenMRS O3) is written and validates; nothing has ever been started. See
`CURRENT_STATE.md` for the honest assessment.

## Ticket status

| Status | Tickets |
|---|---|
| ✅ Done | DH-001 |
| 🔨 In progress | — |
| ⛔ Blocked | DH-002 (docker access — human) |
| 📋 Ready | DH-009 (spike; no dependency on DH-002) |
| ⏸ Waiting on dependencies | DH-003 … DH-018 |

---

## Blockers

### BLOCKER-1 — Docker daemon unreachable · **Human Decision Required: Yes**

**Ticket:** DH-002 · **Raised:** 2026-08-10
**Problem:** `libanbritt` is not in the `docker` group; every docker command fails with
`permission denied` on `/var/run/docker.sock`. This blocks all verification work.
**Options:** (a) `sudo usermod -aG docker $USER` + re-login — convenient, permanent, effectively
root-equivalent on this machine; (b) per-invocation `sudo` — no standing privilege, more friction.
**Why not an agent decision:** it is a standing privilege grant on Liban's machine, and the same
question recurs with different stakes on the pilot VM.
**Response recorded:** _(none yet)_

---

## Golden end-to-end flow — status

The one flow that makes Day 1 mean anything (sprint spec §8). Demonstrated directly to Liban, not
reported as passing.

| # | Step | Status |
|---|---|---|
| 1 | Register test patient in OpenMRS | ⏸ |
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

### DH-002 · Docker access, first pull, digest pinning, verify OpenMRS · P0 · ⛔ BLOCKED
**Objective:** get step 1 from "written" to "verified", and make the deployment reproducible.
**Depends on:** BLOCKER-1 (human).
**Files:** `docker-compose.yml`, `.env.example`, `README.md` (created here).
**Requirements:** pull the four images; record real digests into the compose file, replacing the
`TODO`; `docker compose up -d`; watch O3's first-boot Liquibase run to completion (minutes — do
not restart it); confirm the healthchecks actually pass rather than merely being defined.
**Acceptance:** OpenMRS reachable at `127.0.0.1:${OMRS_LOCAL_PORT}/openmrs/spa`; admin login
works; a **test patient** can be registered and retrieved; `docker compose down && up` returns to
a working state with data intact; every image pinned by digest.
**DoD:** verification steps written into `README.md` so Liban can repeat them independently.

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

None — nothing has run.

## Notes for next session

- Read `DECISIONS.md` before touching anything. ADR-005 is the live open question; ADR-003,
  ADR-006, ADR-007 are pinned and not to be "simplified".
- **DH-009 needs no docker access.** If BLOCKER-1 is still unresolved, work that instead of idling.
- Nothing here authorizes real patient data. Pilot-ready ≠ clinically live (ADR-012).
