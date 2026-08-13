# Daher Health

A health information system for a birth delivery centre and its laboratory in Djibouti.
OpenMRS (patient records, maternity care) + OpenELIS (laboratory) + Keycloak (one login for both)
+ a small adapter that routes lab orders and results between them, all in Docker containers behind
one `docker-compose.yml`.

> ### ⚠️ Status: infrastructure foundation runs. Not for use with real patients.
>
> **Step 1 (MariaDB + OpenMRS O3)** is API-verified and Liban registered a test patient in the SPA.
> **Step 3 (PostgreSQL + OpenELIS)** is running with its schema and mutual-TLS FHIR endpoint
> verified; configured admin authentication is form-verified, but its browser workflow has not
> been exercised, and an upstream Horiba analyzer test import is blocked
> pending confirmation that the device is in facility scope. **DH-007 Keycloak** is running with
> the French-first realm, roles, clients, secret interpolation, readiness, and restart persistence
> verified.
> **DH-010's adapter safety skeleton** is also healthy with its real database migration and audit
> volume verified; it does not move FHIR messages yet. One-login SSO and the clinical/integration
> steps remain incomplete.
>
> First boot takes **≈50 minutes** and looks like a hang. Read the timing note before running it.
>
> The target state is **pilot-ready**, which is not the same as *clinically live*. Clinically live
> additionally requires the birth centre's clinical lead to sign off on the prenatal lab order set
> and the partograph form fields. **Nobody — human or agent — treats pilot-ready as authorization
> to put real patient data through this system.**
>
> Honest current assessment: [`CURRENT_STATE.md`](CURRENT_STATE.md).

---

## What this system does, and does not do

**Does:** register patients · record clinical observations and labour monitoring data · manage
maternity beds · place lab orders and route them to the laboratory · route results back to the
patient chart · notify the patient that results are ready · authenticate staff once across both
applications · back everything up nightly to off-site storage.

**Does not:** interpret results, score risk, triage, recommend treatment, or generate any clinical
output whatsoever. Every value displayed is a value a human entered. This is a **record-and-route**
system, deliberately, and that boundary is enforced in the project's rules rather than left to
judgement.

French is the primary language throughout — it is Djibouti's working medical and administrative
language, not a localisation afterthought.

---

## Where to look

