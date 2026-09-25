# Restore drill — performed 2026-08-13

> **Read this as history, 2026-09-25 (DHEALTH-NATIVE-RECOVERY-001).** This is the
> **2026-08-13 OpenMRS/OpenELIS** drill, for the maternity / reference-connector track. It is kept
> exactly as it was written, including its own two unmet acceptance legs and the tenancy
> re-hardening gate it identified after the fact — that gate is the reason the Native restore
> re-hardens inline and then proves it, and it remains this track's own outstanding item.
>
> It is **not** the Native drill and was never re-run by DHEALTH-NATIVE-RECOVERY-001. The Native
> destructive recovery qualification is `docs/NATIVE-RECOVERY.md` plus `PROJECT_STATE.md`'s
> DHEALTH-NATIVE-RECOVERY-001 entry. This document is not superseded by it; the two cover different
> deployments.

**Ticket:** DH-015 (pinned P0) · **Status: PERFORMED, PARTIAL COVERAGE.** Two acceptance legs could
not be exercised because the data they need does not exist yet. They are named in §6 and the drill
**must be re-run** before pilot-ready is claimed.

This is not "the dump ran". A backup archive was decrypted, restored into an isolated scratch stack,
and the clinical record was then read back **through the application's REST API**, not just out of a
table.

---

## 1. What was restored, and from what

Source archive: `daher-20260813T114224Z.tar.gz.enc`, produced by the real
`backup` service (`--profile backup run --rm`, `BACKUP_REQUIRE_OFFSITE=false`).

- archive sha256 `482e95db29ff9882b4293deac23b67bb02e35286a575e728a48efce7e83ae270`, 355 091 344 bytes
- decrypted with the drill passphrase, then **all seven component SHA-256s re-computed and matched
  against `MANIFEST.txt` independently** — `openmrs.sql`, `clinlims.dump`, `keycloak.dump`,
  `adapter.dump`, `keycloak-realm.json`, `openmrs-data.tar.gz`, `adapter-audit.tar.gz`.

The archive extracts to a nested `run-<TS>-XXXXXX` directory. **The suffix is random** — never
hardcode that path in a restore script; discover it with `find -maxdepth 1 -type d -name 'run-*'`.

## 2. The false green this drill was designed to catch

**The OpenELIS database image ships `clinlims` already populated.** Measured on the scratch stack
*before restoring anything*:

| Check | Before restore (base image alone) | After restore | Live source |
|---|---|---|---|
| `clinlims` tables | **167** | 375 | 375 |
| `clinlims.login_user` | **relation absent** | present | present |
| OpenMRS tables | 0 | full schema | full schema |
| OpenMRS `patient` rows | table absent | **102** | **102** |
| OpenMRS marker `10001MJ` | table absent | **1** | **1** |
| OpenMRS `person_name` rows | table absent | 108 | 108 |
| `keycloak` realm rows | relation absent | 2 (incl. `daher`) | 2 (incl. `daher`) |
| `adapter` `alembic_version` | relation absent | `0001_safety_ledger` | `0001_safety_ledger` |

A drill that asked "does the lab schema exist?" would have **passed against 167 tables that the
restore did not put there**. Every verification below is therefore anchored on state that cannot
exist in a base image. Always record the "before" column; a drill without it proves nothing.

## 3. Two defects this drill found

### 3a. `pg_restore --clean --if-exists` over the pre-populated image is unsafe — do not use it

First attempt, restoring on top of the image's own `clinlims`:

- **213 error lines**, including `cannot drop constraint … because other objects depend on it`,
  `cannot drop schema clinlims because other objects depend on it`, `multiple primary keys for
  table`, and — the serious one — **`insert or update on table …` failures, i.e. rows that did not
  load**. `pg_restore` still exited in a way that a careless script would treat as done.

**Correct procedure — drop and recreate, then restore into an empty database:**

```sh
psql -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                     WHERE datname='$DB' AND pid<>pg_backend_pid();"
psql -d postgres -c "DROP DATABASE IF EXISTS $DB;"
psql -d postgres -c "CREATE DATABASE $DB OWNER $OWNER;"
pg_restore -U postgres -d "$DB" --no-owner --no-privileges "${DB}.dump"
```

Re-run this way, all three databases restored at **exit 0 with zero error lines**.

### 3a-bis. ⚠️ That procedure DISCARDS DH-020 tenant isolation — re-harden, then verify

**This is the most dangerous step in the runbook.** `harden-tenancy.sh` runs at *initdb* time, so
dropping and recreating the three databases throws its work away, and `pg_restore --no-privileges`
does not put the grants back either. Measured directly on a throwaway PostgreSQL 14.4:

```
CREATE DATABASE testdb OWNER tenant;
SELECT has_database_privilege('public','testdb','CONNECT');   -- true
```

…and an unrelated login role then **connected to that database successfully**. A recovery that stops
at "pg_restore exited 0" leaves a system where the `adapter` role can connect to `clinlims`.

**Mandatory, not optional — after every restore:**

1. Re-run the hardening. The live stack already has `openelis-db-hardening` as a separate one-shot
   for exactly this existing-volume case; run that entrypoint against the restored server.
2. **Verify all nine tenancy cases as an acceptance gate**, over TCP with real passwords (a
   `docker exec … psql` without `-h` can pass on local-socket trust and tell you nothing about
   password auth). Each of `clinlims`, `keycloak`, `adapter` must reach its own database and be
   refused by the other two. Include a wrong-password control that is correctly refused.

