# Current State — Daher Health

**Assessed:** 2026-08-12 · **Target:** pilot-ready by 2026-09-01 (20 days)
**Method:** inspection of the actual repository and running environment, not the plan.

---

## 1. Honest summary

**Step 1 now runs, and running it produced five defects that the syntax check could not.** The
four images have been pulled and pinned by digest; MariaDB and OpenMRS O3 boot; the admin API
authenticates; a test patient was registered, retrieved by name, by identifier, and through the
FHIR R4 endpoint; and the data survived a `down`/`up` cycle.

**What running it cost, and what it exposed** (details in §3a):

1. The gateway could not reach the backend at all — it hard-codes upstream hostnames.
2. The backend healthcheck reported **healthy 8 seconds in**, while the application was unusable.
3. `OMRS_CONFIG_ADD_DEMO_DATA` is inert in this image.
4. **The distro created 50 fictional demo patients** — the hazard `.env.example` warned about,
   reached through a mechanism nobody had identified. See BLOCKER-2 in `PROJECT_STATE.md`.
5. A cold boot takes **≈49 minutes**, nearly all of it importing 23 OCL concept packages.

Verified by an agent, **not yet demonstrated to Liban** — DH-002 is not closed until he runs the
steps himself. Steps 2–8 remain entirely unwritten. This is 1 of 8, now genuinely done rather
than merely written.

---

## 2. What is running

