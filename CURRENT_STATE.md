# Current State — Daher Health

**Assessed:** 2026-08-13 · **Target:** pilot-ready by 2026-09-01
**Method:** inspection of the actual repository and running environment, not the plan.

---

## 1. Honest summary

**Build steps 1 and 3 now run, and running them produced nine defects the syntax check could not.**
Step 1 (MariaDB + OpenMRS O3) is verified through REST and FHIR **and by Liban himself in the SPA**.
Step 3 (PostgreSQL + OpenELIS) is verified: three co-tenant databases, a 167-table OpenELIS schema,
the webapp on HTTPS, and a FHIR server answering over mutual TLS. Every image is digest-pinned.

The **DH-007 Keycloak foundation also runs and is runtime-verified**: realm import, French default,
roles, clients, environment-resolved secrets, readiness, and restart persistence. Application SSO
is still absent; DH-008 must wire and demonstrate one login across both applications.

**DH-010 now runs as well:** the adapter's transport-neutral safety skeleton migrated its real
PostgreSQL database and is healthy with a durable unique ledger, dead-letter table, private audit
volume, transient-only retry policy, and dependency-aware readiness. It does not move FHIR data yet.

**What running it cost, and what it exposed** (details in §3a):

1. The gateway could not reach the backend at all — it hard-codes upstream hostnames.
2. The backend healthcheck reported **healthy 8 seconds in**, while the application was unusable.
3. `OMRS_CONFIG_ADD_DEMO_DATA` is inert in this image.
4. **The distro created 50 fictional demo patients** — the hazard `.env.example` warned about,
   reached through a mechanism nobody had identified. See BLOCKER-2 in `PROJECT_STATE.md`.
5. A cold boot takes **≈49 minutes**, nearly all of it importing 23 OCL concept packages.

