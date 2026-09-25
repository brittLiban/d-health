# Backup sidecar (DH-014)

> **Scope, clarified 2026-09-25 (DHEALTH-NATIVE-RECOVERY-001).** This directory is the
> **maternity / reference-connector** track's backup: MariaDB, OpenMRS, OpenELIS's `clinlims`, and a
> live Keycloak realm export. It is unchanged by that ticket and still describes its own deployment
> accurately.
>
> **DHealth Native has its own, separate backup and recovery:** `docs/NATIVE-RECOVERY.md`, with its
> own tooling under `deploy/native/backup/` and `scripts/native/`. Two deployment tracks (ADR-061),
> two recovery stories, and **neither is evidence about the other**. In particular the performed
> destructive drill recorded for Native says nothing about this one.

When started with production encryption and off-site settings, the sidecar runs
`/usr/local/bin/daher-backup` once nightly at `BACKUP_HOUR_UTC`. It covers both
database engines, the live Keycloak realm, OpenMRS data, and the adapter audit volume, then writes
one encrypted archive to the `backup-data` volume and optionally uploads it to approved
S3-compatible storage.

The scheduler is in Compose's opt-in `backup` profile so an unconfigured default
`docker compose up -d` cannot create a credential-failure restart loop. After the production
encryption key and approved off-site destination are configured, start it with:

```bash
docker compose --profile backup up -d backup
```

For the pilot deployment, set `COMPOSE_PROFILES=backup` in `.env` after those settings exist. The
normal `docker compose up -d` then starts the complete configured stack in one command.

`BACKUP_REQUIRE_OFFSITE=true` is the pilot default. A missing or failed upload marks the run
`FAILED` even when an encrypted local archive exists. Set it to `false` only for explicit local
verification; that does not satisfy the off-site requirement.

Check the latest result without exposing archive contents:

```bash
docker compose exec backup cat /backup/last-run.status
docker compose logs --tail 100 backup
```

Run one cycle manually:

```bash
docker compose run --rm backup /usr/local/bin/daher-backup
```

Never put the encryption passphrase in a command or commit it. Keep a recoverable copy outside the
VM; losing the VM and the only copy of its passphrase makes every off-site archive useless.

## Verification boundary

The complete local path passed on 2026-08-13, including archive encryption and read-back. External
upload was deliberately disabled and no test archive was retained, so **off-site is still
unexercised** and DH-014 is not closed.

**A restore has now actually been performed** (DH-015, 2026-08-13 — see
[`restore-drill.md`](restore-drill.md)): the archive was decrypted, all seven manifest hashes were
re-verified independently, the data was restored into an isolated scratch stack, and patient
`10001MJ` was read back **through the OpenMRS REST API** rather than out of a table. It found two
real defects, which is what a drill is for.

⚠️ **Performed is not done.** Two acceptance legs could not be exercised because the data does not
exist yet — a known lab result and a working SSO login — so the drill **must be re-run** once
DH-011/DH-012 and SSO activation land. Until then this directory is a verified local backup path
with a partially verified restore, and must not be described as a working backup system.

## Who holds the passphrase (ADR-025)

For a facility provisioned through the control plane, the encryption passphrase is **supplied by the
operator at host bootstrap**, never generated on the facility host. A passphrase generated on the
machine being backed up dies with that machine — the exact disaster backups exist for — and every
off-site archive becomes permanently undecryptable. Supplying it at bootstrap means it exists in at
least two places by construction.

For the same reason, backup is **scheduled only when it can succeed**: a passphrase *and* an
off-site destination. Absent either, the scheduler is not enabled and the facility reports having no
backup **as a problem**, rather than quietly producing local-only archives beside the data they are
meant to protect.

**This is wired end to end (DH-027G, 2026-08-21).** The operator places both values in the facility
agent's own process environment at bootstrap; the agent forwards them into this facility's env file
during `PROVISION` (never before, never by rewriting — `BACKUP_ENCRYPTION_PASSPHRASE` is
unrotatable, see `facility_env.UNROTATABLE`). See `provisioning/README.md`'s "Backup wiring" section
for the exact variables and what `/facilities/{id}/status` reports.

**The nightly scheduler's own result now reaches the control plane too (DH-027H, 2026-08-21).**
`STATUS` reads `last-run.status` the same way an operator already could by hand —
`docker compose exec backup cat /backup/last-run.status`, documented just above — through one
more fixed, read-only command the agent runs, never a caller-supplied one. Parsing is strict:
only a well-formed `<STATE> <TIMESTAMP>` line is ever read as evidence; anything else becomes
`NEVER_RUN`. See `provisioning/README.md`'s matching section for the full design.

**`status()` now writes atomically.** The old `printf ... > "$STATUS_FILE"` truncates before
writing, so a reader landing in that window would see an empty file — tolerable when the only
reader was a human running `cat` occasionally, not once the control plane reads this file too.
Now written to a `.tmp` file and `mv -f`'d into place, a `rename(2)` and therefore atomic on
POSIX: a reader always sees either the complete previous status or the complete new one.