| Service | State |
|---|---|
| MariaDB | **running, healthy, verified** — 10.11.18, digest-pinned |
| OpenMRS backend | **running** — boots, migrates, serves REST + FHIR R4, admin login works |
| OpenMRS frontend | **running** — container healthy; the SPA has **not** been exercised in a browser |
| OpenMRS gateway | **running** — routes to backend and frontend after the alias fix |
| PostgreSQL | **running, healthy, verified** — 14.4 (OpenELIS's image), 3 co-tenant databases |
| OpenELIS webapp | **running** — serves HTTPS 8443; no OpenELIS login performed yet |
| OpenELIS FHIR | **running** — `GET /fhir/metadata` → 200 over mutual TLS. HAPI 6.6.0, 146 resource types |
| OpenELIS frontend | running, **not exercised** — it has no route until Caddy (DH-016) |
| Keycloak | not written — but its **database already exists** (DH-005), so DH-007 is unblocked |
| Adapter | not written |
| WhatsApp notifier | not written |
| Backup | not written |
| Caddy | not written |

⚠️ **This local stack contains 50 fictional demo patients** plus two deliberate test patients (one
registered via the API, one by Liban in the SPA). It is a throwaway verification stack and must
never be confused with a clean instance.

**Build steps 1 and 3 of 8 are done.** Step 2 (DH-003) is deliberately not started — it depends on
BLOCKER-2, which is a human decision. Steps 4–8 remain unwritten, though DH-007 (Keycloak) and
DH-014 (backup) are now unblocked and need no decision.

---

## 3. What is configured but unverified

| Artefact | State | What "unverified" still means here |
|---|---|---|
| `docker-compose.yml` | **Booted and corrected** | Healthchecks, the `mariadb` CLI name, the `${VAR:?}` guards and the backend↔db handshake are all now exercised. The new backend healthcheck's *cold-start* behaviour has been observed on one warm boot only. |
| `.env.example` | Used to boot | Two additions (`OMRS_ADMIN_PASSWORD`, corrected demo-data note) are documented but **not yet wired into compose** — DH-019. |
| Image digests | **Pinned from a real pull, 2026-08-11** | Recorded in `docs/ARCHITECTURE.md` §12. Reproducible now. |
| O3 SPA in a browser | **Not exercised** | Every check so far went through REST/FHIR with `curl`. The single-page app, the French default locale, and the registration form have **not** been opened by a human. This is the gap Liban's own run closes. |
| `docs/ARCHITECTURE.md` | Current | §3's stale "MariaDB 11.4" row corrected to 10.11 at DH-002. |

## 3a. What running it actually exposed

| # | Finding | Evidence | Status |
|---|---|---|---|
| 1 | Gateway 502 on every request — its nginx template hard-codes `http://backend:8080` and `http://frontend`, which do not resolve to our service names | `backend could not be resolved` in the gateway log; `/etc/nginx/templates/default.conf.template` in the image | **Fixed** — network aliases, chosen over renaming services or hand-editing upstream's config (ADR-010 reasoning) |
| 2 | Backend healthcheck reported `healthy` 8s into the first boot, while Liquibase ran and the gateway 502'd | `docker compose ps` vs. a 502 from the same instant | **Fixed** — explicit `= 200` test on the REST session endpoint. `curl -f` was the root cause: it passes on the 302 that URL returns during boot |
| 3 | `OMRS_CONFIG_ADD_DEMO_DATA` is not read by the image | Absent from the `OMRS_*` names in `startup-init.sh` / `startup.sh` | Documented as inert in compose and `.env.example`; **not** removed |
| 4 | **50 fictional demo patients created on first boot** | `SELECT COUNT(*) FROM patient` = 51 after boot; `referencedemodata-2.6.1.omod`; global property `referencedemodata.createDemoPatientsOnNextStartup` | **Open — BLOCKER-2**, human decision. Not acted on: touches patient tables and belongs to DH-003 |
| 5 | Cold boot ≈ 49 min, dominated by 23 OCL concept-package imports | Deployment start 06:36:49Z → first HTTP 200 at 07:26:08Z | Documented; healthcheck timings derived from it |
| 6 | No OpenMRS admin password variable; image defaults to the published `Admin123`, applied **first boot only** | `OMRS_ADMIN_USER_PASSWORD=${OMRS_ADMIN_USER_PASSWORD:-Admin123}` in `startup-init.sh` | **Open — DH-019** |

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

### BLOCKER-2 — The O3 distro creates 50 fictional demo patients · **Human Decision Required: Yes**

Found at DH-002 by running the stack. `OMRS_ADD_DEMO_DATA=false` does not prevent it. Full detail,
including the exact mechanism and what must be decided, is in `PROJECT_STATE.md`. Blocks the pilot
instance, not local work.

### (Not numbered) — `fhir2` Subscriptions vs. polling unresolved · blocks the adapter

See `DECISIONS.md` ADR-005, ticketed as DH-009. Not blocking today, but it gates DH-010 through
DH-012, the longest pole after SSO. Resolve it early, not when the adapter is due.

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

Build steps 2–8 in their entirety: OpenMRS maternity configuration (locales, ward, beds, ANC and
partograph forms), PostgreSQL + OpenELIS, Keycloak and SSO wiring, the adapter and its audit
ledger, the WhatsApp notifier, backup **and a performed restore drill**, Caddy/TLS, and the
`README.md` — which does not exist at all yet, despite being a Definition-of-Done item ("good
enough to hand to someone else").

`AGENTS.md`, `PROJECT_STATE.md`, `DECISIONS.md` are created by this assessment.

---

## 7. Risks to 1 September, specifically

| Risk | Severity | Why | Mitigation |
|---|---|---|---|
| **Restore drill gets squeezed last** | **High** | It is pinned P0 and sits at build step 7, behind the adapter. Deadline pressure compresses whatever is last. | Move backup + restore drill to run in parallel with SSO work, as soon as both databases exist — not after the adapter. It only needs databases, not the full flow. |
| SSO across two apps | High | The classic multi-day sink: realm import, redirect URIs, role mapping, two different OIDC implementations. Cannot be verified piecemeal. | Ticketed as one joint ticket (DH-008), not split. Start it early. |
| ADR-005 resolves to polling | Medium | Adds a scheduler, watermark, and restart semantics to the adapter — days, not hours, and it is safety-relevant code needing human review. | Resolve as a spike (DH-009) before adapter work, not during. |
| O3 first-boot slowness | Medium | Liquibase on an empty database takes minutes; easy to misread as a hang and start "fixing" a working system. | Generous `start_period` already set. Watch logs, don't restart. |
| WhatsApp credentials never arrive | Medium | Real risk given none exist today. | Already handled by design: mock now, `.env`-only swap, ticket stays `BLOCKED`. Does not block anything else. |
| Clinical sign-off not obtained | **High** | Gates *clinically live*, not pilot-ready — but a pilot with no sign-off path is a pilot that cannot proceed to use. | Get the order set and partograph fields in front of the clinical lead as soon as they are drafted, in week 1, not week 3. |
| Version drift on moving tags | Medium | `3.7.x` is a branch tag; a rebuild could silently change the application. | Pin digests at DH-002, immediately after first pull. |

**Schedule reality:** 22 days for eight build steps, with roughly three of them (SSO, adapter,
restore drill) historically prone to consuming multiple days each. There is no slack for
rework caused by skipping verification. Verify each step before moving on, as the build prompt
requires.
