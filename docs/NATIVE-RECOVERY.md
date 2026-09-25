# DHealth Native — backup and recovery

**Ticket:** DHEALTH-NATIVE-RECOVERY-001 · **Decision:** ADR-062 · **Applies to:** `docker-compose.native.yml`, the canonical
Native deployment (ADR-061)

This is the Native track's runbook. The maternity/reference-connector track keeps its own, separate
and unchanged: [`../backup/README.md`](../backup/README.md) and
[`../backup/restore-drill.md`](../backup/restore-drill.md), whose 2026-08-13 drill was performed
against OpenMRS/OpenELIS and is **not** evidence about anything here. Two deployment tracks, two
recovery stories, neither standing in for the other.

---

## 1. What is durable, and what is not

Produced by inspecting the running deployment, not by reading the compose file and assuming. Every
classification below is a decision with a consequence, so each one says what happens if it is wrong.

| State | Where it actually lives | Class | Why |
|---|---|---|---|
| **Exchange database** (`dhealth_exchange`) | `dhealth-db-data` volume | **MUST BACK UP** | Organisations, memberships, role assignments, patients, patient references, provider/lab routes, offerings, result templates, orders, collections, processing state, observations, corrections, releases, **release versions**, amendments, clinician reviews, patient-access grants, notification enrollments/obligations/attempts, Connect installations/qualifications/correlations/obligations, and `exchange_security_audit_events`. Irreplaceable by definition: it is the clinical record. |
| **Keycloak database** (`keycloak`) | the same `dhealth-db-data` volume | **MUST BACK UP** | The user rows. Realm and client *definitions* are reconstructible (below); **who the staff are is not**. Lose this and every `exchange_identities.subject` points at a subject that no longer exists — the clinical data restores and nobody can sign in to read it. |
| **Adapter audit trail** (`audit.jsonl`, `exchange-audit.jsonl`) | `adapter-audit` volume | **MUST BACK UP** | File-backed, so no database dump contains it. `exchange/models.py` classifies it precisely: the authoritative record of security *mutations* is the database table, and this file carries authorization **refusal** telemetry, deliberately non-authoritative because "access denial must never depend on a database write succeeding". Non-authoritative is not disposable — refusal history is exactly what an incident review wants — so it is captured. Observed non-empty after one golden journey. |
| **PostgreSQL roles** (`keycloak`, `adapter`) and their passwords | the cluster, in `dhealth-db-data` | **RECONSTRUCTIBLE** + **OPERATOR-SUPPLIED** | `deploy/native/db-init/init-tenant-databases.sh` recreates both roles on a fresh volume from the env contract. The *passwords* come from the operator. Not in the archive, on purpose: a backup that carried its own database credentials would hand over the deployment to anyone who obtained it. |
| **Database grants / tenant hardening** | the cluster | **RECONSTRUCTIBLE, and actively re-established** | `CREATE DATABASE` grants `CONNECT` to `PUBLIC`, so a restore that only drops and recreates silently un-hardens the deployment — the defect the 2026-08-13 maternity drill found *after* it ran. `native-restore.sh` re-hardens inline and then proves it over TCP. See §6. |
| **Keycloak realm, clients, mappers, roles** | `keycloak/realm-config/dhealth-exchange-realm.json` + `keycloak/reconcile-exchange.sh` | **RECONSTRUCTIBLE** | In Git and applied on every boot. This is why the Native backup takes **no** Keycloak admin REST export, unlike the historical script: the reproducible part is reproduced, and skipping the export keeps client secrets out of the archive entirely. |
| **Alembic schema** | migrations in Git, applied by the adapter entrypoint | **RECONSTRUCTIBLE** | A clean deployment converges an empty database to head with no separate step. The archive records the revision it was taken at and a restore refuses a revision this codebase does not contain. |
| **Caddy data/config** (ACME cache, internal CA) | `caddy-data`, `caddy-config` | **RECONSTRUCTIBLE** | Certificates are re-obtained for a real domain. Verified in the drill: these volumes were destroyed and the deployment came back serving all three vhosts. ⚠️ **Caveat:** on a real domain, re-issuing is subject to the CA's rate limits, and with `BASE_DOMAIN=localhost` Caddy generates a *new* internal CA, so every client must re-trust it. Neither is a data-loss risk; both are an availability one. |
| **Compose topology, Caddyfile, application images** | Git | **RECONSTRUCTIBLE** | The recovery model is "rebuild infrastructure from source control, restore only what is irreplaceable". |
| **Every password, client secret, and the backup passphrase** | the operator's own custody | **OPERATOR-SUPPLIED** | Never in the archive, never in Git, never in a Compose file. ADR-025: a passphrase whose only copy was on the destroyed host is not a disaster-recovery passphrase. |
| Container filesystems, `/tmp`, logs, Keycloak's in-memory cache, connector heartbeat/runtime liveness | — | **EPHEMERAL** | Recreated by starting the deployment. Connector *durable* state (installations, qualifications, correlations, delivery obligations) is in the Exchange database and is backed up; only the derived liveness view is ephemeral. |

