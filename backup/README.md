# Backup sidecar (DH-014)

The sidecar runs `/usr/local/bin/daher-backup` once nightly at `BACKUP_HOUR_UTC`. It covers both
database engines, the live Keycloak realm, OpenMRS data, and the adapter audit volume, then writes
one encrypted archive to the `backup-data` volume and optionally uploads it to approved
S3-compatible storage.

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
