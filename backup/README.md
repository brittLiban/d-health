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
upload was deliberately disabled and no test archive was retained. **No restore has been
performed.** DH-015 remains the pinned pilot-ready gate; this directory must not be described as a
working backup system until that restore is actually run and documented.