**The short version:** two databases and one file volume are backed up. Everything else is either in
Git or in the operator's hands.

---

## 2. What the archive is

One file: `dhealth-native-<UTC timestamp>.tar.gz.enc`.

```
dhealth-native-20260925T162202Z.tar.gz.enc      AES-256-CBC, PBKDF2, salted
  └── (decrypted) tar.gz
        ├── MANIFEST                 format, timestamps, revision, sizes, SHA-256 per component
        ├── exchange.dump            pg_dump --format=custom of the Exchange database
        ├── keycloak.dump            pg_dump --format=custom of the Keycloak database
        └── adapter-audit.tar.gz     the adapter audit volume, numeric ownership preserved
```

**Whole databases, never a table list.** This is the property that makes the inventory above
future-proof: a table added by a later ticket — another notification channel, a Connect table, a new
audit surface — is in the archive the day it exists, with nothing to remember to update. `pg_dump` of
a database, not of the tables this drill happens to name.

**Manifest** (`key=value`, plus one `component` line each). No secret appears in it — no password, no
passphrase, no token, no client secret, no patient-identifying value:

```
format=dhealth-native-backup/1
created_utc=20260925T162202Z
exchange_database=dhealth_exchange
keycloak_database=keycloak
exchange_owner=adapter
keycloak_owner=keycloak
alembic_revision=0032_patient_notifications
postgres_server_version=16.x
exchange_dump_started_utc=...
keycloak_dump_started_utc=...
consistency=per-database-snapshot
component exchange.dump 190800 <sha256>
component keycloak.dump 253502 <sha256>
component adapter-audit.tar.gz 378 <sha256>
```

