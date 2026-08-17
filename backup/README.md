# Backup sidecar (DH-014)

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
