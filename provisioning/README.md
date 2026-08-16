# Provisioning API and portal

Creates and manages **facility deployments** of the clinical stack. Hospitals and labs are
independently provisionable; neither implies the other.

> ## ⚠️ This system never touches clinical data
>
> It provisions and monitors: creates facilities, checks health, tracks backup status and image
> versions. It does **not** read, display, derive, or aggregate anything originating from a patient
> record — no patient counts, no cross-facility dashboards, no results, not one summary stat.
>
> A change that would require otherwise is a hard stop, `Human Decision Required: Yes`. That is not
> a style preference: it is the line between a fleet-management tool and a reportable incident.
> See **ADR-021**.

## Status

**Foundation only.** Secret generation and the response schemas exist and are tested. The API
surface, the Compose deploy driver, and the portal are **not built yet**. Nothing here provisions a
real facility today.

Two things are **deliberately incomplete and must not be reported as working**:

- **DNS/TLS** — no domain or DNS provider exists (NEEDS LIBAN #15). Built against a
  provider-agnostic interface, left unconfigured, and reports `dns_configured: false`.
- **Audit log external destination** — the requirement is a log that survives the API host being
  rebuilt or tampered with. The interface will exist before the destination does.

Both report their incompleteness rather than appearing to succeed. A provisioning step that
silently "succeeds" against nothing is a false green, and this project has found five of those.

## Why the API cannot leak clinical data

Three layers, weakest to strongest:

1. **Closed schemas** — every model sets `extra="forbid"`, so a clinical field cannot be attached
   downstream by a dict that happens to carry one.
2. **A vocabulary test** — `tests/test_no_clinical_data.py` walks every model and fails the build if
   a field is named for a patient, encounter, result, specimen, diagnosis and so on.
3. **No clinical database credentials exist in this service at all.** It talks to deployment and
   health surfaces, never to `openmrs` or `clinlims`.

The third is the one that matters. The first two catch mistakes; the third makes the mistake
unreachable — it does not depend on anyone continuing to be careful.

**This is control-tested, not assumed.** Adding a `patient_count` field to `FacilityStatus` makes
two independent tests fail with the ADR-021 explanation. If you change the guard, re-run that
control — a security check nobody has tried to defeat is worth very little.

## Layout

```
provisioning/
  src/daher_provisioning/
    secrets.py       per-facility credential generation
    models.py        facility records + closed API schemas
    facility_env.py  renders and writes a facility's .env
    audit.py         tamper-evident hash-chained audit log
  tests/
    test_secrets.py           generation, uniqueness, redaction
    test_no_clinical_data.py  the structural guarantee
    test_facility_env.py      rendering, permissions, compose sync
    test_audit.py             recording, tamper detection, no secrets
```

## The facility env file

`render_env()` produces everything a facility's stack needs; `write_env()` writes it at mode
`0600`, created restricted from the outset rather than chmod-ed afterwards — a world-readable
window, however brief, is a window in which every credential is readable.

**It refuses to overwrite an existing env file.** A facility's databases are initialised with the
credentials from their first boot. Rewriting the file with fresh secrets does not rotate anything:
it leaves a running stack whose passwords no longer match its own databases, and an
`OMRS_ADMIN_PASSWORD` that was only ever applied on first boot. Rotation is a separate, deliberate
operation, and `overwrite=True` exists for it — not as a retry convenience.

### The sync test

`test_renders_every_variable_the_real_compose_file_requires` parses the actual
`docker-compose.yml` for `${VAR:?}` guards and asserts the renderer produces all of them. When
someone adds a required variable to the clinical stack, **that test fails** rather than a facility
failing at `docker compose up` after it has already been provisioned.

It is control-tested: removing one variable from the renderer makes it fail, naming the exact
variable. Run it with the compose file mounted:

```sh
docker run --rm \
  -v "$PWD/docker-compose.yml:/docker-compose.yml:ro" \
  -v "$PWD/provisioning/src:/app/src:ro" \
  -v "$PWD/provisioning/tests:/app/tests:ro" \
  daher-provisioning-test pytest -q
```

## The audit log

Every provisioning action is recorded **by the code that performed it**, including failures. What
the portal claims happened is not evidence; the API's own record is.

Entries are hash-chained: each carries the hash of the previous one, so editing, deleting,
reordering, or truncating the file breaks the chain and `verify_chain()` reports the first damaged
position. All four of those tampering shapes have their own test — a hash chain nobody has tried to
break is a claim, not a property.

⚠️ **Be precise about what this buys.** A hash chain makes tampering **detectable**, not
**impossible**. An attacker with write access can rewrite the whole file and recompute every hash.
Your actual requirement — a log that survives this host being rebuilt — needs an **external
append-only destination, which does not exist yet** and is marked incomplete rather than pretended.
What the chain gives today is that silent selective edits are caught, and it makes the eventual
external destination a shipper rather than a redesign.

⚠️ **Verifying zero entries is not a pass.** `verify_chain()` returns the number of entries
checked, so callers must check the count. A clean return on an empty log after actions were
performed is itself the finding.

Secrets never enter the log. Entries reference credentials by `credential_set_id`, the model is
closed, and a test asserts no field is named for a password, secret, token, or passphrase.

## Secrets

`generate_facility_secrets()` produces all 16 credentials the clinical stack requires. Every
facility gets its own; none is ever reused.

The generators encode constraints learned by **running** the stack, not by reading documentation:

| Constraint | Why it exists |
|---|---|
| OpenELIS admin password uses only letters, digits and `* $ # !` | A general base64 generator produces values OpenELIS silently rejects |
| OpenMRS password needs upper + lower + digit and must not contain `admin` | OpenMRS enforces this and refuses the account otherwise |
| `FACILITY_ID` is lowercase alphanumeric and hyphens | It becomes a Compose project name |
| Keystore passwords are never empty | An empty value used to select upstream's published `kspass` through a `:-` fallback (DH-021) |

⚠️ **`FacilitySecrets` must never be logged, serialised, or returned.** `__repr__` and `__str__`
are overridden to redact, because the default dataclass repr would print every secret into any
stack trace that touched it. That redaction is tested by formatting the object into a log line and
asserting no value appears — not by trusting the override.

Secrets are written once into a facility's env file and referenced by id afterwards. API responses
carry a **reference**, never a value: returning secrets inline would put them in every access log,
proxy log, and browser history between here and the operator.

## Running the tests

```sh
docker build --target test -t daher-provisioning-test provisioning
docker run --rm daher-provisioning-test pytest -q
docker run --rm -v "$PWD/provisioning:/p:ro" -w /p -e RUFF_CACHE_DIR=/tmp/ruff \
  daher-provisioning-test sh -c 'ruff check src tests && ruff format --check src tests'
```

## Related

- **ADR-020** — topology, DNS strategy, implementation stack
- **ADR-021** — trust boundary and the compromise blast-radius walkthrough
- **DH-026 / DH-027** in [`PROJECT_STATE.md`](../PROJECT_STATE.md)
- [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §16