| File | What it is |
|---|---|
| [`CURRENT_STATE.md`](CURRENT_STATE.md) | What actually works vs. what is merely written. Start here. |
| [`PROJECT_STATE.md`](PROJECT_STATE.md) | Tickets, blockers, critical path, session handoff notes |
| [`DECISIONS.md`](DECISIONS.md) | Architecture decision records — why things are the way they are |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full design: topology, data flow, versions, build order |
| [`backup/README.md`](backup/README.md) | Backup operation, verification boundary, and status checks |
| [`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) | Rules for anyone (human or AI) working in this repo |

---

## Prerequisites

- Ubuntu 24.04 LTS (a fresh VM is the assumed target)
- Docker Engine with the Compose plugin
- 4 vCPU / 16 GB RAM / 60 GB disk is the intended pilot sizing
- Your user must be able to reach the Docker daemon:

  ```bash
  sudo usermod -aG docker $USER   # then log out and back in
  docker info                     # should print server details, not "permission denied"
  ```

  Note that membership in the `docker` group is effectively root on that machine. On a server,
  decide that deliberately rather than by habit.

## Configuration

```bash
cp .env.example .env
chmod 600 .env
```

Then edit `.env` and fill in every value marked `CHANGE ME`. Generate passwords rather than
inventing them:

```bash
openssl rand -base64 30
```

**`.env` is gitignored and must never be committed.** `.env.example` carries placeholders only,
and every variable is documented there with a comment the moment it is introduced.
Keep `.env` mode `0600`: it contains database, application, and OIDC client credentials. The
2026-08-13 quality audit found the local file at `0664` and corrected it to `0600`.

The compose file uses `${VAR:?}` guards, so a missing required value fails loudly at startup
instead of silently falling back to a default password.

OpenELIS password policy is narrower than a general password generator: its admin password must
use only letters, numbers, and `* $ # !`, must be at least seven characters, and must contain at
least one of those four special characters. `.env.example` includes a compliant generation command.
DH-024 verified that the image ignores its documented `DEFAULT_PW`; a post-bootstrap one-shot now
rotates and verifies the database hash before the OpenELIS frontend may start.

### ⚠️ The OpenMRS admin password is a first-boot-only setting

The O3 backend image defaults the `admin` account to the well-known published password
`Admin123`, and it writes that password into the database **the first time OpenMRS initialises an
empty schema**. Changing it afterwards in `.env` does nothing — the account already exists.

DH-019 now passes required `OMRS_ADMIN_PASSWORD` through
`OMRS_CONFIG_ADMIN_USER_PASSWORD`; compose refuses to start if it is missing. This was verified on
an empty volume: the configured password authenticated and `Admin123` was refused. On a database
created before DH-019 landed, `.env` cannot rotate the existing account—change it manually in
OpenMRS.

## Running the current foundation

```bash
docker compose up -d
docker compose logs -f openmrs-backend
```

This starts the currently configured foundation, not the backup scheduler. Backup is deliberately
behind the `backup` profile until its encryption key and approved off-site destination exist:

```bash
docker compose --profile backup up -d backup
```

Do not enable that profile merely to make the container list look complete. DH-014 still requires
production key escrow and approved off-site settings, and DH-015 still requires a performed restore.
Once those settings are provisioned, set `COMPOSE_PROFILES=backup` in `.env`; the normal
one-command `docker compose up -d` then includes the scheduler.

### ⏱ The first boot takes about 50 minutes. This is normal. Do not restart it.

Measured on 2026-08-11, 4 vCPU / 30 GB: **49 minutes** from `up -d` to the first HTTP 200.
A **subsequent** boot on the same volumes took **38 seconds**.

Almost all of that first-boot time is the O3 distribution importing 23 Open Concept Lab packages
(~6 MB zipped, the largest containing 2,186 concepts and 13,472 mappings). While that runs:

- the log goes quiet for long stretches — this is **not** a hang;
- port 8080 is not listening yet, so the browser will refuse to connect;
- after ~30 minutes `docker compose ps` may show the backend as **unhealthy**. Nothing restarts
  it, and it recovers on its own. Restarting it throws away the import and starts over.

To watch real progress rather than guess, count what has actually landed:

```bash
docker exec daher-health-openmrs-db-1 sh -c \
  'mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" openmrs \
   -e "SELECT (SELECT COUNT(*) FROM openconceptlab_import) imports,
              (SELECT COUNT(*) FROM openconceptlab_item) items;"'
```

`imports` climbs towards 23 and `items` towards ~27,000. If both are increasing, it is working.

Wait for readiness with an explicit check rather than by eye:

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:8080/openmrs/ws/rest/v1/session)" = "200" ]; do sleep 15; done; echo READY
```

Then open <http://127.0.0.1:8080/openmrs/spa>.

The port binds to `127.0.0.1` only. On a remote VM, reach it through an SSH tunnel rather than
opening a firewall port:

```bash
ssh -L 8080:127.0.0.1:8080 user@your-vm
```

Public access arrives at build step 8, with Caddy and HTTPS. There is deliberately no
public route before TLS exists.

### Verifying step 1

**What has been verified, and by whom.** The REST/FHIR and persistence checks below were exercised
with `curl` on 2026-08-11. Liban then opened the single-page app and registered a test patient,
closing DH-002. DH-003 later verified French in both frontend environment and backend properties;
Liban has not yet reported whether the rendered UI visibly opens in French.

1. **Log in.** Open <http://127.0.0.1:8080/openmrs/spa> with `admin` and the password configured
   before that database's first boot. Existing pre-DH-019 volumes retain their existing password.
2. **Register a test patient**, then search for them and open the chart.
3. **Check the French default.** The frontend has `SPA_DEFAULT_LOCALE=fr`; DH-003 also sets
   backend `default_locale` and `reporting.defaultLocale` to `fr`, with `fr` first in the allowed
   list. The configuration is verified; the visible browser result still needs your look.
4. **Confirm persistence:**
   ```bash
   docker compose down          # NEVER `down -v` — that deletes the volumes and the patient
   docker compose up -d         # ~38 seconds on an already-populated volume
   ```
   Then search for your patient again. It should still be there.

The same checks from the command line, if you want them:

```bash
# does the API authenticate?
curl -s -u "admin:<configured-password>" http://127.0.0.1:8080/openmrs/ws/rest/v1/session

