# Current State — Daher Health

**Assessed:** 2026-08-10 · **Target:** pilot-ready by 2026-09-01 (22 days)
**Method:** inspection of the actual repository and running environment, not the plan.

---

## 1. Honest summary

**Nothing is running.** Four commits exist. The stack has never been started, because the Docker
daemon is not reachable from this account (§4). One service group — MariaDB + OpenMRS O3 — is
written and syntactically valid but **entirely unverified**: no image has been pulled, no
container has started, no page has been loaded.

Do not read the tidy repository as progress. What exists is a well-reasoned plan, a validated
compose file, and a set of decisions recorded (`DECISIONS.md`). Step 1 of 8 is written, not done.

---

## 2. What is running

Nothing.

| Service | State |
|---|---|
| MariaDB | not started |
| OpenMRS (backend / frontend / gateway) | not started, images not pulled |
| PostgreSQL | not written |
| OpenELIS | not written |
| Keycloak | not written |
| Adapter | not written |
| WhatsApp notifier | not written |
| Backup | not written |
| Caddy | not written |

---

## 3. What is configured but unverified

Everything written so far falls in this category.

| Artefact | State | What "unverified" means here |
|---|---|---|
| `docker-compose.yml` | `docker compose config` passes | Syntax only. Healthcheck commands, the `mariadb` CLI name in 10.11, the `${VAR:?}` guards, the backend↔db handshake, and O3's 3-to-5-minute first-boot Liquibase run are all untested. |
| `.env.example` | Complete for step 1 | Never used to boot anything. |
| Image tags `3.7.x`, `mariadb:10.11.18` | Chosen from registry listings | Never pulled. **No digests recorded** — the compose file carries a `TODO`, not a fabricated digest. `3.7.x` is a moving branch tag, so until digests are pinned this deployment is not reproducible. |
| `docs/ARCHITECTURE.md` | Current as of today | Three claims already corrected against upstream (gateway container, MariaDB line, notifier placement). Expect more corrections as reality arrives. |

---

## 4. Blockers

### BLOCKER-1 — Docker daemon unreachable · **Human Decision Required: Yes** · blocks everything

`libanbritt` is not in the `docker` group; the socket is `root:docker 0660`. Every pull, build,
and `up` fails with `permission denied`.

Two options, differing in security posture, which is why this is not an agent decision:
- `sudo usermod -aG docker $USER` + re-login — convenient, permanent, and effectively grants root
  on this machine (group members can bind-mount the host filesystem into a container).
- Run docker under `sudo` per-invocation — no standing privilege, friction on every step.

The same question recurs on the pilot VM and deserves a more deliberate answer there.

**This is the critical path.** Nothing after it can start.

### BLOCKER-2 — `fhir2` Subscriptions vs. polling unresolved · blocks the adapter

See `DECISIONS.md` ADR-005. Not startable-blocking today, but it gates DH-010 through DH-012, and
those are the longest pole after SSO. Resolve it early, not when the adapter is due.

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
