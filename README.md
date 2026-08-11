# Daher Health

A health information system for a birth delivery centre and its laboratory in Djibouti.
OpenMRS (patient records, maternity care) + OpenELIS (laboratory) + Keycloak (one login for both)
+ a small adapter that routes lab orders and results between them, all in Docker containers behind
one `docker-compose.yml`.

> ### ⚠️ Status: not yet deployable. Not for use with real patients.
>
> **Build step 1 of 8 is written and not yet verified.** No container in this repository has ever
> been started. The compose file is syntactically valid; nothing beyond that is confirmed.
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

## Running what currently exists (build step 1: OpenMRS only)

```bash
docker compose up -d
docker compose logs -f openmrs-backend
```

First boot takes several minutes — OpenMRS runs its database migrations against an empty schema.
**Watch the logs and wait; do not restart it** because it appears to hang.

Then open <http://127.0.0.1:8080/openmrs/spa>.

The port binds to `127.0.0.1` only. On a remote VM, reach it through an SSH tunnel rather than
opening a firewall port:

```bash
ssh -L 8080:127.0.0.1:8080 user@your-vm
```

Public access arrives at build step 8, with Caddy and HTTPS. There is deliberately no
public route before TLS exists.

### Verifying step 1

1. Log in to OpenMRS.
2. Register a test patient.
3. Search for that patient and open the chart.
4. `docker compose down && docker compose up -d`, then confirm the patient is still there —
   this checks that data actually persists in the volumes.

`OMRS_ADD_DEMO_DATA` defaults to `false` and should stay that way: demo data puts fictional people
in the same tables as real ones.

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