**Encryption** is `openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_PASSPHRASE` —
the same mechanism ADR-025 established for this repository, unchanged on purpose. The passphrase is
passed by environment, never on a command line (where the host's process table would expose it),
never written to a Compose file, never logged, and never stored in the archive. PBKDF2 runs at
openssl's default iteration count; raising it would change the format and is a decision for a future
ticket, not a refactor.

**Consistency boundary, stated rather than assumed.** Each `pg_dump` is internally consistent — one
repeatable-read snapshot. The two are separate snapshots taken seconds apart, because PostgreSQL has
no cross-database snapshot. Both start timestamps are in the manifest. Clinical operations are **not**
frozen: the only cross-database relationship is "an Exchange identity references a Keycloak subject",
and the worst case in the window is a staff account created between the two dumps, which reappears at
the next backup and blocks nobody's care. Freezing a clinic to close that would trade a real cost for
a theoretical one.

---

## 3. Taking a backup

```bash
# The passphrase comes from your own secret custody, into this shell only.
read -rs BACKUP_ENCRYPTION_PASSPHRASE && export BACKUP_ENCRYPTION_PASSPHRASE

scripts/native/backup.sh              # runs against the LIVE deployment; no downtime
scripts/native/backup.sh --status     # last result + the archives, without decrypting anything
```

**Where archives go.** `NATIVE_BACKUP_DIR`, default `./backup/native-archives` (gitignored). It is a
**bind mount, never a named volume** — an archive stored in a volume of the project it protects dies
with that project, which the drill's own destruction step demonstrates. **In production, point this at
separate storage**, and treat the default as a "works out of the box" convenience rather than a
recovery plan.

**Off-site is required by default and not implemented.** `BACKUP_REQUIRE_OFFSITE` defaults to `true`,
so a production run with no off-site destination ends **FAILED** with its encrypted local archive
retained — the same contract the historical sidecar uses. The Native image carries no uploader, and
setting `BACKUP_S3_BUCKET` produces an explicit failure saying so rather than letting anyone believe a
copy left the host. **This is an open gap, not a completed feature.** The recovery drill sets
`BACKUP_REQUIRE_OFFSITE=false` explicitly, which is why its claim is *local destructive restore
qualified* and never *off-site recovery qualified*.

There is no scheduler for Native yet either. The historical track has one (`backup/scheduler.sh`); the
Native equivalent is a follow-up.

---

## 4. Verifying an archive without restoring it

```bash
scripts/native/restore.sh verify dhealth-native-20260925T162202Z.tar.gz.enc
```

Decrypts to a RAM-backed staging area, refuses any archive member that is not one of four expected
basenames (an allowlist, which is stronger than filtering `..`), checks the format version, requires
all three components — named in the restore script, **not** read from the manifest, so a backup that
forgot one cannot produce a manifest that agrees with itself — independently re-computes every
SHA-256 and byte size, and refuses an Alembic revision this codebase does not contain. Touches no
database. Safe against a live deployment.

---

## 5. Restoring

**Order matters.** The restore refuses a target that still has application sessions, which is the
intended shape: stop the application first.

```bash
read -rs BACKUP_ENCRYPTION_PASSPHRASE && export BACKUP_ENCRYPTION_PASSPHRASE
C="docker compose -f docker-compose.native.yml --env-file .env.native"

# 1. A clean target, built from source control. (After a real loss this is a fresh host:
#    git clone, .env.native from your secret custody, then up.)
$C up -d --wait

# 2. Stop everything that talks to the databases. Only dhealth-db keeps running.
$C stop adapter keycloak caddy daher-ui

# 3. Restore. Destructive: it DROPS both databases before writing.
scripts/native/restore.sh restore dhealth-native-<...>.tar.gz.enc \
    --confirm-destroy-database dhealth_exchange

# 4. Prove tenant isolation BEFORE starting anything. See §6 — this is not optional.
scripts/native/restore.sh verify-tenancy

# 5. Bring the deployment up.
$C up -d --wait
```

**It fails closed on:** a wrong passphrase · a corrupt archive · any component hash or size mismatch ·
a missing required component · an unknown format version · an Alembic revision this codebase does not
contain · an unmounted migration directory · a target database with live sessions · a
`--confirm-destroy-database` that does not name this deployment's Exchange database · an archive whose
manifest names different databases than the deployment being restored into (which is what stops a demo
archive landing on production, or the reverse). In every one of those cases nothing is dropped: all
verification happens in staging and the first statement that can change anything is issued only after
every check has passed.

---

## 6. Tenant isolation is part of the restore, not an afterthought

`DROP DATABASE; CREATE DATABASE; pg_restore` recreates a database with **default** privileges, and
PostgreSQL's default grants `CONNECT` to `PUBLIC`. The maternity drill on 2026-08-13 hit exactly this
and only noticed afterwards (`backup/restore-drill.md` §3). So:

* the restore **re-hardens inline**, immediately after `CREATE DATABASE`, inside the same sequence;
* it **preserves ownership** (never `--no-owner`: the roles exist, and stripping ownership would hand
  every table to the superuser and leave the tenant role unable to write its own data), and then
  verifies that every `public` table is owned by the tenant role;
* it **proves isolation over TCP with real passwords** — six cases, including both wrong-password
  denials. A Unix-socket `trust` connection proves nothing about tenancy: it authorizes by local uid.

**Run `verify-tenancy` between the restore and the next `up`.** `up` re-runs
`dhealth-db-hardening`, which converges the grants — so a restore that failed to re-harden would be
silently fixed by the very next command, and the check would pass for the wrong reason. Both layers
exist deliberately; running the probe in between is what makes the restore's own hardening the thing
under test. NC-RECOVERY-4 in `PROJECT_STATE.md` is that ordering, proven by removing the inline
re-hardening and watching the probe go red.

---

## 7. The drill

```bash
NATIVE_ENV_FILE=demo/native/demo.env \
NATIVE_COMPOSE_EXTRA=docker-compose.native-demo.yml \
scripts/native/qualify-recovery.sh --yes-destroy-this-deployment
```

It refuses to run without that flag, and refuses `.env.native` entirely unless
`NATIVE_RECOVERY_DRILL_ALLOW_PRODUCTION=1` is also set. Twenty steps: validate → clean deployment →
real-browser golden journey → patient access + notification + synthetic Connect state → record
sentinels → encrypted backup → prove the archive is encrypted and no plaintext remains → **`down -v`,
destroying every volume** → prove the volumes are gone and the archive is not → clean deployment from
source control → **prove the clean target does not contain the source state** → stop the application →
restore → prove tenancy → start → prove the same order, release version, review, identities, audit
event and patient grant returned, through a real browser → **perform a whole new clinical workflow** →
re-prove the restored release did not change.

**Recovery sentinels are the defence against the obvious false green.** The demo baseline seeds
organisations and a patient with *fixed names*, so "a patient called Patient Démo Alpha is on screen"
proves nothing after a restore. Every "same record" claim is keyed on identifiers captured before the
backup — order id, release version id, review id, patient id, the Keycloak `user_entity.id` values,
and a SHA-256 fingerprint of the released observations — asserted **absent** on the clean target and
**present** afterwards. The Keycloak user ids are the check that separates *restored identity state*
from *a reconciler having run again*: the clean target's `keycloak-demo-users` one-shot creates the
same three usernames with brand-new ids, and only a restore brings the original ids back.

**Plaintext.** Staging is a `tmpfs` in both services and the containers are `run --rm`, so a decrypted
dump never reaches a disk and cannot outlive the run even on a crash. `umask 077` throughout, cleanup
trapped on every exit path. After teardown the backup directory holds nothing but `*.tar.gz.enc` and
`last-run.status` — the drill asserts that, and also that the archive begins with an openssl salt
header and contains no recognisable SQL.

---

## 8. What is qualified, and what is not

**Qualified (DHEALTH-NATIVE-RECOVERY-001):** local encrypted backup → destruction of every durable
volume → restore into a clean deployment → real Keycloak Authorization Code + PKCE login by the
pre-backup identity → the same clinical records found through the real product → tenant isolation
intact over TCP → a new clinical workflow completed afterwards.

**Not qualified, and not claimed:**

* **Off-site recovery.** No archive has ever left this host. §3.
* **Scheduled backup for Native.** No scheduler exists; every backup so far was invoked by hand.
* **A production deployment.** The drill ran on `BASE_DOMAIN=localhost` with Caddy's internal CA, on
  one workstation, against the demo overlay's synthetic identities.
* **Restore at realistic data volume.** The archive was ~118 KB. Nothing here says anything about how
  long a clinic-year of data takes, and the `tmpfs` staging size (1 GiB) is a pilot-scale assumption.
* **Passphrase rotation, or re-encrypting existing archives.** Not built.
* **Human usability, clinical sign-off, real provider messaging, Connect vendor qualification.**
  Untouched by this ticket. ADR-003's WhatsApp gate stays `BLOCKED` — the drill's notification state
  was created on the `fake` provider, which has no network code at all.

**Measured timings** are in `PROJECT_STATE.md`'s DHEALTH-NATIVE-RECOVERY-001 entry. They are one run
on one workstation with a tiny dataset: **qualification measurements, not an RTO**, and setting an
operational target needs a realistic dataset and a real host.