# is a given patient retrievable by identifier, and through FHIR R4?
curl -s -u "admin:<configured-password>" "http://127.0.0.1:8080/openmrs/ws/rest/v1/patient?q=<IDENTIFIER>"
curl -s -u "admin:<configured-password>" "http://127.0.0.1:8080/openmrs/ws/fhir2/R4/Patient?identifier=<IDENTIFIER>"
```

### ⚠️ Demo patients are an explicit first-boot option

`OMRS_ADD_DEMO_DATA=false` **does not prevent this** — that variable was verified inert at DH-002.
The actual control is `OMRS_DEMO_PATIENTS=off|on`, implemented at the distribution's Initializer
source. It defaults to `off`, the only appropriate value for an instance that may hold real patient
records. `on` deliberately creates 50 fictional people and is for training/demo instances only.

Choose this before first boot. The module zeroes its property after firing and Initializer leaves
an unchanged file alone, but switching an existing database from `off` to `on` changes that file's
checksum and can inject 50 fictional patients. Switching to `off` never removes existing patients.
The original local verification volume predates DH-024's sole-writer correction and now contains
100 fictional patients from two 50-row demo batches, plus two separate deliberate test patients.
It is a throwaway test stack, not a clean pilot database; no patient rows were deleted during the
fix.

`OMRS_DEMO_CONTENT=on|off` is separate. Its default `on` preserves the O3 reference distribution
as shipped, including `BasicLabTests` and other vocabulary that later mapping may need. `off`
selects a locally built backend target whose exact digest-pinned upstream base omits only the
`referenceapplication-demo` source directories. It does not delete concepts or other metadata
already imported into an existing database, and it is not a substitute for clinical sign-off.

Demo patients require a deliberate two-stage setup because the upstream generator runs before its
`OpenMRS ID` metadata is available on a fresh database:

1. Boot once with `OMRS_DEMO_CONTENT=on` and `OMRS_DEMO_PATIENTS=off`; wait for healthy.
2. Change only `OMRS_DEMO_PATIENTS=on` and run `docker compose up -d` again.

DH-024's isolated proof created exactly 50 patients with one creator and reset the sole trigger to
zero. A database preflight rejects first-boot `on/on`; the backend separately rejects
content-off/on. Neither invalid state can silently reset the trigger with zero requested patients.

### Maternity ward and bed counts

`Maternité`, the delivery/postpartum bed types, and the backend French properties are loaded
without admin clicking. Set `MATERNITY_DELIVERY_BEDS` and `MATERNITY_POSTPARTUM_BEDS` in `.env`,
then run `docker compose up -d`. The committed `0/0` values are deliberate placeholders pending
the birth centre's real counts.

The seed is strictly additive (ADR-007): raising a count adds missing beds; lowering it does
not remove, retire, move, or edit anything. If existing ward/type/bed metadata conflicts with the
committed definition, the setup job exits non-zero and the frontend dependency gate stays closed.

## Not yet present

OpenMRS ANC and partograph forms, the prenatal order set,
single sign-on wiring, adapter order/result transport, WhatsApp notifications, the remaining
backup layers **and a tested restore**, and Caddy with HTTPS. Sequence and status:
[`PROJECT_STATE.md`](PROJECT_STATE.md).

Deliberate placeholders — **not gaps to be quietly filled**: maternity bed counts, WhatsApp
credentials (none held yet), the prenatal lab order set, and the partograph form fields. The last
two need clinical sign-off; the first two need real values from the facility.

---

## Security notes

- All patient data stays in this stack's own databases, on the facility's own VM. No patient data
  is sent to any third-party AI/ML service.
- The only outbound patient-adjacent traffic is a content-free WhatsApp notification ("your results
  are ready" — never result values) and the encrypted nightly backup.
- Databases, FHIR endpoints, and the adapter are never publicly routed; only the three application
  subdomains are, and only over HTTPS.
- Every image is version-pinned. No `:latest` tags anywhere.

## Licence

Not yet determined.
