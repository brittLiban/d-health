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
    secrets.py    per-facility credential generation
    models.py     facility records + closed API schemas
  tests/
    test_secrets.py           generation, uniqueness, redaction
    test_no_clinical_data.py  the structural guarantee
```

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
