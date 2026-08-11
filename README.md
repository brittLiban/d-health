# Daher Health

A health information system for a birth delivery centre and its laboratory in Djibouti.
OpenMRS (patient records, maternity care) + OpenELIS (laboratory) + Keycloak (one login for both)
+ a small adapter that routes lab orders and results between them, all in Docker containers behind
one `docker-compose.yml`.

> ### ⚠️ Status: build step 1 of 8 runs. Not for use with real patients.
>
> **Step 1 (MariaDB + OpenMRS O3) boots and has been verified through the API** on 2026-08-11:
> images pinned by digest, admin login, a test patient registered and retrieved via REST and FHIR,
> and data surviving a `down`/`up` cycle. The single-page app has **not** yet been opened in a
> browser. Steps 2–8 are not written.
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
```

Then edit `.env` and fill in every value marked `CHANGE ME`. Generate passwords rather than
inventing them:

```bash
openssl rand -base64 30
```

**`.env` is gitignored and must never be committed.** `.env.example` carries placeholders only,
and every variable is documented there with a comment the moment it is introduced.

The compose file uses `${VAR:?}` guards, so a missing required value fails loudly at startup
instead of silently falling back to a default password.

### ⚠️ The OpenMRS admin password is a first-boot-only setting

The O3 backend image defaults the `admin` account to the well-known published password
`Admin123`, and it writes that password into the database **the first time OpenMRS initialises an
empty schema**. Changing it afterwards in `.env` does nothing — the account already exists.

As of build step 1 the compose file does not yet pass an admin password through
(`OMRS_CONFIG_ADMIN_USER_PASSWORD`, ticket DH-019), so **this stack currently uses `Admin123`**.
That is tolerable here only because the port binds to `127.0.0.1`. Before the first `up` on any
instance that is not a throwaway, DH-019 must land and the password must be set — after the first
boot the fix is a manual password change, not an `.env` edit.

## Running what currently exists (build step 1: OpenMRS only)

```bash
docker compose up -d
docker compose logs -f openmrs-backend
```

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

**What has been verified, and by whom.** Everything below was exercised on 2026-08-11 through the
REST and FHIR APIs with `curl`. **The single-page app has not been opened in a browser** — that is
the part left for you, and it is the reason DH-002 is not marked done.

1. **Log in.** Open <http://127.0.0.1:8080/openmrs/spa>. See the ⚠️ note above about the admin
   password — on this stack it is the image default, `admin` / `Admin123`.
2. **Register a test patient**, then search for them and open the chart.
3. **Check the French default.** The frontend is configured with `SPA_DEFAULT_LOCALE=fr`. Note
   that the *backend* still reports `locale: "en"` — its allowed-locale list and default are set
   separately, in build step 2 (DH-003). If the SPA does not open in French, that is a DH-003
   finding, not a bug in step 1.
4. **Confirm persistence:**
   ```bash
   docker compose down          # NEVER `down -v` — that deletes the volumes and the patient
   docker compose up -d         # ~38 seconds on an already-populated volume
   ```
   Then search for your patient again. It should still be there.

The same checks from the command line, if you want them:

```bash
# does the API authenticate?
curl -s -u admin:Admin123 http://127.0.0.1:8080/openmrs/ws/rest/v1/session

# is a given patient retrievable by identifier, and through FHIR R4?
curl -s -u admin:Admin123 "http://127.0.0.1:8080/openmrs/ws/rest/v1/patient?q=<IDENTIFIER>"
curl -s -u admin:Admin123 "http://127.0.0.1:8080/openmrs/ws/fhir2/R4/Patient?identifier=<IDENTIFIER>"
```

### ⚠️ This stack boots with 50 fictional demo patients

`OMRS_ADD_DEMO_DATA=false` **does not prevent this** — that variable was verified inert at DH-002.
The demo patients come from the `referencedemodata` module bundled in the O3 distribution image,
driven by an OpenMRS global property this repo does not currently set. On a fresh boot you will
find 50 fictional people (Betty Williams, Michael Turner, …) alongside anything you register — so
a patient count of 50 on an instance where you have registered nobody is expected, not a sign that
something went wrong. They are created once, on the first boot; restarting does not add more.

That is tolerable on a throwaway verification stack and **is not acceptable on the pilot
instance**. Suppressing it is an open decision recorded as BLOCKER-2 in
[`PROJECT_STATE.md`](PROJECT_STATE.md), to be resolved in build step 2. Until then, do not treat
any instance of this stack as clean.

## Not yet present

Build steps 2–8: OpenMRS maternity configuration (ward, beds, ANC and partograph forms),
PostgreSQL + OpenELIS, Keycloak and single sign-on, the adapter and its audit trail, WhatsApp
notifications, backup **and a tested restore**, and Caddy with HTTPS. Sequence and status:
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