Step 3 added four more (DH-005's ticket entry): `certs` cannot be dropped as the architecture
claimed; the webapp needs a Docker secret or its context dies while Tomcat still reports success;
the FHIR container ignores `JAVA_OPTS`; and its FHIR endpoint requires **mutual TLS**.

**The honest boundary:** substantial buildable work remains. No OpenELIS login has been performed;
its frontend is unexercised until Caddy (DH-016); SSO, maternity configuration, adapter transport,
notifications, complete backup/restore, and public TLS remain incomplete. Human decisions are
collected under **NEEDS LIBAN** in `PROJECT_STATE.md`.

---

## 2. What is running

| Service | State |
|---|---|
| MariaDB | **running, healthy, verified** — 10.11.18, digest-pinned |
| OpenMRS backend | **running** — boots, migrates, serves REST + FHIR R4, admin login works |
| OpenMRS frontend | **running** — container healthy; Liban registered a test patient in the SPA |
| OpenMRS gateway | **running** — routes to backend and frontend after the alias fix |
| PostgreSQL | **running, healthy, verified** — 14.4 (OpenELIS's image), 3 co-tenant databases |
| OpenELIS webapp | **running** — serves HTTPS 8443; no OpenELIS login performed yet |
| OpenELIS FHIR | **running** — `GET /fhir/metadata` → 200 over mutual TLS. HAPI 6.6.0, 146 resource types |
| OpenELIS frontend | running, **not exercised** — it has no route until Caddy (DH-016) |
| Keycloak | **running, healthy, realm verified** — DH-007 complete; pilot session lifetimes approved (ADR-013) |
| Adapter | **running, healthy, DH-010 verified** — ledger/dead-letter/retry/audit foundation only; no FHIR transport yet |
| WhatsApp notifier | not written |
| Backup | **local path verified end-to-end** — both engines, realm, volumes, manifest, encryption, scheduler; off-site upload unverified; no retained test archive; no restore performed |
| Caddy | not written |

⚠️ **This local stack contains 50 fictional demo patients** plus two deliberate test patients (one
registered via the API, one by Liban in the SPA). It is a throwaway verification stack and must
never be confused with a clean instance.

**Build steps 1 and 3 are done; the Keycloak half of step 4 is built and verified.** Step 2
(DH-003) has not started but its demo-patient blocker is resolved. SSO and the remaining steps are
not complete.

---

## 3. What is configured but unverified

| Artefact | State | What "unverified" still means here |
|---|---|---|
| `docker-compose.yml` | **Booted and corrected** | Healthchecks, the `mariadb` CLI name, the `${VAR:?}` guards and the backend↔db handshake are all now exercised. The new backend healthcheck's *cold-start* behaviour has been observed on one warm boot only. |
| `.env.example` | Used to boot | `OMRS_ADMIN_PASSWORD` is now wired and **verified on an empty volume** (DH-019). The OpenELIS block is wired and booted. |
| Image digests | **Pinned from a real pull, 2026-08-11** | Recorded in `docs/ARCHITECTURE.md` §12. Reproducible now. |
| O3 SPA in a browser | **Exercised by Liban 2026-08-11** | He registered a patient through the SPA. The French default locale was **not** reported on either way — still open, and a DH-003 concern. |
| OpenELIS UI | **Not exercised** | No OpenELIS login has been performed. Its frontend has no route until Caddy (DH-016). |
| OpenELIS FHIR *delivery* | **Advertised, not proven** | Its CapabilityStatement offers `Subscription` with `create`, but no subscription has been created and no delivery observed — see ADR-005 Q4. |
| Keycloak realm | **Runtime verified** | Provider foundation only. Pilot session lifetimes are approved (ADR-013); OpenMRS/OpenELIS SSO wiring is DH-008. |
| `docs/ARCHITECTURE.md` | Current | §3's stale "MariaDB 11.4" row corrected to 10.11 at DH-002. |

## 3a. What running it actually exposed

| # | Finding | Evidence | Status |
|---|---|---|---|
| 1 | Gateway 502 on every request — its nginx template hard-codes `http://backend:8080` and `http://frontend`, which do not resolve to our service names | `backend could not be resolved` in the gateway log; `/etc/nginx/templates/default.conf.template` in the image | **Fixed** — network aliases, chosen over renaming services or hand-editing upstream's config (ADR-010 reasoning) |
| 2 | Backend healthcheck reported `healthy` 8s into the first boot, while Liquibase ran and the gateway 502'd | `docker compose ps` vs. a 502 from the same instant | **Fixed** — explicit `= 200` test on the REST session endpoint. `curl -f` was the root cause: it passes on the 302 that URL returns during boot |
| 3 | `OMRS_CONFIG_ADD_DEMO_DATA` is not read by the image | Absent from the `OMRS_*` names in `startup-init.sh` / `startup.sh` | Documented as inert in compose and `.env.example`; **not** removed |
| 4 | **50 fictional demo patients created on first boot** | `SELECT COUNT(*) FROM patient` = 51 after boot; `referencedemodata-2.6.1.omod`; global property `referencedemodata.createDemoPatientsOnNextStartup` | **Resolved by decision** — explicit `OMRS_DEMO_PATIENTS=off|on` distribution-source overlay, default `off` |
| 5 | Cold boot ≈ 49 min, dominated by 23 OCL concept-package imports | Deployment start 06:36:49Z → first HTTP 200 at 07:26:08Z | Documented; healthcheck timings derived from it |
| 6 | No OpenMRS admin password variable; image defaults to the published `Admin123`, applied **first boot only** | `OMRS_ADMIN_USER_PASSWORD=${OMRS_ADMIN_USER_PASSWORD:-Admin123}` in `startup-init.sh` | **Fixed and verified on an empty volume — DH-019** |

Two upstream oddities observed and deliberately not chased, recorded so they are not later
mistaken for new breakage: `AddressConfigurationLoader` logs an ERROR that the distro's own
`addressConfiguration.xml` "appears invalid" and skips it; and the gateway config carries a
`/grafana → grafana:3000` block for a service this stack does not run (relevant at DH-016 — it
should not be exposed publicly).

---

## 4. Blockers

**Blocker numbering lives in `PROJECT_STATE.md`.** This section summarises; that file is the
source. (Earlier revisions of this file numbered blockers independently, which produced two
different "BLOCKER-2"s. Fixed here — do not renumber in this file again.)

### BLOCKER-1 — Docker daemon unreachable · ✅ RESOLVED 2026-08-10

`libanbritt` was not in the `docker` group. Resolved by `usermod -aG docker $USER` plus a
logout/reboot. Docker has since pulled images and run the full stack, so this is confirmed
resolved by use, not just by inspection.

**Still live as a question for the pilot VM:** `docker` group membership is effectively root on
the host. Decide it deliberately there rather than by habit.

### BLOCKER-2 — The O3 distro creates 50 fictional demo patients · ✅ RESOLVED BY DECISION 2026-08-12

`OMRS_DEMO_PATIENTS` now selects an explicit `off` (default) or `on` Initializer overlay at the
distribution source. This is separate from the inert `OMRS_ADD_DEMO_DATA` variable. See
`PROJECT_STATE.md` for the mechanism and the remaining separate demo-concept-content work in
DH-003.

### (Not numbered) — ADR-005 · ✅ ACCEPTED 2026-08-12 · adapter skeleton unblocked

Both legs have primary evidence (DH-009a, DH-009b), and Liban approved the custom adapter with a
configurable 15-second OpenMRS order poll, inclusive watermark, and durable ledger. OpenELIS runs a
full HAPI server that advertises `Subscription`, but delivery and corrected-result behavior still
must be demonstrated before trusting the result path. DH-010 built and runtime-verified the safety
foundation; the watermark and poller themselves remain DH-011.

---

## 5. Known placeholders — deliberate, not gaps to fill

**These are decided placeholders. An agent that "helpfully" fills them in is violating §5 of the
sprint spec, not being useful.**

| Placeholder | Status | Who resolves |
|---|---|---|
| Maternity bed counts | `.env` config, additive-only seed (ADR-007). Committed defaults are placeholders, marked as such. | Liban, from the birth centre |
| WhatsApp credentials | None held. Mock build; ticket stays `BLOCKED` not `DONE` (ADR-003). | Liban |
| Prenatal lab order set | Not yet written. Must be unmodified WHO/OpenELIS reference defaults, marked as needing sign-off. | Clinical lead |
| Partograph form fields | Not yet written. Data-entry only, no decision support (ADR-006). | Clinical lead |

---

## 6. What is genuinely missing

OpenMRS maternity configuration (locales, ward, beds, ANC and partograph forms); prenatal panel;
SSO wiring for both applications; adapter order/result transport; WhatsApp notifier; the
remaining backup layers **and a performed restore drill**; Caddy/TLS; the golden demonstration;
and a README complete enough to hand the stack to another operator.

---

## 7. Risks to 1 September, specifically

| Risk | Severity | Why | Mitigation |
|---|---|---|---|
| **Restore drill gets squeezed last** | **High** | It is pinned P0 and sits at build step 7, behind the adapter. Deadline pressure compresses whatever is last. | Move backup + restore drill to run in parallel with SSO work, as soon as both databases exist — not after the adapter. It only needs databases, not the full flow. |
| SSO across two apps | High | The classic multi-day sink: realm import, redirect URIs, role mapping, two different OIDC implementations. Cannot be verified piecemeal. | Ticketed as one joint ticket (DH-008), not split. Start it early. |
| OpenMRS order polling safety | Medium | The accepted scheduler, watermark, ledger, and restart semantics are safety-relevant code — days, not hours. | ADR-005/DH-009 are complete; implement and test the accepted design in DH-010/011. |
| O3 first-boot slowness | Medium | Liquibase on an empty database takes minutes; easy to misread as a hang and start "fixing" a working system. | Generous `start_period` already set. Watch logs, don't restart. |
| WhatsApp credentials never arrive | Medium | Real risk given none exist today. | Already handled by design: mock now, `.env`-only swap, ticket stays `BLOCKED`. Does not block anything else. |
| Clinical sign-off not obtained | **High** | Gates *clinically live*, not pilot-ready — but a pilot with no sign-off path is a pilot that cannot proceed to use. | Get the order set and partograph fields in front of the clinical lead as soon as they are drafted, in week 1, not week 3. |
| Version drift on moving tags | Medium | `3.7.x` is a branch tag; a rebuild could silently change the application. | Pin digests at DH-002, immediately after first pull. |

**Schedule reality:** 22 days for eight build steps, with roughly three of them (SSO, adapter,
restore drill) historically prone to consuming multiple days each. There is no slack for
rework caused by skipping verification. Verify each step before moving on, as the build prompt
requires.