The restore is **not complete** until that nine-case check passes. This gate was **not** run during
the 2026-08-13 drill — see §6.

### 3b. The restore must not use `pg_isready` alone to decide the database is up

Reproduced DH-024 finding 9 while building the scratch stack: `pg_isready` passes against the
image's **temporary init server**, which then shuts down. Gate on
`test "$(cat /proc/1/comm)" = postgres && pg_isready …`, exactly as the Compose healthcheck does.

Also: `zz-daher-extra-databases.sh` execs `/usr/local/bin/harden-tenancy`. Any scratch or recovery
stack must mount `harden-tenancy.sh` there too, or Postgres init dies with exit 127 **after**
creating the databases — a half-initialised state that looks superficially fine.

## 4. Restoring the data volume is what makes recovery fast

`openmrs-data.tar.gz` (351 MB compressed, 471.9 MB restored) carries OCL, `modules`,
`complex_obs`, `configuration`, and `oauth2.properties`.

| | Time to first HTTP 200 |
|---|---|
| Cold boot, empty database (documented) | **≈ 49 minutes**, dominated by 23 OCL concept-package imports |
| This restore, DB + data volume | **41 seconds** (11:59:26Z → 12:00:07Z) |

Restoring the database **without** the data volume would force the OCL re-import — a ~49 minute
recovery instead of a ~1 minute one. The volume is not optional.

## 5. What was verified, and at which layer

**Only OpenMRS was verified through its application.** OpenELIS and Keycloak were verified by SQL
inspection alone — **neither was ever booted against the restored database.** That distinction
matters, because this ticket's own warning is that a PostgreSQL-only restore returns "a working lab
system and no patient records"; the converse is equally untested here. Both are listed as unmet in
§6, not quietly folded into a pass.

On the restored OpenMRS instance (application layer, via REST):

- configured admin authenticates → `{"authenticated":true, … "System Developer" …}`
- **`Admin123` refused** → `{"authenticated":false}` — DH-019/DH-024's rotation survived the restore
- **the clinical record reads back through REST**:
  `GET /ws/rest/v1/patient?q=10001MJ` → `10001MJ - Test-DH002 Verification`
- `GET /ws/fhir2/R4/metadata` → **200**
- OAuth 2.0 Login 1.5.0 present and `started=false` — SSO stayed **fail-closed** across the restore
Database layer only (no application involved):

- OpenELIS admin bcrypt hash is **byte-identical** to live (`md5` fingerprint
  `beb2a5445494052106e7044cfd36e862`), so DH-024's rotated credential survived; `clinlims.test`
  row counts match live exactly (189)
- Keycloak's `realm` table restored with the `daher` realm present. Note `keycloak-realm.json` was
  **hash-verified but never parsed** — a corrupt-but-correctly-hashed export would pass that check.
  Parse it on the next drill.

## 6. What this drill did NOT cover — the honest boundary

**DH-015 is not `DONE`.** Two acceptance legs from the ticket could not be exercised:

| Unmet leg | Why | Closes when |
|---|---|---|
| **"a known lab result"** | No lab result exists anywhere in the system. `clinlims.analysis`, `.sample`, and `.patient` are all **0 rows** on live and restored alike, because no order has ever been transported (DH-011/DH-012 are blocked). There was nothing to verify. | DH-011/DH-012 produce a real result |
| **"a working SSO login"** | SSO is deliberately fail-closed pending BLOCKER-5's authorization matrix. The drill confirmed the module survives the restore in its stopped state, which is not the same as a login. | BLOCKER-5 resolved and DH-008 activated |
| **OpenELIS and Keycloak booted against the restored database** | Neither application was started; both were checked by SQL only. "The lab system comes back up" is therefore unproven. | Next drill — boot both against the restored data |
| **The §3a-bis tenancy re-hardening gate** | Found *after* the drill ran. The scratch databases were dropped and recreated, so they almost certainly had no cross-tenant isolation, and the nine-case check was not run against them. The defect is in the procedure, now corrected above. | Next drill — re-harden, then pass all nine cases |

Also not covered: **off-site**. This was a local-only restore; no S3-compatible upload has ever run
and no production key is in escrow (DH-014).

⚠️ **A question for Liban, deliberately not answered here.** The lab-result leg could be closed by
hand-entering a result in the throwaway stack — ADR-016 just made manual entry the v1 workflow, so
that is arguably the sanctioned path rather than invented clinical content. It would still write
fabricated clinical values into a database, so it is his call, not an agent's.

**Schedule consequence, stated now rather than in late August:** this drill must be **re-run** once
those two legs can be exercised. Budget for it.

## 7. Isolation and cleanup

The scratch stack used a `daher-restore-*` name prefix, its own volumes, its own network, and
**published no host ports** (live publishes only `127.0.0.1:8080`).

Precisely what was and was not written: **no live database row and no live application volume was
modified**, and the live stack's restart/OOM counters were unchanged (verified before and after).
The one live resource written was the backup output volume `daher-health_backup-data` — the run
wrote `daher-<TS>.tar.gz.enc` and `last-run.status` there, and both were removed during cleanup,
leaving it empty.

⚠️ `docker compose down -v` with the wrong project name destroys the throwaway stack that every
documented verification in this repo rests on. Enumerate with
`docker volume ls --filter name=daher-restore` and read the output before removing anything.

The drill passphrase was generated into a session scratchpad, never written to `.env`, and never
committed. Passphrase, decrypted archive, and every scratch container/volume were destroyed after
verification — so **the archive used here is not recoverable and is not a usable backup**.
