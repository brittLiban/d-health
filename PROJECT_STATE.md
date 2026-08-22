# Project State — Daher Health

**Updated:** 2026-08-21 · **Phase:** provisioning control plane works end to end on a stand-in;
DH-011 transport is **blocked on BLOCKER-6** (target ingestion path — a human decision), not the
unblocked critical path this line claimed until now (stale since BLOCKER-6 was raised 2026-08-16,
the day after this line was written)

**⚠️ Recorded schedule trade, made deliberately by Liban 2026-08-15.** The provisioning API and
portal are being built *before* DH-011→012→013. The consequence, stated once and accepted: the
order/result transport chain and therefore the **golden end-to-end flow will very likely not be
demonstrated by 1 September**. This is a decision, not an oversight — it is recorded here so nobody
later reads the missed date as a delivery failure.
**Target:** pilot-ready 2026-09-01 · **Not** clinically live (ADR-012)

Single agent, one ticket at a time. Update this file before ending a session.

---

## Current phase

**Step 1** (MariaDB + OpenMRS O3) boots and works — verified through REST and FHIR, and by Liban
himself in the SPA. **Step 3** (PostgreSQL + OpenELIS) has its runtime foundation verified: three
co-tenant databases, 375 base tables in the fully migrated OpenELIS `clinlims` schema, webapp on
HTTPS, and a FHIR server answering `metadata` over mutual TLS. **DH-019** closed the published
default admin password. BLOCKER-4 records an upstream analyzer configuration import that is not
verified.

**DH-008's fail-closed protocol plumbing is built and runtime-verified.** OpenMRS has official
OAuth2 Login 1.5.0 built from its exact release commit and installed into both fresh and existing
data volumes; OpenELIS uses its working upstream SAML path. Keycloak fresh definitions and the
existing-realm reconciliation agree on OpenMRS OIDC and OpenELIS SAML. The reconciliation passed
twice and the normal stack is healthy with `SSO_ENABLED=false`, preserving local logins. Activation
and the one-login demonstration are blocked by BLOCKER-5: native application-role mapping and the
OpenMRS-provider rule require Liban's approval.

The running stack surfaced multiple findings that `docker compose config` could never have caught
(`CURRENT_STATE.md` §3a and DH-005's ticket entry). Buildable work remains alongside the decisions
gathered under **NEEDS LIBAN** below.

**DH-003 is complete and empty-volume verified.** French backend locales, the `Maternité`
admission ward, both decided bed types, the explicit demo-content switch, and an additive-only
bed seed are configuration as code. The real bed counts remain a facility placeholder rather
than something an agent may invent. The SPA's visible language still needs a human browser look;
the backend properties and frontend environment are verified, but that observation is not inferred.

**Quality verification 2026-08-13:** the current Compose stack passed config validation; all ten
long-running project services had zero restarts and zero OOM kills; OpenMRS REST/FHIR, OpenELIS
webapp, OpenELIS FHIR mutual TLS, the OpenELIS frontend's internal HTTP endpoint, Keycloak, and the
adapter readiness path answered. The adapter test image again passed dependency consistency, Ruff,
and all 14 tests. Shell syntax, realm JSON, OpenMRS XML, Git integrity/whitespace, tracked-secret
patterns, image-tag policy, and every relative Markdown link passed static checks. A completed
two-container `daher-demotest` password-verification stack was found and stopped without deleting
its containers or volumes. The backup scheduler remains intentionally absent from the running
stack until production encryption/off-site settings are supplied; DH-015 was unperformed at that
time — it has since been **performed 2026-08-13**, see the DH-015 ticket.

**Deep quality verification 2026-08-13:** both local images rebuilt; adapter dependency consistency,
Ruff lint/format, and all 14 tests passed. OpenMRS REST/FHIR and configured admin authentication,
OpenELIS HTTPS/FHIR mutual TLS/frontend, Keycloak discovery/realm, adapter readiness, all nine
PostgreSQL role/database cases, all container timezones, and aggregate database state passed.
Every running/setup container had zero restarts and zero OOM kills. A fresh local-only encrypted
backup captured all seven required components and passed decrypt/manifest/schema/data read-back;
its archive and status marker were removed because the ephemeral key was not retained. This was
not an off-site test or restore. The audit also corrected the local `.env` from mode `0664` to
`0600`, rotated the old live OpenMRS volume away from `Admin123`, completed DH-022, and raised
BLOCKER-4 for the reproducible OpenELIS analyzer import conflict.

**DH-003 verification 2026-08-13:** exact Initializer 2.12.0 and bedmanagement 7.2.0 source was
checked before selecting the native REST seam. Five seed safety tests plus Ruff lint/format passed.
The live throwaway stack demonstrated same/increase/lower/zero count behavior, and an isolated
empty-volume stack demonstrated no-click first boot with demo patients/content off. The normal
`docker compose up -d` then completed with all ten long-running services healthy/running as
applicable, all three setup jobs exited 0, and every container reported zero restarts and zero OOM
kills. Exact evidence and the remaining visible-browser-language check are in DH-003 below.

**DH-024 fresh-deployment audit 2026-08-13:** found and fixed three paths that looked healthy but
were unsafe on real or empty volumes. OpenMRS had two writers for the demo-patient trigger and this
throwaway database received a second 50-row batch; upstream's trigger is now removed from the local
derived base so `patients.on/off.xml` is authoritative. A direct first-boot `on/on` also proved the
upstream generator runs before its metadata is ready; the new read-only preflight refuses that
state, while the enforced two-stage path produced exactly 50 patients. OpenELIS PostgreSQL
readiness now rejects the temporary init server, and a post-webapp password job replaces/verifies
the ignored published admin hash before the frontend starts. The exact empty-volume OpenELIS graph
passed: database healthy, hardening 0, webapp healthy, password job 0, frontend running, configured
form login accepted, `adminADMIN!` refused. No existing patient row, laboratory test, or analyzer
mapping was changed.

A fresh local-only encrypted backup also passed decrypt, seven-entry manifest/hash validation,
three `pg_restore --list` reads, MariaDB patient DDL/data checks, Keycloak JSON parsing, and both
volume archive reads; the ephemeral-key archive/status were removed. This is not off-site or a
restore, so DH-014/DH-015 remained open at that time. DH-015 has since been **performed
2026-08-13** with partial coverage; DH-014's off-site leg is still unexercised.

**DH-008 deep verification 2026-08-13:** both OpenMRS backend targets built with official OAuth2
Login 1.5.0 from the exact release commit. The live persistent volume received the byte-identical
OMOD; OpenMRS REST reported it registered and stopped in fail-closed mode, configured local admin
authentication and FHIR passed, and `Admin123` remained refused. OpenELIS configured CSRF/form
login passed, `adminADMIN!` remained refused, and mutual-TLS FHIR metadata passed. OIDC discovery,
SAML metadata, both OpenELIS HTTP paths, and adapter readiness answered. Keycloak existing-realm
reconciliation passed repeatedly, an isolated networkless H2 import proved the fresh realm JSON,
and a final clean Keycloak recreation imported/skipped normally with zero restarts. The complete
project container set finished at zero restarts/OOM kills and every setup job at exit 0. Adapter
dependency/Ruff/14 tests and maternity Ruff/5 safety tests passed; Compose variants, shell, JSON,
XML, Git, image-tag policy, whitespace, and relative Markdown links passed. SSO activation itself
was deliberately not attempted because BLOCKER-5 is unresolved.

**Post-commit deep quality audit 2026-08-13:** rebuilt OpenMRS `on`/`off`, adapter, and backup
images; adapter dependency/Ruff/14 tests and maternity Ruff/5 tests passed. All Compose variants,
shell/JSON/XML/CSV syntax, Git integrity/whitespace, tracked-secret scan, image policy, and relative
Markdown links passed. The normal graph has ten running services, six setup jobs at exit 0, only
the OpenMRS gateway published on `127.0.0.1:8080`, and no backup scheduler by design. Configured
OpenMRS/OpenELIS logins pass while `Admin123`/`adminADMIN!` fail; both FHIR metadata paths,
OpenELIS frontend, OIDC/SAML metadata, adapter readiness/service token, all nine PostgreSQL tenancy
cases, aggregate data state, and every timezone passed. Keycloak fresh import, repeated existing-
realm reconciliation, and persisted-realm restart passed. Final project containers report zero
OOM kills and no crash restarts.

The same audit found two honest gaps. Both shipped Horiba CBC imports—not only Pentra60—fail on
the existing `LYM#(Whole Blood)` description, so BLOCKER-4 now asks about Micros60 and Pentra60.
More importantly, a durable unique ledger claim cannot resolve a crash after the target write but
before `processing` becomes `completed`. No transport exists, so no message has been affected;
DH-025 is now a P0 technical gate requiring target reconciliation and crash-point tests before
DH-011 may activate.

**DH-025 implementation 2026-08-13:** the adapter now derives a stable opaque correlation from the
full source-event identity, retains a PostgreSQL advisory fence for its complete process lifetime,
and exposes a typed recovery coordinator. Exactly one target match completes the existing claim;
proven zero matches authorises one retry; an unreachable target, multiple matches, `dead_letter`,
or any other ambiguity fails closed. The suite now passes 27 tests, including actual concurrent
claims and crashes immediately before/after a target write. A rollback-only live-PostgreSQL probe
exercised recovery without retaining a row; a second process was refused while the healthy adapter
held its fence; restart released/reacquired it; operational rows remain zero.

**Boundary not claimed:** no OpenELIS resource currently carries that correlation and no concrete
OpenELIS lookup has been demonstrated because DH-011's order shape/requester/mapping is not yet
selected. The transport-neutral safety core is verified; the writer/probe binding remains an
activation criterion inside DH-011, alongside BLOCKER-3. No clinical message moved.

**Deep quality verification 2026-08-13 (post-ADR-015/016).** Both Compose profiles parse; every
tracked shell/JSON/XML file passes syntax; Git integrity and whitespace pass; `.env` is untracked.
The three non-digest image references are the locally built `daher-health/*` images, and all four of
their `FROM` bases are digest-pinned. Adapter: `pip check`, Ruff lint, Ruff format, and **27 tests**
passed; maternity seed safety **5 tests** passed. Runtime: ten services running, six setup jobs at
exit 0, **zero restarts and zero OOM kills across all sixteen containers**. OpenMRS REST session and
FHIR R4 metadata answered 200 for the configured admin while `Admin123` returned
`authenticated:false`; OpenELIS webapp HTTPS 200; OpenELIS FHIR returned 200 with FHIR 4.0.1 from
HAPI **over mutual TLS**, and correctly failed TLS with `bad certificate` when no client
certificate was presented; OpenELIS frontend 200; Keycloak realm, OIDC discovery, and SAML
descriptor 200. All nine PostgreSQL tenancy cases behaved exactly as specified. Every container is
`EAT+0300`. Only `127.0.0.1:8080` is published. The adapter ledger and dead-letter tables remain at
zero rows on migration `0001_safety_ledger`; OpenMRS holds 102 patients, none voided. OAuth 2.0
Login 1.5.0 is registered and `started=false` (fail-closed); no backup container exists in the
default profile, by DH-022's design.

**Two probe-method corrections, recorded so the next audit does not repeat them.** The OpenELIS
frontend serves nginx on **port 80**, not 3000 — a probe against 3000 returns connection refused and
looks like an outage. Keycloak and the frontend images contain **no `curl`**; probe them with Python
from the adapter container over `daher-health_internal`. Extracting the client certificate for the
mutual-TLS FHIR probe needs `openssl pkcs12 -legacy`, because the shipped keystore uses RC2-40-CBC
which OpenSSL 3 refuses by default.

**A false alarm worth recording.** An initial OpenELIS login check judged success by grepping the
Dashboard body for "login", and reported that `adminADMIN!` still authenticated. It does not. The
POST returns 302 in every case; the discriminator is the `Location` header — configured admin
redirects to `/OpenELIS-Global/`, while `adminADMIN!` redirects to `/LoginPage`, byte-identical to
the behaviour of a deliberately wrong control password. DH-024's result stands. Test the redirect
target, not the page text, and always run the wrong-password control.

**End-of-session verification 2026-08-14.** Both Compose profiles parse; all tracked
shell/JSON/XML/CSV syntax, Git whitespace, and the untracked-`.env` check pass; the only non-digest
images are the four locally built `daher-health/*`, whose bases are all pinned. **Three test suites
pass: adapter 27, seeds 16, forms 18 (+1 skipped)**, with Ruff lint/format clean in all three under
the project config. Runtime: **11 services running, 7 setup jobs at exit 0, zero restarts, zero OOM
kills**. Configured OpenMRS login passes while `Admin123` is refused; OpenELIS serves DH-021's
generated certificate (`CN=openelis.org`, not expired); all three Caddy vhosts answer and the
OpenELIS API shares the `lab` origin. Data unchanged: 102 patients, ledger 0 rows, `Bilan prénatal`
present with **0 members** exactly as intended.

## Ticket status

| Status | Tickets |
|---|---|
| ✅ Done | DH-001, **DH-002** (Liban confirmed 2026-08-11), **DH-003**, **DH-007**, **DH-009a**, **DH-009b**, **DH-010**, **DH-019** (empty-volume verified; old live volume rotated), **DH-020**, **DH-022**, **DH-024**, **DH-011A** (ADR-026), **DH-011B** (spike performed) |
| 🟢 Built + verified, awaiting human input | **DH-005** (Liban's own look) |
| 🔨 In progress | DH-014 (local encrypted path verified; off-site unverified), DH-017 (README brought current; needs a fresh-VM walk-through), **DH-025** (recovery core verified + committed; concrete OpenELIS binding remains with DH-011C) |
| 🟢 Built + verified, activation blocked | **DH-008** (protocol/client plumbing; BLOCKER-5 — 🗓 meeting) |
| 🔨 Performed, partial coverage | **DH-015** (restore drill performed 2026-08-13; re-run needed for the lab-result and SSO legs) |
| 🔨 Built + locally verified, external input to finish | **DH-016** (routing/TLS verified locally; public certificates wait on #15 DNS) |
| 🗓 Blocked on the birth-centre meeting | **DH-004** (ADR-006 guard built; fields await sign-off), **DH-006** (mechanism built; contents await sign-off), **DH-008** activation (staff **names** only — types decided, ADR-017) |
| 🟢 Built + verified, **not activated** | **DH-011E** (OpenMRS poller + durable watermark, 94 tests, 5 controls — no loop runs it and no delivery target exists) |
| 🛑 Blocked on **BLOCKER-6** (human) | **DH-011** target leg — DH-011C (correlation binding), DH-011J (cancellation), DH-011K (crash points), DH-011L (demonstration). BLOCKER-3 is closed; this is a *new* blocker found by probing on 2026-08-16 |
| 🔨 Provisioning control plane | **DH-026** (facility parameterisation), **DH-027** (API, portal, agent, durable state — works end to end **on a stand-in**; no clinical stack has booted this way) |
| ✅ Done (infrastructure) | **DH-027M** (CI — 678 tests now gated, every check control-tested), **DH-027B** (compensated facility creation — not a single ACID transaction; see its entry), **DH-027F** (atomic env writes; rotation rollback still open), **DH-027E** (real soft decommission), **DH-027C** (lifecycle state machine), **DH-027D** (observed infrastructure status), **bug 18** (agent polls through a long job), **DH-027G** (backup bootstrap/off-site wiring), **DH-027H** (nightly-scheduler backup evidence reaches the control plane) |
| ⏭ Deferred to v1.1 by decision | **DH-023** (analyzer interface — ADR-016) |
| ⏸ Waiting on dependencies | DH-012, DH-013, DH-018 |
| ✅ Done (architecture/product) | **UI-001** (unified clinical UX + data-contract map — no frontend built; see `docs/UI-001-CLINICAL-UX.md`) |

## ▶ START HERE (next session)

**Read `DECISIONS.md` first — 26 ADRs, several load-bearing.** Then this section.

### Where things actually are

Two systems now live in this repo:

1. **The clinical stack** (`docker-compose.yml`) — 11 services running locally, verified. Golden
   flow is **1 of 8 steps**. The order/result transport (DH-011/012) is **not built**.
2. **The provisioning control plane** (`provisioning/`) — API, portal, host agent, durable state.
   360 tests. Works end to end **against a stand-in compose file**.

⚠️ **The 1 Sept pilot target is at risk by a recorded decision, not an oversight.** Liban chose on
2026-08-15 to build provisioning before DH-011's transport chain, so the golden end-to-end flow will
very likely not be demonstrated by then.

### Unblocked and buildable right now

**The DH-011 order path is half built and now waits on you.** Read BLOCKER-6 before anything else in
this section — it is the decision that unblocks five of the eight golden-flow steps.

1. 🛑 **DH-011's target leg is blocked on BLOCKER-6 / NEEDS LIBAN #16.** Probing before writing code
   found that OpenELIS **pulls** orders from a remote FHIR source rather than accepting a push into
   its FHIR store — the mechanism every document here implicitly assumed. **Five** candidates are
   recorded with their costs; DH-011B proved Option F reaches real native lab state but is refused
   by accession configuration (`DEV01`, NEEDS LIBAN #7). Which path to use changes what the adapter
   *is*, and one option adds a second system storing patient data, so it is yours.
   ✅ **Already done meanwhile:** DH-011A (ADR-026), DH-011B (spike), **DH-011E** (poller + watermark,
   built and verified, deliberately not activated).
2. ~~**DH-027M — CI**~~ — ✅ **DONE 2026-08-17.** `.github/workflows/ci.yml` gated **582 tests** at
   landing; it currently gates **678 tests across four suites** (adapter 94, provisioning 549, seed
   16, form-safety 19 — measured against the real CI run, not copied) plus Ruff, compose
   interpolation on both profiles, shell/JSON/XML/CSV syntax, image pinning, a tracked-secret scan,
   the `.env`-untracked check, and relative-link resolution. Details and the honest limits are in the
   ticket entry below.
3. **DH-017** — the README needs a walk-through on a genuinely fresh VM. Documentation verified only
   against an already-working machine is the weakest kind. ⚠️ Now partly entangled with the
   cloud-primary direction, which is itself an open decision (data residency). **This is the next
   agent-buildable ticket**, and it needs a VM someone can give me access to.
4. ~~**Known bug 17**~~ — ✅ **fixed 2026-08-16** with per-verb timeouts. It left **bug 18** open by
   decision: the agent does not poll while a long job runs, so jobs issued in that window are refused
   as stale. **Bugs 19 and 20 were also opened** — see the known-bugs table; 19 is an upstream
   OpenELIS `201`-on-failure that any target implementation must defend against. ~~**Bug 18**~~ — ✅
   **fixed 2026-08-20**: `execute` runs on a background thread, so the loop keeps polling while a
   long job runs; a companion fix to DH-027C's lifecycle table (`app.py`) was required alongside it —
   see the known-bugs table.

### Blocked, and on what

| Blocked on | Items |
|---|---|
| 🗓 **Birth-centre meeting** | Staff names for the SSO role map (types already decided, ADR-017) · clinical sign-off for DH-004/DH-006 · confirm ADR-018's cancellation default · the facility's **city** |
| ⏳ **Real domain + DNS** | DH-016 public certificates · the provisioning API's last `incomplete_step` |
| 🔒 **External** | Off-site bucket + key escrow (DH-014) · WhatsApp credentials (DH-013, ADR-003) |

### Do not

- **Do not activate SSO** until BLOCKER-5's staff names exist. Types are decided; names are not.
- **Do not fill the prenatal panel or partograph fields.** Both mechanisms ship **empty on purpose**
  and tests enforce it. `docs/CLINICAL-SIGNOFF.md` is the handover sheet.
- **Do not touch the Horiba imports.** ADR-016 deferred analyzers to v1.1 and did not authorize
  disabling or repairing shipped laboratory configuration.
- **Never `docker compose down -v`.** It destroys the volumes and every piece of evidence in this
  repo. Cold boot is ~50 minutes; warm is ~38 seconds.

### The pattern worth inheriting

This session found **eight** checks that passed for reasons unrelated to what they claimed — a
healthcheck green through a total outage, a guard that validated zero files, a control a dependency
silently overwrote, a TLS control that never reached TLS, string replacements that matched nothing
and reported success. Several were in tooling written the same hour.

So: **control-test every guard** — break it deliberately and watch it fail — and treat an empty or
suspiciously clean result as a finding, not a pass.

---

## ▶▶ NEEDS LIBAN — the decision list

Everything an agent could not decide alone, in one place, roughly in the order it starts costing
time. Nothing below is urgent for the local stack; several are hard gates before the pilot VM
exists. Each links to the detail rather than repeating it.

| # | Decision | Why it isn't mine | Blocks |
|---|---|---|---|
| ~~1~~ | ~~**The 50 fictional demo patients**~~ — ✅ **RESOLVED 2026-08-12.** Answered with "make it an option": `OMRS_DEMO_PATIENTS`, default `off`. The demo *concept* content becomes a second knob in DH-003, default as-shipped — no decision needed from you unless you want a different default | — | Nothing |
| ~~2~~ | ~~**Poll interval for the adapter**~~ — ✅ **RESOLVED 2026-08-12 by Liban:** 15 seconds, configurable by environment variable | — | DH-010 is unblocked |
| ~~3~~ | ~~**Does voiding an order need to reach the laboratory?**~~ — ✅ **RESOLVED TECHNICALLY 2026-08-13.** A proper `DISCONTINUE` creates a new visible ServiceRequest carrying `replaces`; the adapter must forward it and must not filter on `status` | — | Nothing; implementation constraint recorded in `WATERMARK-DESIGN.md` §6 |
| ~~4~~ | ~~**Revisit `labonfhir` (ADR-004)?**~~ — ✅ **RESOLVED 2026-08-12 by Liban:** continue the custom adapter for v1; revisit after the golden flow | — | DH-010 is unblocked |
| ~~5~~ | ~~**Keycloak session lifetimes**~~ — ✅ **RESOLVED 2026-08-12 by Liban:** 1h idle, 10h maximum, 5m access token (ADR-013) | — | DH-007 complete |
| ~~6~~ | ~~**OpenELIS default requester identity**~~ — ⏳ **INTERIM DECIDED 2026-08-14:** create one controlled test provider ("Pilot Test Clinician") replacing upstream's sample ("Felix Dr. Mauritius"). The real ordering clinician swaps in after the meeting — a one-line change, not a redesign. **Not yet created; tracked in DH-011.** | — | DH-011 |
| 7 | **Facility identity** — ✅ **PARTIALLY SET 2026-08-14:** country `Djibouti`, facility id `DJ-BC-001` (deliberate internal placeholder). ⏳ **Still needed: city**, plus state/district/postal code. ⚠️ **Now also blocks Option F and therefore the whole order path (DH-011B, 2026-08-17).** The exact question, narrowed so it can be asked of the laboratory directly: **what numeric site code should lab accession numbers carry?** OpenELIS is configured `acessionFormat = SITEYEARNUM`, whose validator formats the accession as **`%013d` — 13 digits, all numeric** — while the configured prefix is still upstream's alphabetic `DEV01`. So every order is refused with `Generated lab number is invalid`. A supplied accession does not work around it: the value is always server-generated. This is a laboratory identifier convention and is deliberately not being invented | The birth centre's real details are yours | `common.properties.template`, **and the DH-011 order path** |
| 8 | **Maternity bed counts** — ✅ **PLACEHOLDERS SET 2026-08-14:** 2 delivery / 4 postpartum, clearly marked. ⚠️ Additive-only means these CANNOT be removed later; set real numbers before the pilot deployment | Facility numbers | DH-003 unblocked |
| 9 | **Prenatal panel + partograph fields** — untouched, both need the clinical lead | Hard gate for *clinically live* (ADR-012) | DH-004, DH-006 |
| ~~10~~ | ~~**DH-011 first-sync boundary**~~ — ✅ **RESOLVED 2026-08-13 by Liban (ADR-015):** no historical backfill; the watermark starts at deployment time. The pilot is forward-going patients, not a paper-record digitisation | — | BLOCKER-3 leg 1 closed |
| ~~11~~ | ⏳ **OpenELIS requester identity** — **INTERIM DECIDED 2026-08-14** (controlled test provider; real name after the meeting). Was: 🗓  — provide the facility's real requester identity; the upstream sample clinician must not silently own pilot orders | Order attribution is clinical/audit data. The current upstream value is deliberately only a placeholder | DH-006, DH-011 |
| ~~12~~ | ~~**Cancellation contract**~~ — ✅ **DECIDED 2026-08-14 (ADR-018):** revoke if the lab has not accepted/started; leave in-progress work for manual handling. Interim policy, to be **validated against real workflow** at the meeting | — | BLOCKER-3 closed |
| ~~13~~ | ~~**Horiba Micros60/Pentra60 analyzer scope**~~ — ✅ **RESOLVED 2026-08-13 by Liban (ADR-016):** v1 is manual result entry only; no analyzer/HL7/ASTM interface. DH-023 deferred to v1.1. The shipped imports are left untouched — the decision does not authorize editing lab test rows | — | BLOCKER-4 closed |
| ~~14~~ | ~~**SSO authorization matrix**~~ — ✅ **ROLE TYPES DECIDED 2026-08-14 (ADR-017).** `clinician`→`Provider`+`Organizational: Nurse` (provider=yes); `lab-tech`→`Reception`,`Results`,`Validation`; `sys-admin`→`Organizational: System Administrator`+`Global Administrator`. ⏳ **Only the NAMES still need the meeting.** Was:  — map `clinician`, `lab-tech`, and `sys-admin` to exact OpenMRS and OpenELIS native roles, and decide which staff classes are OpenMRS providers | These mappings grant access to clinical records and laboratory functions and change clinical attribution. An agent may not infer them from role labels | DH-008 / BLOCKER-5 |
| 15 | ⏳ **Pilot domain and DNS** — *in progress 2026-08-13; Liban is registering it.* Provide/control the real `BASE_DOMAIN` and point `emr`, `lab`, and `id` records at the pilot VM | Public certificate issuance cannot be demonstrated against `localhost` or invented facility DNS | DH-016 |
| 16 | 🛑 **How an order enters OpenELIS** — BLOCKER-6, raised 2026-08-16. OpenELIS **pulls** orders from a remote FHIR source it is configured to trust; it does not accept a push into its own FHIR store, which is where the adapter was implicitly assumed to write. **Four** options with costs are in BLOCKER-6 (D added by Liban, none selected) | A removes the adapter's audit/ledger boundary that ADR-005 made load-bearing for patient safety; B turns the adapter into a FHIR server; **D adds a second system storing patient/order data**, changing backup, retention and security scope. Architecture and data-flow decisions, not implementation details | **DH-011** target leg, and therefore DH-012, DH-013, DH-018. **DH-011A** (poller + watermark) is deliberately carved out and proceeds |

### 🗓 Waiting on the birth-centre meeting — as of 2026-08-15

**Much shorter than it was.** On 2026-08-14 four of these turned out to be policy calls rather than
facts, and were decided (ADR-017, ADR-018, ADR-019, plus marked placeholders). What is left needs
the room:

| Needs | Decisions | Tickets held | What unblocks the moment it is answered |
|---|---|---|---|
| **Staff names only** | #14 — role *types* already decided (ADR-017) | **DH-008** activation | Who holds `clinician` / `lab-tech` / `sys-admin`. The mapping itself is done |
| **Clinical sign-off** | #9 prenatal panel + partograph fields | **DH-004**, **DH-006** | Mechanisms are built and ship empty; the lead's answer fills them. Gates *clinically live* (ADR-012), not pilot-ready |
| **Workflow validation** | Confirm ADR-018's cancellation default matches practice | — | Nothing is held; it is already the interim policy. Confirm or supersede |
| **The city** | Facility identity #7 | — | Country and facility id are set; city/state/district/postal are still blank because nobody has supplied them |

**Downstream of those, by dependency only:** DH-012 (needs DH-011), DH-013 (needs DH-012),
DH-018 (needs DH-013 + DH-015). So the meeting sits in front of five of the eight golden-flow steps.

**Separately in progress, not meeting-blocked:** #15 domain/DNS — Liban is registering it; DH-016
stays blocked for public verification until the records exist.

**Not waiting on the meeting:** DH-015 (performed), DH-016 (built; public certs need #15),
DH-017 (brought current; needs a fresh-VM walk-through), DH-021 (done), healthcheck bug 15 (done).
The remaining agent-buildable surface is now small — most of what is left needs the meeting or the
domain.

**ADR-005 review completed 2026-08-12.** Liban approved the custom adapter and 15-second poll
interval after the evidence and watermark design were presented. OpenELIS outbound delivery and
corrected-result behavior remain empirical gates, not assumptions.

**Two security items I fixed without asking** (disabling exposure needs no permission; restoring it
would): upstream's `mail.bcc`/`mail.from` default of a third-party gmail address is blanked, and
every published default credential found so far is now actively refused — OpenMRS admin (DH-019),
the OpenELIS DB roles, and OpenELIS admin via DH-024's verified post-bootstrap bcrypt gate.

---

## Blockers

### BLOCKER-1 — Docker daemon unreachable · ✅ RESOLVED 2026-08-10

**Ticket:** DH-002
**Was:** `libanbritt` was not in the `docker` group; every docker command failed with
`permission denied` on `/var/run/docker.sock`.
**Response recorded:** Liban ran `sudo usermod -aG docker $USER`. Verified in `/etc/group`
(`docker:x:973:libanbritt`). Group membership attaches at login, so it required a logout/reboot
and a Claude Code restart to take effect — done 2026-08-10.
**Note for the pilot VM:** the same choice recurs there with higher stakes. `docker` group
membership is effectively root on the host. Decide it deliberately, not by habit.

### BLOCKER-2 — Demo data · ✅ RESOLVED BY DECISION 2026-08-12 — made a switch, not a choice

**Liban's response, recorded verbatim in effect:** *"When spinning up different instances, give me
the option to be able to include demo data or not."* He rejected the either/or I presented and asked
for a knob — consistent with ADR-007's treatment of bed counts.

**Built:** `OMRS_DEMO_PATIENTS` (default `off`) selects a late, single-property Initializer overlay
at the **distribution source**, because the entrypoint re-copies the live config tree on every
boot. DH-003 replaced the earlier whole-file overlay without changing its behavior. An isolated
empty-volume boot with this set to `off` produced zero patients.

**Split into two knobs deliberately and completed at DH-003.** `OMRS_DEMO_CONTENT` defaults to
`on` (upstream as shipped), because the demo tree includes `BasicLabTests` vocabulary DH-006 may
need. `off` selects a local backend build target that removes only the distro's
`referenceapplication-demo` source directories. The exact pinned upstream image remains the base,
and the final container still runs as UID 1001. The empty-volume `off` path copied zero demo
directories and reached healthy. Neither switch deletes rows from an existing database.

**Below is the original entry, kept for the reasoning.** ⚠️ Its framing as a decision to be made is
superseded; both switches now exist, while clinical approval of vocabulary remains separate.

<details>
<summary>Original BLOCKER-2 (superseded)</summary>

**Ticket:** DH-003 (surfaced during DH-002; does **not** block DH-002)
**Raised:** 2026-08-10

**Observed, not inferred.** Booting the stock backend image, the container's Initializer
configuration tree contains two OCL package directories:

- `/openmrs/data/configuration/ocl/referenceapplication/` — 2 packages
  (`CIELImmunizationContent`, `PD`)
- `/openmrs/data/configuration/ocl/referenceapplication-**demo**/` — 21 packages, ~6 MB zipped,
  including `BasicLabTests`, `DiagnosesStarterKit` (3.7 MB), `BasicDrugs`, `CIELAllergySet`,
  `procedures`, and also `DemoPrograms`, `DemoQueueConcepts`, `GenericDemoForm`, `IPD-DEMO`

All of them import on every boot (`openconceptlab_import` rows, one per package). Alongside them,
`configuration/concepts/referenceapplication-demo/*.csv` loads a further ~700 concept rows.

**Why this is a human decision and not an agent's.** This is *concept dictionary content* — the
medical terminology the EMR offers clinicians. Three things are tangled together and only a human
should separate them:

1. Most of it is legitimate unmodified reference content (CIEL-derived lab tests, diagnoses,
   drugs) — exactly what the non-negotiable "reference defaults only" rule *wants*.
2. Some of it is explicitly demo scaffolding (`GenericDemoForm`, `DemoQueueConcepts`, `IPD-DEMO`).
3. It arrives from a directory named `-demo`, so "is this facility-appropriate clinical content or
   is it sample data?" cannot be answered from the directory name alone.

Stripping the `-demo` tree would be the "obviously right" tidy-up — and that instinct is precisely
what §5 of the sprint spec exists to stop. Removing it may also remove the lab test concepts that
DH-006's prenatal panel needs to map to. **Not touched.**

### ⚠️ It is NOT metadata only — the distro creates 50 fictional PATIENTS

An earlier note in this entry said no demo patients were created. **That was wrong**, and the
correction matters more than the original entry. The count was taken mid-boot; the demo patients
are created in the last seconds before the app answers.

**Observed after the boot completed:** `SELECT COUNT(*) FROM patient` = 51 — the one deliberate
test patient plus **50 fictional people** (Betty Williams `100000Y`, Michael Turner `100006J`,
Susan Lopez `100001W`, …), all `date_created 2026-08-11 10:24:46`.

**Mechanism, located exactly:**

- The image bundles `referencedemodata-2.6.1.omod` in `/openmrs/data/modules/`.
- It is driven by the OpenMRS **global property**
  `referencedemodata.createDemoPatientsOnNextStartup` — now `0`, having fired once on this boot.
- **`OMRS_CONFIG_ADD_DEMO_DATA=false` did not prevent this**, consistent with that variable being
  inert (see the compose comment). The real control is a global property this repo does not set,
  in a module this repo did not choose to install.

This is the exact hazard `.env.example` warned about in words — fictional people in the same
tables as real ones — reached through a mechanism nobody had identified. It is *not* a reason to
panic: this is a local throwaway verification stack. It **is** a hard gate on the pilot instance.

**Not acted on, deliberately.** Deleting patient rows and disabling a bundled module are both
outside DH-002, and one of them touches patient tables. Setting the global property belongs in
DH-003's Initializer configuration, where it applies from first boot — which is the only moment
that helps, since the property self-clears after firing.

**Both original questions are now answered:** (2) is built and verified as `OMRS_DEMO_PATIENTS`;
(1) becomes a second knob in DH-003 rather than a decision.

</details>

_No blockers open against DH-002._

### BLOCKER-3 — DH-011 activation and cancellation contract · ✅ CLOSED BY DECISION 2026-08-14

**Ticket:** DH-011

**Leg 1 — first-sync boundary · ✅ RESOLVED 2026-08-13 by Liban (ADR-015).** No historical backfill;
the watermark starts at deployment time, read from the OpenMRS server clock and persisted once.
Accepted consequence: an order placed before deployment never reaches OpenELIS, including one still
active, and no alert marks its absence. Nightly reconciliation must count only from the bootstrap
watermark or it reports a permanent false mismatch. **Two legs below remain open.**

**Problem:** the target contract has two remaining human gates. OpenELIS requires an attributable requester,
while this deployment still carries upstream's sample clinician placeholder. For discontinuation,
ADR-005 proves the adapter will see a new `ServiceRequest.replaces`, but it does not decide the
laboratory-side state transition after the original order has been received.

**Primary evidence checked 2026-08-13:** deployed OpenELIS 3.2.1.11 bytecode and its exact source
tag (`e2a6bdf7`) show the incoming workflow is Task/ServiceRequest based and resolves requester and
test mappings. The official Ozone `eip-openelis-openmrs` source inspected at `7f994594` labels panel
support and discontinuation unsupported. Its current cancellation path deletes only a still-
`REQUESTED` Task/ServiceRequest and carries a TODO that it should revoke rather than delete. That
is evidence of an unresolved upstream contract, not permission to choose one here.

**All three legs are now decided:**

1. **First sync** — ADR-015: no historical backfill; watermark starts at deployment time.
2. **Requester** — interim decision 2026-08-14: a controlled test provider ("Pilot Test Clinician")
   replaces upstream's sample for testing; the real ordering clinician swaps in after the meeting.
3. **Cancellation** — ADR-018: revoke if the laboratory has not accepted or started the test; leave
   in-progress work alone for manual handling. Interim policy, to be validated against real
   workflow at the meeting.

**DH-011 is therefore no longer blocked on decisions.** What remains is implementation plus two
verification gates: DH-025's correlation must be attached to the concrete OpenELIS resource, and
ADR-018's "accepted or started" must map to a *verified* OpenELIS status rather than an assumed
one. No poller or clinical order has been activated.

### BLOCKER-4 — OpenELIS Horiba Micros60/Pentra60 analyzer imports · ✅ CLOSED BY SCOPE 2026-08-13 (ADR-016)

**Ticket:** DH-023 — deferred to v1.1, not blocked.

**Was:** on every observed OpenELIS webapp boot, both `horiba-micros60-cbc.csv` and
`horiba-pentra60-cbc.csv` attempt to update different test rows to the already-existing unique
description `LYM#(Whole Blood)`. PostgreSQL rejects each update on `test_desc_uk`; each transaction
aborts and OpenELIS logs failed test-editor bridging for subsequent rows.

**Resolved by decision, not by repair.** Liban decided v1 is manual result entry only — lab techs
type results into OpenELIS as they currently write them on paper — with analyzer/HL7/ASTM
integration deferred to v1.1 or later. The scope question that made this a blocker is answered.

**What stays true:** each failed transaction rolls back, the live table has zero duplicate
descriptions, and the web/FHIR endpoints answer. The imports are left untouched — ADR-016 does not
authorize disabling, renaming, repairing, or log-suppressing shipped laboratory test configuration.
The errors remain a known non-blocking boot behaviour. No test, mapping, or analyzer configuration
has ever been changed.

### BLOCKER-5 — SSO native authorization mapping · ✅ ROLE TYPES DECIDED 2026-08-14 (ADR-017) · ⏳ NAMES PENDING

**Ticket:** DH-008

**Problem:** Keycloak currently has high-level realm roles `clinician`, `lab-tech`, and
`sys-admin`, but the applications do not authorize those labels. OpenMRS OAuth2 Login consumes
exact OpenMRS role names and separately decides whether to create a Provider. OpenELIS SAML
consumes exact OpenELIS role names from its case-sensitive `Role` assertion attribute. Mapping a
staff category to either set grants access to patient records or laboratory functions and affects
clinical attribution.

**Decision needed:** provide an explicit matrix for each realm role:

1. exact OpenMRS native role name(s), if any;
2. whether members should be created as OpenMRS providers; and
3. exact OpenELIS native role name(s), if any.

No role equivalence was inferred. Until the matrix is approved, the Keycloak `provider` claim is
hard-coded false, SSO defaults disabled, both local login paths remain available, and the one-login
acceptance test is deliberately unperformed.

### BLOCKER-6 — how an order actually enters OpenELIS · 🛑 OPEN 2026-08-16 · **Human Decision Required: Yes**

**Ticket:** DH-011 (order path). Found by probing the running stack before writing transport code.

**Problem.** Every document in this repo says "the adapter routes the order to OpenELIS" without
saying *through which ingestion mechanism*, and the evidence now says the obvious one is not it.

**Evidence — read-only, gathered 2026-08-16:**

1. OpenELIS's FHIR store is **completely empty**: `Task`, `ServiceRequest`, `DiagnosticReport`,
   `Patient` and `Practitioner` all return `total: 0`. So does `clinlims`: `electronic_order`,
   `sample` and `analysis` are all **0 rows**. The laboratory has never received an order.
2. `org.openelisglobal.fhirstore.uri` points at **OpenELIS's own HAPI container**. Per
   `org.openelisglobal.fhir.subscriber.resources`, that store is where OpenELIS *publishes* its own
   resources. It is not documented as an inbox.
3. The inbound path in the rendered `common.properties` is a **poll, not a push**:
   `org.openelisglobal.remote.source.uri` (currently **empty**),
   `org.openelisglobal.remote.poll.frequency=120000`, and
   `org.openelisglobal.remote.source.identifier=Practitioner/*`. OpenELIS **pulls** orders from a
   remote FHIR source it is configured to trust, every two minutes.

**Why this is not mine to decide.** It changes what the adapter *is*, and it changes what patient
data flows where — both on the always-ask list:

| Option | What it means | Cost |
|---|---|---|
| **A. Point `remote.source.uri` at OpenMRS** | OpenELIS pulls orders straight from the EMR | Simplest, and it makes the adapter's order leg largely redundant — which contradicts the accepted architecture and removes the ledger/audit boundary that ADR-005 made load-bearing for patient safety |
| **B. The adapter exposes a FHIR endpoint OpenELIS polls** | Adapter pulls from OpenMRS, serves to OpenELIS | Preserves the adapter as the routing and audit boundary, but makes it a FHIR **server**, not the client the design assumes. Substantially more surface than DH-010/DH-025 were built for |
| **C. The adapter writes into OpenELIS's own fhirstore** | Adapter pushes, hoping OpenELIS ingests | ⚠️ **Unverified and possibly not a real path.** The store is empty, `transformOnStartup=false`, and nothing in the config describes it as an inbox. Would need proving before it could be trusted |
| **D. A dedicated order-exchange FHIR store** *(added by Liban 2026-08-16; **not** selected)* | The adapter writes orders to a separate exchange store; OpenELIS polls **that** store via `remote.source.uri`. Keeps the adapter as the routing and audit boundary without making it a FHIR server (B), and uses the pull mechanism OpenELIS actually documents | ⚠️ **Human Decision Required: Yes — and the widest scope of the four.** It adds another system that **stores patient and order data**, so it lands inside every boundary this project has already drawn: DH-014/DH-015 backup and restore coverage, ADR-021's "no clinical data outside the stack" reasoning, the tenant-isolation model, retention, and the security surface of one more network-reachable store holding clinical records. None of those are implementation details, and a new clinical datastore is not a thing an agent may adopt because it is architecturally tidy |

| **F. OpenELIS's own business FHIR providers** *(added 2026-08-16, ADR-026; **spiked, still not selected**)* | The adapter posts to the FHIR providers inside the OpenELIS **webapp** — a different layer from option C's generic HAPI store. Keeps the adapter as the ledger/audit boundary without making it a FHIR server (B) or adding a clinical datastore (D) | ✅ **Fully spiked 2026-08-17 (DH-011B) and it is the strongest candidate: the providers write OpenELIS's own domain tables — `clinlims.patient` and `clinlims.provider` rows were created through the supported FHIR surface, and `ServiceRequestProvider` calls the same `SamplePatientEntryService` the human workflow uses.** Two gates, neither an engineering choice: (1) **ServiceRequest is refused** — `SITEYEARNUM` formats accessions as `%013d`, 13 all-numeric digits, while the configured prefix is `DEV01`; a **numeric facility site code** is required (#7), and a supplied accession is ignored because the value is always server-generated. (2) **The submitted correlation is not retained** — our identifier is discarded (`external_id` NULL) and replaced by OpenELIS's own. ✅ *Corrected 2026-08-17:* exact identifier **search works** (a real identifier returns 1, a wrong system with the right value returns 0), so only *persistence* of the incoming identifier is missing. → **DH-011D**, narrower than first recorded |

**Deliberately not narrowed.** Options A–D are retained exactly as first recorded; F is appended
rather than replacing them, so the reasoning history stays auditable (ADR-026).

**Also settled by whichever option is chosen:** `remote.source.identifier=Practitioner/*` is the
filter deciding which orders OpenELIS considers its own — so the requester identity (NEEDS LIBAN #6,
interim "Pilot Test Clinician") stops being an attribution detail and becomes the routing key.

**Deliberately not done:** no probe Task was written into OpenELIS. Writing into the laboratory to
discover whether it ingests would give an ambiguous answer either way — nothing visible could mean
"not wired" or "wired but unconfigured" — and it is a write into a clinical system to satisfy a
guess. The read-only evidence above is enough to state the fork; it is not enough to pick a branch.

**Consequence for DH-011:** the OpenMRS-side poller and watermark are still buildable and are
unaffected by this choice. The OpenELIS write, DH-025's correlation binding, and ADR-018's status
mapping are all downstream of it.

---

## Golden end-to-end flow — status

The one flow that makes Day 1 mean anything (sprint spec §8). Demonstrated directly to Liban, not
reported as passing.

| # | Step | Status |
|---|---|---|
| 1 | Register test patient in OpenMRS | ✅ Liban registered a patient in the O3 SPA himself, 2026-08-11 |
| 2 | Same SSO session into OpenELIS, no second login | ⏸ |
| 3 | Place prenatal panel lab order in OpenMRS | ⏸ — the panel deliberately ships **empty** (DH-006, clinical sign-off). An order *can* be placed with an existing upstream test, and DH-011E's poller would detect it; nothing consumes it |
| 4 | Order appears in OpenELIS worklist | ⏸ 🛑 **BLOCKER-6.** Not a build task: how an order enters OpenELIS is undecided. DH-011B showed the lab has **never** received one (`electronic_order`, `sample`, `analysis` all 0 rows) |
| 5 | Enter result in OpenELIS | ⏸ |
| 6 | Result appears in OpenMRS chart | ⏸ |
| 7 | WhatsApp notification fires (mock OK for Day 1; real send is the `DONE` gate) | ⏸ |
| 8 | Audit log shows full round trip, **no duplicates on retry/restart** | ⏸ |

---

## Tickets, in dependency order

Priority: **P0** = required for pilot-ready · P1 = if time allows · P2 = post-pilot.
🔒 = pinned P0, immune to being cut under deadline pressure (sprint spec §2).

### DH-001 · Record decisions as ADRs · P0 · ✅ DONE
Move the reasoning that existed only in conversation into `DECISIONS.md`. Twelve ADRs, including
ADR-005 (the Subscriptions/polling question, later accepted 2026-08-12) and ADR-004 (the
`labonfhir` deferral).
**DoD:** ✅ ADRs committed; `CURRENT_STATE.md`, `PROJECT_STATE.md`, `AGENTS.md` created.

### DH-002 · First pull, digest pinning, verify OpenMRS · P0 · ✅ DONE
**Worked:** 2026-08-11. **Confirmed by Liban 2026-08-11** — he registered a patient through the O3
single-page app himself and reported it working. That was the one criterion an agent could not
close.

**Precision about what his run covered:** he confirmed registration works. He did **not** report
on the SPA's display language. DH-003 later verified the French frontend/backend configuration,
but the human-visible browser observation remains explicitly unreported.

**Acceptance criteria, individually:**

| Criterion | Result |
|---|---|
| Every image pinned by digest | ✅ Four repository digests pulled and recorded (compose + ARCHITECTURE §12). All four tags existed as listed; no substitutions |
| OpenMRS reachable on `${OMRS_LOCAL_PORT}` | ✅ after fixing the gateway's hard-coded upstream hostnames |
| Admin login works | ✅ via REST (`/ws/rest/v1/session`, `authenticated: true`). **Not** via the SPA |
| Test patient registered and retrieved | ✅ `10001MJ — Test-DH002 Verification`; found by name, by identifier, and via FHIR R4. **Registered through the API, not the registration form** |
| `down` && `up` returns to a working state, data intact | ✅ same patient, same UUID; no duplication (patients 51, concepts 4,259, OCL items 27,319 all identical); warm boot 38s |
| Healthchecks actually pass rather than merely being defined | ✅ — and the backend's was **found lying** and rewritten. See `CURRENT_STATE.md` §3a |
| DoD: verification steps in `README.md` | ✅ including the ≈50-minute first-boot warning and the demo-patient warning |

**Why this is now ✅ DONE.** The technical checks ran through `curl`, then Liban opened the O3
single-page app and registered a patient himself on 2026-08-11. The only UI observation not reported
was the display language, which remains a DH-003 check rather than reopening DH-002.

**Surfaced here, resolved elsewhere:** BLOCKER-2 became the explicit demo-patient switch, and
DH-019 wired and empty-volume-verified the required admin password.

### DH-019 · Wire the OpenMRS admin password through compose · P0 · ✅ DONE 2026-08-11
**Verified the only way it can be**: on a throwaway scratch project with an empty volume, a full
cold boot, then `admin` + the configured password → `authenticated: true`, and `admin`/`Admin123`
→ refused. The live stack was never touched, and the scratch stack was destroyed afterwards.
**Raised by:** DH-002, 2026-08-11.
**Problem.** The backend image sets the `admin` password from `OMRS_ADMIN_USER_PASSWORD`,
defaulting to the published value `Admin123`
(`OMRS_ADMIN_USER_PASSWORD=${OMRS_ADMIN_USER_PASSWORD:-Admin123}` in the image's
`startup-init.sh`; `OMRS_CONFIG_ADMIN_USER_PASSWORD` overrides it). The compose file passes
neither, so every instance built from this repo today ships with a known admin password.
**The sharp edge:** the password is written into the database **when OpenMRS initialises an empty
schema, and only then**. Setting it after the first boot does nothing. So this must land *before*
the pilot VM is first brought up, not before go-live.
**Requirements:** pass `OMRS_CONFIG_ADMIN_USER_PASSWORD` from `.env` (`OMRS_ADMIN_PASSWORD`,
already documented in `.env.example`); guard it with `${VAR:?}` so an empty value fails loudly
rather than silently falling back to the default; document the post-first-boot recovery path
(manual password change) in the runbook.
**Acceptance:** a fresh stack with a generated password refuses `Admin123` and accepts the
configured one. Verified on an empty volume, since that is the only case where it applies.

### DH-009a · Spike: ADR-005, **OpenMRS leg** · P0 · ✅ DONE — reviewed by Liban
**Worked:** 2026-08-11, against the running DH-002 stack rather than from documentation.

**Answer: neither event-driven option exists in the O3 3.7.x distro as we deploy it.**
- FHIR `Subscription` → HTTP 404, `HAPI-0302: Unknown resource type`; absent from the
  CapabilityStatement's 24 advertised types; `ServiceRequest` returns 200 as a control.
- `event-4.0.0`'s ActiveMQ broker is `vm://localhost` (in-JVM), the module ships an empty
  `moduleApplicationContext.xml`, and 61616 is refused. A separate adapter container cannot reach
  it. (A bare `tcp://` string exists in the jar; no way to expose it was found — "none found" is
  recorded as exactly that, not as "none exists".)
- Polling is viable: `_lastUpdated` is advertised **and verified applied** on `ServiceRequest`,
  `DiagnosticReport`, `Observation`, `Task`.

**Evidence** is appended to ADR-005 in `DECISIONS.md`. Liban accepted the proposed resolution on
2026-08-12: custom adapter, 15-second configurable order poll, inclusive watermark, and durable
ledger. **The watermark design is accepted; DH-010 has built its safety foundation, while the
watermark and poller remain unbuilt until DH-011**: `docs/WATERMARK-DESIGN.md`.

**Review scope presented to Liban**, in order: ADR-005's evidence section, then
`WATERMARK-DESIGN.md` §4 (why `ge` and not `gt` — the choice that trades duplicates for never
losing a result), §6 (tested discontinuation semantics and the `replaces` obligation), §7 (the
timezone constraint), and §9.2 (polling being the only trigger is new information bearing on
ADR-004's `labonfhir` deferral).

**Not done here:** no poller was written. The completed review now unblocks DH-010.

### DH-009b · Spike: ADR-005, **OpenELIS leg** · P0 · ✅ EVIDENCE COMPLETE — runtime gates remain
OpenELIS is now deployed. Its HAPI FHIR 6.6.0 CapabilityStatement advertises `Subscription`, and
its committed upstream-derived configuration exposes an outbound subscriber URL plus retry
interval/resources. That establishes capability and configuration shape, **not working delivery**:
no receiving endpoint has been registered and no outbound result has been observed.

ADR-005 accepts a test-driven branch: use outbound delivery only if it is demonstrated; otherwise
poll with equivalent safety properties. Demonstrating delivery belongs to adapter work, not this
completed evidence spike.

One higher-stakes behavior also remains uncharacterised: whether correcting a result advances the
FHIR `DiagnosticReport`/`Observation` version and timestamp. A stale corrected value in OpenMRS is
more dangerous than a stale order. This must be tested before the result leg is trusted; the
order-leg answer must not be assumed to generalise.

### DH-009 · (superseded by DH-009a / DH-009b above) · P0 · ✅ DONE
**Objective:** answer, with primary evidence, how the adapter learns an order/result exists.
**Depends on:** nothing — this is source and documentation reading. **Do it early**; it gates the
longest pole after SSO, and it does not need a running stack.
**Requirements:** read the `fhir2` module source and docs for the 3.7.x line; determine whether
outbound FHIR Subscriptions are supported and stable; do the same for OpenELIS's outbound result
direction. **Present quoted source with file paths — a conclusion alone is not acceptable output**
(ADR-005). If the answer is polling, write the watermark design (keying, advancement, restart
mid-window, back-dated records, failure mode when wrong) for human review **before** building it.
**Acceptance:** ADR-005 updated from `OPEN` to a decision, with evidence, reviewed by Liban.

### DH-003 · OpenMRS maternity config as code · P0 · ✅ DONE 2026-08-13

Initializer 2.12.0 handles the French global properties and deterministic `Maternité` admission
location, but its exact source contains no bed or bed-type domain. Bedmanagement 7.2.0's native
REST resources therefore back a short-lived, non-root seed job. Its implementation has no update,
retire, void, or delete call: existing objects must match or deployment stops for review.

**Acceptance evidence:**

| Criterion | Result |
|---|---|
| Fresh boot, no admin clicking | ✅ isolated empty-volume project reached healthy in ~4 minutes with demo patients/content off; ward, both types, and one test bed of each type existed; seed exited 0 |
| Same counts are idempotent | ✅ `2/2` rerun created `0/0` |
| Raising counts only adds the delta | ✅ `2/2 → 3/4` created `1/2` |
| Lowering deletes nothing | ✅ `3/4 → 1/1`, then committed `0/0`, created zero and retained all 3 delivery + 4 postpartum beds |
| French backend configuration | ✅ `default_locale=fr`, `reporting.defaultLocale=fr`, allowed list begins with `fr` |
| Demo controls remain explicit | ✅ fresh `off/off` boot had zero patients and zero copied `referenceapplication-demo` directories. A metadata preflight requires one completed content-on/patients-off boot before `patients=on`; the isolated two-stage path then created exactly 50 patients from one creator and reset its sole trigger |
| Runtime identity / safety | ✅ backend target and process uid 1001; seed uid 65534, read-only root, all capabilities dropped; conflicts fail without writes |

The exact pinned upstream backend is the base of local `on`/`off` build targets because some
Initializer loaders ignore its filename-exclusion API. DH-024 removed only upstream's competing
demo-patient trigger from that base, leaving the mounted `patients.on/off.xml` as its sole writer;
the other six properties in that upstream file remain. `content=off` affects what a new/restarted
image loads and deliberately does not delete content already imported into a database. The existing
local throwaway stack now contains 100 fictional demo patients, two deliberate test patients, and
the 3+4 lifecycle-test beds; no patient or bed row was deleted. Committed counts remain explicit
zero placeholders pending real facility numbers (ADR-007). A human has not yet reported whether
the SPA visibly opens in French, so that browser observation remains unverified rather than being
inferred from config.

### DH-004 · ANC + partograph forms · P0 · 🔨 ADR-006 GUARD BUILT 2026-08-13 · 🛑 FIELDS AWAIT SIGN-OFF
Data-entry only. 🔒 No alert lines, no action lines, no prolonged-labour flagging (ADR-006).
**Cannot be marked DONE without a recorded human response**, even when technically complete.

**Same finding as DH-006: nothing ships to copy.** No antenatal or partograph form exists anywhere
in this stack. Checked, not assumed — the only 10 forms present are upstream `-core_demo.json` demo
forms, and a full-text search of the distribution for "antenatal"/"partograph" returns only a demo
billing CSV. So the fields must be composed, which is the clinical lead's decision.

**Built: ADR-006 is now executable rather than a promise.**
`openmrs-config/forms/validate_form_safety.py` refuses any AMPATH form schema that performs
clinical interpretation — `calculate`/`calculateExpression`, `alert`/`warning` keys,
`js_expression` validators, and decision-support vocabulary (alert line, action line, prolonged
labour, risk score, triage, recommend, danger sign). 19 tests, each encoding a specific way
decision support gets added to a form. A pinned P0 that previously depended on whoever remembered
it now fails the deployment instead.

**A defect found in the guard itself, and fixed.** Pointed at a directory whose contents it could
not see, it reported "no clinical interpretation found" and exited 0 — a checker producing a green
result having examined zero files. An empty check is now an error, with tests locking that in. This
is the fourth false-green of this kind found in the project; the pattern is always the same, a check
that passes for a reason unrelated to what it claims to prove.

**⚠️ Finding — three demo forms currently loaded here DO perform clinical interpretation.**
Running the guard against the shipped demo schemas flagged `structured_soap_note` (an actual
`alert` key, `calculateExpression`, expression validators), `mental_health_assessment_form`, and
`form-engine-cookbook`. All three are live in the database. They are upstream **demo content**, not
maternity forms, and **nothing was changed**. `OMRS_DEMO_CONTENT=off` removes them and is already a
supported switch — but demo content is a decided option (BLOCKER-2), so turning it off for the
pilot is **Liban's call, not an agent's**. Human Decision Required: Yes.

**Still required to close:** the clinical lead supplies the field list in
`docs/CLINICAL-SIGNOFF.md` §6; the schema is built from it and must pass the guard; the response is
recorded here with its date and who gave it.

### DH-005 · PostgreSQL + OpenELIS standalone · P0 · 🟢 BUILT AND VERIFIED 2026-08-12
Five services added, all digest-pinned. Verified by running, not by `compose config`:

| Check | Result |
|---|---|
| Three databases as co-tenants (ADR-001) | ✅ `clinlims`, `keycloak`, `adapter`, correct owners |
| Tenant isolation | ✅ DH-020: each application role can connect only to its own database; all six cross-tenant attempts are denied |
| OpenELIS schema provisioned | ✅ 375 base tables in the fully migrated `clinlims` schema (live recount 2026-08-13) |
| Webapp serving | ✅ HTTPS 8443 (its HTTP connector does not listen) |
| FHIR server serving | ✅ `GET /fhir/metadata` → 200, **over mutual TLS** |
| Frontend container | ✅ running, **not yet exercised** — it had no route until Caddy (DH-016) |

**Four things running it exposed** (none catchable by a syntax check):
1. `certs` **cannot** be dropped as ARCHITECTURE §2 claimed — Tomcat aborts without the keystore.
2. The webapp reads its Spring config from a **Docker secret**; without it the WAR deploys, the
   context dies with `FileNotFoundException: /run/secrets/common.properties`, and Tomcat still
   reports a clean startup — a failure that looks like success in `docker compose ps`.
3. The FHIR container ignores `JAVA_OPTS` (bitnami replaces it); `CATALINA_OPTS` works. With the
   properties unsubstituted, **both** connectors stayed down while the log said "Server startup in".
4. The OpenELIS FHIR endpoint requires **mutual TLS** — see ADR-005 Q5. Load-bearing for DH-011/012.

**Later DH-024 evidence:** the configured admin login is now exercised through the real CSRF/form
path on both current and empty volumes, and published `adminADMIN!` is refused. The frontend's
internal HTTP endpoint answers; no human browser workflow has been performed.

### DH-020 · Restrict PUBLIC on the OpenELIS database · P1 · ✅ DONE 2026-08-13
Raised at DH-005 after the `adapter` role could connect to `clinlims` through PostgreSQL's default
database grant. The fix revokes database-level `PUBLIC` privileges on all three tenant databases
and restores explicit privileges only to each owner. It deliberately does **not** change OpenELIS's
schema grants: a role denied `CONNECT` cannot exploit the permissive `public` schema, while upstream
migrations retain their expected schema behavior.

Fresh volumes call the shared script from `initdb.d`; an idempotent, digest-pinned one-shot Compose
job applies the same grant state to existing volumes before OpenELIS, Keycloak, or the adapter may
start. Runtime verification used the actual three application roles: all three own-database
connections passed and all six cross-tenant connections were denied. After restarting every
PostgreSQL client, OpenELIS HTTPS, OpenELIS FHIR mutual-TLS metadata, Keycloak readiness, and adapter
readiness all passed. A second hardening run exited 0, proving restart idempotency.

### DH-021 · Replace the shipped keystore · **P0 (raised from P1)** · ✅ DONE 2026-08-13
Raised at DH-005. `itechuw/certgen` generates nothing — it ships **pre-built** keystores inside a
public image, so the private key protecting the internal 8443 connectors is publicly downloadable.
Attempting the replacement on 2026-08-13 found two further defects and one unrelated false green.

**Why this is now P0, not P1:** the shipped certificate **had already expired** — `notAfter
2026-07-23`, found 2026-08-13, verified with `openssl x509 -checkend 0` against the container clock.
Nothing on the internal path validates expiry, so it kept working silently. The first thing to
check it strictly — Caddy at DH-016, or the adapter's mTLS client at ADR-005 Q5 — would have failed
against a cause three weeks in the past.

**Second defect: the store passwords were the published `kspass`/`tspass` in effect.** `.env` sets
`OE_KEYSTORE_PASSWORD=` and `OE_TRUSTSTORE_PASSWORD=` **empty**, and compose read
`${OE_KEYSTORE_PASSWORD:-kspass}` — so an empty value silently selected upstream's published
password while the variable looked configured. Confirmed directly: the live keystore opens with
`kspass`, and refuses both the `.env` value and a wrong-password control.

**Built and proven in isolation** (`openelis-config/certs/`): a generator producing 4096-bit
RSA/SHA-256 material valid 825 days, with upstream's SANs plus this stack's service names, and
`serverAuth,clientAuth` because the webapp presents the same certificate as a *client* to the FHIR
server. It keeps the exact filenames, paths, PKCS12 structure, and alias layout so no consumer
changes, and is idempotent — it regenerates only when the certificate is missing, expired, within
`OE_CERT_RENEW_BEFORE_DAYS`, or unreadable with the configured password. Verified: generation, a
second run leaving it untouched, an `openssl s_server -Verify 1` mutual-TLS handshake returning
`Verify return code: 0 (ok)`, and a no-client-certificate control correctly refused with
`tlsv13 alert certificate required`.

**The coordination step, found the hard way.** The first attempt took OpenELIS down: the Spring
context failed with `keystore password was incorrect` and every page became a redirect loop.
OpenELIS reads its SSL passwords from `common.properties` — the **committed** Docker secret — not
from `CATALINA_OPTS`. A regenerated keystore paired with a `kspass` properties file is an outage.
The stack was restored from a pre-change backup and re-verified before continuing.

**How it was closed.** `common.properties` became
`common.properties.template` with `@OE_KEYSTORE_PASSWORD@` / `@OE_TRUSTSTORE_PASSWORD@`
placeholders, and `render-properties.sh` substitutes them at deploy time into the
`openelis-properties` volume, mounted read-only at `/run/secrets` — the path the application
already expects. A file-based Docker `secret` must be a host path, and a host path cannot be
produced by a container during the same `up`, which is why the delivery mechanism changed rather
than the value. **Certificates and properties are generated by one job on purpose**, so the
keystore and the password that opens it cannot drift apart.

**Verified 2026-08-13 on the live stack:** the keystore opens with the configured password and
refuses both `kspass` and a wrong-password control; the certificate served on the wire is
`CN=openelis.org, O=Daher Health`, valid to 2028-11-16, `checkend 0` clean; mutual-TLS FHIR
metadata returns 200 with the new client certificate and is refused without one; the rendered
properties file has zero remaining placeholders, mode `0444`, and its password hash matches `.env`;
configured admin login reaches `/OpenELIS-Global/` while `adminADMIN!` and a wrong control both
bounce to `/LoginPage`. A full `docker compose up -d` is idempotent — the certs job reported
"existing certificate is valid for more than 30 days — leaving it alone". Ten services running,
zero restarts, zero OOM, 102 patients unchanged.

⚠️ **`encryption.general.password=kspass` was deliberately NOT changed.** It is in the same file
and looks like part of this fix. It is not — it is application data encryption, and changing it may
make already-encrypted stored values unreadable. It needs its own analysis and is potentially
clinical-data surface. The template carries this warning inline.

### DH-022 · Keep unconfigured backup out of the default Compose path · P0 · ✅ DONE 2026-08-13
The documented `docker compose up -d` previously selected `backup` even when the required
encryption/off-site variables were absent, producing a credential-failure restart loop that
contradicted `CURRENT_STATE.md`. The scheduler now uses Compose's opt-in `backup` profile. Default
foundation startup remains clean; operators enable it explicitly only after approved settings
exist. This does not weaken the pinned gate: DH-014 production off-site verification and DH-015's
performed restore remain mandatory.

### DH-023 · Resolve OpenELIS Horiba analyzer import conflict · ⏭ DEFERRED TO v1.1 (ADR-016)
**Not blocked, not in scope.** Liban decided 2026-08-13 that v1 is manual result entry only, with
no analyzer interface and no HL7/ASTM instrument integration. This ticket is not sized or worked
now.

The shipped Horiba imports are left **exactly as upstream ships them** — not disabled, renamed,
repaired, or log-suppressed. Each failed import rolls back cleanly, the live table has zero
duplicate descriptions, and the web/FHIR endpoints answer, so the boot-log errors are a known
non-blocking upstream behaviour (`CURRENT_STATE.md` §3a, known bug 8). ADR-016 explicitly does not
authorize editing laboratory test rows to quiet them. When an analyzer interface is genuinely
wanted, the laboratory lead's confirmed test identities come first.

### DH-024 · Deep fresh-deployment quality hardening · P0 · ✅ DONE 2026-08-13
Raised by a full repository/runtime audit after DH-003. Three concrete false-green paths were
reproduced and fixed without changing clinical content:

1. **Demo-patient precedence:** the live configuration contained both upstream `value=50` and our
   `value=0` for the same property. A second 50-row batch appeared in the throwaway volume. The
   pinned local base now removes only upstream's four-line trigger block; six unrelated properties
   remain, `patients.off.xml` is the sole writer, and a live restart held patient count 102→102.
   A direct first-boot `on/on` imported all 23 concept packages but reset its trigger with zero
   patients because the upstream generator runs before that metadata is ready. A read-only database
   preflight now requires one completed content-on/patients-off boot before `patients=on`. The
   enforced two-stage path then created exactly 50 patients from one creator and reset the sole
   trigger. No patient was deleted.
2. **PostgreSQL init race:** `pg_isready` passed against the temporary `initdb.d` server and the
   hardening job then lost its connection during the intentional shutdown. Readiness now also
   requires `postgres` as PID 1. The exact empty-volume dependency graph passed with hardening 0.
3. **Published OpenELIS admin credential:** the image ignores `DEFAULT_PW` and rewrites its bundled
   `adminADMIN!` hash during first webapp startup. A read-only one-shot now waits for the real login
   redirect, bcrypts the required `${OE_ADMIN_PASSWORD}` into exactly one admin row, verifies it,
   and gates the frontend. Current and exact empty-volume CSRF/form probes accept the configured
   value and refuse the published default. The local ignored `.env` was rotated to a policy-valid
   value and remains mode `0600`.

Repository checks passed for both Compose demo targets and backup profile, every local Docker build,
adapter dependency consistency/Ruff/14 tests, maternity Ruff/5 tests, JSON/XML/CSV/shell syntax,
relative Markdown links, Git integrity/whitespace, image policy, and live-secret value scanning.
Runtime checks covered all endpoints, both FHIR capability counts, all nine PostgreSQL tenancy
cases, ten container timezones, Keycloak realm/client-secret/token behavior, adapter constraints,
and a fresh local encrypted backup read-back. Final normal startup left ten long-running services
healthy/running as applicable and all five setup jobs at exit 0, with zero restarts or OOM kills.
The backup was neither off-site nor restored.

### DH-006 · Prenatal panel order set + LOINC mappings · P0 · 🔨 MECHANISM BUILT + VERIFIED 2026-08-13 · 🛑 CONTENTS AWAIT SIGN-OFF
**The panel ships EMPTY, and that is the correct committed state.** The ticket said "draft early
(week 1) so the clinical lead has time" — what has been drafted is the *decision*, not the answer.

**The finding that shaped this.** Neither system ships a prenatal panel to copy unmodified, so it
has to be *composed* — and composition is the reserved clinical decision. Checked, not assumed:
OpenMRS had **zero** order sets defined, and OpenELIS's four shipped panels are `Bilan Biochimique`,
`NFS`, `Serologie VIH`, `Typage lymphocytaire`. **Liban confirmed 2026-08-13** that only artifacts
already shipped in this stack may be used, so no external guideline was fetched and no test was
selected by an agent.

**Built:**
- `docs/CLINICAL-SIGNOFF.md` — a checklist **generated from live reference data**, listing all 37
  OpenMRS lab sets installed here with their LOINC codes and concept UUIDs, plus the 4 OpenELIS
  panels. It is an inventory, explicitly **not** a recommendation, and nothing is pre-ticked.
- `openmrs-config/seed/seed_prenatal_panel.py` + an **empty** `prenatal-panel.members.csv`.
  Additive-only: it adds listed members, verifies each concept already exists, and **never removes**
  a member that is present but unlisted — that member may already have been used to order tests for
  a patient (ADR-007's reasoning). It never creates a clinical concept.
- Compose job `openmrs-prenatal-panel`, gating the frontend like the DH-003 seed does.

**Verified:** 16 seed tests (11 new) plus Ruff lint/format. Against live OpenMRS the seed created
`Bilan prénatal` with **0 members**, and a second run reported "already exists / 0 added" — the
order set count stayed at 1 and members at 0. Full stack: 11 running, 7 setup jobs at exit 0, zero
restarts.

**Technical finding for the meeting — LOINC coverage is partial on both sides.** OpenELIS: 42 of
177 active tests carry a LOINC code. OpenMRS: 21 of 37 lab sets. So LOINC alone cannot be the join
key between an OpenMRS order and an OpenELIS test; whichever tests are chosen, some pairs will need
an explicit mapping. This changes how much wiring each choice needs, not what may be chosen.

**Still required to close:** the clinical lead completes `docs/CLINICAL-SIGNOFF.md`; each ticked lab
set is copied verbatim into the members file; the response is recorded here with its date and who
gave it. **DH-006 stays OPEN until then, even though the mechanism is finished.**

### DH-007 · Keycloak + realm import · P0 · ✅ DONE 2026-08-13
Keycloak 26.7.1 is digest-pinned and running in production mode against its PostgreSQL co-tenant.
The committed `daher` realm imports automatically with French as default and English selectable;
roles `clinician`, `lab-tech`, and `sys-admin`; application browser clients; and a non-browser
adapter service account. No staff users are committed. DH-008 subsequently changed OpenELIS from
OIDC to SAML after exact application-path verification.

**Runtime verification, not syntax claims:**

- realm import succeeded after removing `_comment` fields that Keycloak rejects;
- all three client-secret placeholders resolved and matched their environment values without any
  secret being printed;
- the adapter service account completed a real client-credentials token grant, with neither its
  secret nor token printed;
- readiness returned healthy only after initialization;
- a restart preserved the realm and cleanly logged `already exists. Import skipped` followed by
  `Import finished successfully`; and
- the pulled image digest matches the digest committed in compose and ARCHITECTURE §12.

**DH-007 boundary:** this verified only the provider foundation. DH-008's protocol plumbing and
application configuration are recorded separately below.

**Session lifetimes approved by Liban 2026-08-12 (ADR-013):** 1h idle / 10h maximum / 5m access
token. These are the already-verified committed values. Revisit after observed pilot workflow;
changing them remains a human workflow/security decision.

### DH-008 · SSO wiring · P0 · 🟢 PLUMBING BUILT + VERIFIED · 🛑 ACTIVATION BLOCKED (BLOCKER-5)

The prior OIDC-for-both conclusion was wrong. The deployed OpenELIS 3.2.1.11 WAR does contain an
OIDC client and configuration keys, but exact source at tag commit `e2a6bdf7` shows the OAuth path
uses `CustomFormAuthenticationSuccessHandler`. It accepts `UserDetails`; an OIDC login supplies an
`OidcUser`, so login information remains null, authentication is cleared, and the handler returns
to `LoginPage`. SAML uses `CustomSSOAuthenticationSuccessHandler`, the native working path also
used upstream. This is a demonstrated implementation defect, not an inference from missing keys.

**Built:**

- OpenMRS OIDC through official OAuth2 Login 1.5.0, built from exact tag commit
  `2770233627343a53719ec981e997d94b1d90b7ad` in a digest-pinned Maven image;
- atomic installation of that OMOD into both fresh and already-populated OpenMRS data volumes;
- mode-`0600` runtime configuration that stores only `oauth2.enabled=false` when SSO is off;
- OpenELIS SAML configuration against the same `daher` realm while retaining form login;
- fresh-realm OpenMRS OIDC and OpenELIS SAML clients; and
- an idempotent, non-deleting `keycloak-sso-config` job, because `--import-realm` deliberately
  skips an existing realm.

**Runtime verification 2026-08-13:** both OpenMRS targets built successfully and the exact module appeared in
the live persistent volume byte-for-byte; REST reported version 1.5.0 registered and stopped while
the local admin still authenticated. Keycloak recovered through a clean realm-import startup,
reconciliation passed twice consecutively, and live inspection showed exactly the expected client
protocols and unique mapper names. A network-isolated ephemeral Keycloak import also proved the
fresh-realm JSON. The normal one-command stack returned healthy/running with SSO disabled. The
mapper fragments were deliberately moved outside the realm import directory after a runtime test
proved Keycloak otherwise tries to parse them as complete realms.

**Not claimed:** the acceptance criterion remains one Keycloak login, both applications, no second
prompt. OpenMRS consumes exact native role names and separately auto-creates a provider unless the
`provider` claim is false; OpenELIS SAML consumes exact native roles from `Role`. Mapping the three
realm roles to those privileges changes access to clinical records and laboratory functions.
`provider` is therefore hard-coded false, `SSO_ENABLED` defaults false, and activation is blocked
until Liban approves the authorization matrix in BLOCKER-5.

### DH-014 · Backup, both engines · P0 🔒 · 🔨 LOCAL PATH VERIFIED 2026-08-13 — off-site unverified
The scheduled sidecar is built from a digest-pinned PostgreSQL 14.4 base and includes matching
`pg_dump`, a MariaDB client, live Keycloak export, required read-only volume mounts, per-component
SHA-256 manifest entries, AES-256-CBC/PBKDF2 encryption, optional S3-compatible upload, retention,
and an explicit success/failure marker.

**The local path is genuinely exercised**, not merely written:

| Dump | Result |
|---|---|
| MariaDB `openmrs` | ✅ 15.9 MB, and it **contains the data**: test identifier `10001MJ` present, with real `patient` and `person_name` INSERTs |
| PostgreSQL `clinlims` | ✅ 1.28 MB |
| PostgreSQL `keycloak` | ✅ 250 KB after DH-007 — populated realm database |
| PostgreSQL `adapter` | ✅ DH-024's fresh archive contains the DH-010 schema (`alembic_version`, ledger, dead-letter table) and zero operational rows; `pg_restore --list` passed |
| Live Keycloak realm export | ✅ 59 KB from the admin API; staged only inside the encrypted archive |
| OpenMRS data volume | ✅ 349 MB compressed; includes OCL/module data and future complex observations |
| Adapter audit volume | ✅ mounted and archived; DH-010 now owns it, verified mode `0600`, UID/GID `10001:10001`, currently zero bytes before message transport |
| Encryption + readability | ✅ archive created, encrypted, decrypted, and component filenames read back |
| Scheduler | ✅ calculated the next configured UTC run while retaining container environment |

The test explicitly set `BACKUP_REQUIRE_OFFSITE=false`; no data left the Docker host. Its ephemeral
key was not retained, so the exact test archive and success marker were deleted after verification
rather than left looking like a usable backup.

**NOT yet done:** no S3-compatible upload has run, no production encryption key has been placed in
escrow, and **no restore has been attempted**. Supplying an external bucket changes where encrypted
patient data leaves the system and requires Liban's explicit approval.

⚠️ **DH-027G (2026-08-21) wired this sidecar's configuration into provisioning** — a facility
created through the control plane now actually gets `BACKUP_ENCRYPTION_PASSPHRASE`/`BACKUP_S3_*`
in its env file when the operator supplies them, and the fleet's `backup_state` reflects a real
`BACKUP_NOW` report instead of a hard-coded placeholder. That is a provisioning-wiring fix, not new
evidence for the boundaries above: off-site upload is still unexercised and no restore against a
provisioned (rather than hand-built) facility has happened. See DH-027G's own entry, below DH-027D.
Per AGENTS.md the pinned item is "backup **plus a performed restore drill**" — this is neither
finished nor a substitute for DH-015.
`mariadb-dump` + `pg_dump` (all three DBs) + adapter audit volume + OpenMRS data volume +
Keycloak realm export → encrypted → S3-compatible. **Scheduled deliberately here, in parallel
with SSO, not after the adapter** — it only needs databases, and putting it last is how pinned
work gets squeezed (`CURRENT_STATE.md` §7).

### DH-015 · Restore drill, performed and documented · P0 🔒 · 🔨 PERFORMED 2026-08-13, PARTIAL COVERAGE
**Performed, not `DONE`.** Full evidence in `backup/restore-drill.md`.

A real encrypted archive was decrypted, all seven component SHA-256s independently re-verified
against its manifest, and the contents restored into an isolated `daher-restore-*` scratch stack
with no published host ports. The clinical record was then read back **through the application**:
`GET /ws/rest/v1/patient?q=10001MJ` returned `10001MJ - Test-DH002 Verification`, FHIR R4 metadata
answered 200, the configured admin authenticated, `Admin123` stayed refused, and OAuth 2.0 Login
1.5.0 came back `started=false` — SSO remained fail-closed across the restore. The OpenELIS admin
bcrypt hash was byte-identical to live.

**Two defects found, both now documented with the corrected procedure:**

1. `pg_restore --clean --if-exists` over the OpenELIS image's pre-populated `clinlims` produced
   **213 errors including failed row inserts** — silent data loss behind a plausible-looking run.
   The restore must **drop and recreate** each database first; done that way, all three restored at
   exit 0 with zero errors.
2. A scratch/recovery stack must gate on `/proc/1/comm = postgres` (DH-024 finding 9 reproduces
   otherwise) and must mount `harden-tenancy.sh`, or init dies at exit 127 *after* creating the
   databases.
3. **The corrected drop/recreate procedure silently discards DH-020 tenant isolation.** A freshly
   created PostgreSQL database grants `CONNECT` to `PUBLIC`, and `--no-privileges` does not restore
   the dump's grants; measured directly, an unrelated role then connects successfully. Every restore
   must re-run `harden-tenancy` and **pass all nine tenancy cases as an acceptance gate**. This was
   found after the drill ran, so that gate was *not* exercised on 2026-08-13 — it is listed as unmet
   in the drill doc §6.

**The measurement that matters for recovery time:** restoring the database *with* the
`openmrs-data` volume reached first HTTP 200 in **41 seconds**, against the documented ≈49-minute
cold boot dominated by OCL imports. The volume is not optional.

**Why this is not `DONE`.** Two acceptance legs could not be exercised and the drill must be
re-run once they can: **a known lab result** (none exists anywhere — `clinlims.analysis`, `.sample`,
`.patient` are all 0 rows on live and restored alike, because DH-011/DH-012 are blocked) and **a
working SSO login** (fail-closed pending BLOCKER-5). Off-site remains unexercised (DH-014).
🗓 A question for Liban is recorded in the drill doc §6: whether to hand-enter a result in the
throwaway stack to close the first leg. ADR-016 makes manual entry the v1 workflow, but it writes
fabricated clinical values, so it was not an agent's call to make.

### DH-010 · Adapter skeleton: audit ledger, idempotency, retry · P0 · ✅ DONE 2026-08-13
Processed-message ledger keyed on stable source event ID with a unique constraint; append-only
structured audit log on a named volume; exponential backoff with jitter; dead-letter table. Two
separate mechanisms by design — the ledger answers "did we do this", the audit answers "what did
we do, to whom, when".

**Implemented and verified:** Python 3.13.14 base pinned by pulled digest; exact Python dependency
versions; Alembic migration; unique source-event claim; guarded `processing` → `completed` or
`dead_letter` transitions; stack-owned dead-letter payload retention; strict audit schema that
rejects extra/clinical-value fields; `O_APPEND` + lock + `fsync` JSONL writes; transient-only
exponential jitter retry; and liveness/readiness checks for PostgreSQL plus the audit volume.

Container tests: dependency consistency, Ruff lint/format, and **14 pytest tests passed**. Runtime:
migration applied to the real adapter PostgreSQL database; Compose health passed; expected tables
and named uniqueness constraints inspected; operational rows remained 0; process is UID/GID 10001;
audit file is mode `0600`; and the read-only root filesystem rejected a write. A restart returned
healthy without reapplying the migration. No FHIR message was moved—that is DH-011/012.

**Boundary corrected by the post-commit audit:** the repository intentionally returns an existing
claim's actual state, including `processing`; it does not yet reconcile that state after a process
dies between an external write and `mark_completed`. The schema/skeleton remain complete for
DH-010's transport-neutral scope, but “unique claim” must not be read as end-to-end exactly-once
delivery. DH-025 closes that transport-specific gap before activation.

### DH-025 · Reconcile ambiguous in-flight adapter writes · P0 · 🔨 CORE BUILT + VERIFIED; TARGET BINDING PENDING
**Raised:** 2026-08-13 post-commit quality audit. **Human Decision Required: No** — technical
correctness; does not select clinical content or workflow.

A crash can occur (a) before an outbound write, (b) after the target accepted it but before the
ledger commits `completed`, or (c) after completion. Cases (a) and (b) both restart as
`processing`, but only (a) is safe to replay blindly. Elapsed time cannot distinguish them.

**DoD before DH-011 activation:** correlate every outbound resource to the stable source event;
on a stale claim, query the target first; mark complete if the write exists, retry only when its
absence is proven, and leave the claim blocked/operator-visible if the answer is ambiguous. Test
crashes immediately before and after the target write, concurrent claims, completed duplicates,
dead letters, and restart/watermark behavior. `processing` and `dead_letter` must never be treated
as completed duplicates.

**Built and verified 2026-08-13:** `source_correlation()` hashes the full, unambiguous source-event
tuple under `urn:daher-health:adapter:source-event`. `PostgresRecoveryFence` is acquired during the
FastAPI lifespan and held until process shutdown, so recovery and future transport cannot run in a
second adapter process. Readiness also requires that fence. `RecoveryCoordinator` accepts only a
typed target result: exactly one match, proven absence, or ambiguous. It reconciles the first,
retries the second, and raises without state advancement for the third; completed duplicates and
dead letters stay separate paths.

Dependency consistency, Ruff lint/format, and **27 tests** passed. Tests cover stable/versioned
correlation, two real concurrent claims, no-fence refusal, missing/completed/dead-letter states,
ambiguous lookup, failed retry write, and crashes before and after the write. Against live
PostgreSQL, two fence owners could not coexist; a rollback-only coordinator probe completed and
then left the row count unchanged. The deployed adapter retained the fence for its lifetime,
refused a second process, returned healthy, and reacquired after restart. Both operational tables
remain empty.

**Still required to close the ticket:** DH-011's concrete OpenELIS writer must attach this exact
correlation to the chosen Task/ServiceRequest, and the probe must demonstrate exactly one match or
proven zero matches against that API. Multiple matches or an unavailable lookup are ambiguous and
must stop startup. This cannot be honestly claimed before the target resource shape exists; no
transport or clinical order was activated here.

### DH-011 · Adapter: order path OpenMRS → OpenELIS · P0 · 🛑 BLOCKED ON BLOCKER-6 (target ingestion path)
The accepted 15-second inclusive watermark remains the implementation design.

**Decided (ADR-015):** the first watermark starts at deployment time — no historical backfill. It
must be read from the OpenMRS server clock, persisted once, and never re-derived on restart; the
inclusive lower bound still applies at the boundary. Nightly reconciliation counts only from that
bootstrap watermark.

**Decision-blocked no longer** — BLOCKER-3 closed 2026-08-14 (ADR-015, ADR-018, interim requester).
The earlier "blocked on BLOCKER-3 legs 2–3" text was stale and is corrected here.

**Evidence gathered 2026-08-16, read-only, against the running stack — before any transport code.**
Two findings, one helpful and one that stops the ticket:

**✅ The DH-025 correlation binding is feasible, and the control proves it.** OpenELIS's HAPI 6.6.0
advertises `identifier` search on `Task` (29 params), `ServiceRequest` (31) and `DiagnosticReport`
(26). A search in our exact correlation shape —
`Task?identifier=urn:daher-health:adapter:source-event|<hash>` — returns **HTTP 200, Bundle,
`total: 0`**, which is precisely the "proven absence" that `TargetPresence.ABSENT` requires. The
control is what makes that meaningful: an unknown search parameter is **rejected with HTTP 400
`HAPI-0524`**, so a 200 here means the parameter is genuinely supported rather than silently
ignored. Exactly-one and zero-match behaviour still needs demonstrating against a real resource.

**❌ `_sort` is silently ignored by OpenMRS `fhir2` 4.2.0** — ascending, descending and a nonsense
sort key return byte-identical order. The accepted design's pseudocode relies on server-side
ordering. Correction, evidence and the required client-side handling are appended to
`docs/WATERMARK-DESIGN.md` §3. This does not change the accepted contract; it changes how the
contract is met.

**🛑 ADR-018's status mapping cannot be verified yet, and that is a finding rather than a delay.**
"Accepted or started" must map to a *verified* OpenELIS status. With `electronic_order`, `sample`
and `analysis` all at **0 rows** and the FHIR store empty, there is no observed status vocabulary to
map to — and a `status=` search cannot discriminate anything against an empty store (a bogus status
value returns 200/`total: 0` exactly like a real one). This gate is not passable by probing; it
needs one order to have actually flowed.

**🛑 Blocked on BLOCKER-6:** how an order enters OpenELIS at all. The inbound mechanism is a **poll
from OpenELIS** (`remote.source.uri`, currently empty), not a push into its FHIR store — which is
where every document in this repo implicitly assumed the adapter would write. That fork changes what
the adapter is, so it is a human decision.

**Buildable regardless of BLOCKER-6:** the OpenMRS-side poller, the durable watermark, the
bootstrap-at-deployment-time rule, and client-side ordering. Not started — recording the design
contradiction and the blocker came first.

#### DH-011 sub-ticket lettering — fixed 2026-08-16, read this before using an ID

The 2026-08-16 roadmap lettered these sub-tickets, and one ID briefly carried two meanings
(`DH-011A` was used for both "document Option F" and "the OpenMRS poller"). **The roadmap lettering
below is authoritative.** The poller is **DH-011E**, not DH-011A.

| ID | Scope | Status |
|---|---|---|
| **DH-011A** | Record Option F as a candidate | ✅ **DONE 2026-08-16** — ADR-026 |
| **DH-011B** | Runtime spike: OpenELIS native business FHIR ingestion, **synthetic data only** | ✅ **COMPLETE 2026-08-17** (authorized by Liban) — Option F writes native domain rows; ServiceRequest gated on a numeric site code, correlation does not survive |
| **DH-011C** | Prove DH-025 correlation: 0 / 1 / 2+ match behaviour on the real target | 🟡 **Half answered, corrected 2026-08-17.** The *lookup* half is proven on `Patient`/`Practitioner` — 1 for a real identifier, 0 for absent, and 0 for a wrong system with the right value. What fails is that a *client-supplied* identifier is not retained → DH-011D. Unproven on `ServiceRequest` until the accession blocker clears |
| **DH-011D** | Narrow OpenELIS patch to **persist** an incoming external order identifier. Search already works, so this is smaller than first scoped. Needs a superseding ADR (ADR-016/BLOCKER-4) | 🔓 **Condition met 2026-08-17** — the identifier is discarded on ingestion. ⚠️ Verification is gated on NEEDS LIBAN #7, and whether `ServiceRequest` behaves like `Patient`/`Practitioner` here is **untested** |
| **DH-011E** | OpenMRS poller + durable watermark. **Transport-neutral**, unblocked by BLOCKER-6 | ✅ **BUILT + VERIFIED 2026-08-17, NOT ACTIVATED** — see below |
| **DH-011F** | Watermark/paging test matrix | ⏸ with DH-011E |
| **DH-011G** | Patient identity mapping — controlled identifiers, fail closed on ambiguity | ⏸ |
| **DH-011H** | Practitioner/requester mapping (NEEDS LIBAN #6) | 🗓 meeting |
| **DH-011I** | Version-controlled test mapping registry; never fuzzy-match test names | 🛑 clinical sign-off |
| **DH-011J** | Cancellation via `replaces`; verify real OpenELIS status transitions | 🛑 **Not testable yet** — DH-011B could create no order, so no native transition could be observed. The provider does expose `@Delete`/`@Update`, so the surface exists |
| **DH-011K** | Crash-point qualification across all five points | ⏸ |
| **DH-011L** | End-to-end synthetic order demonstration → closes DH-011 | ⏸ |

#### DH-011E — OpenMRS poller + durable watermark · ✅ BUILT + VERIFIED 2026-08-17 · ⚠️ NOT ACTIVATED

**94 adapter tests (from 27), Ruff clean, migration `0002_watermarks` applied to the live database
and verified.** Nothing starts the loop: `grep` confirms `OrderPoller` is referenced by no compose
service and not by `app.py`. There is no production delivery implementation, because BLOCKER-6 has
not decided what the target is. **No order has moved.**

| Module | What it does |
|---|---|
| `instants.py` | `ServerInstant` — the server's timestamp string, kept byte-exact (§7). Validates the shape, asserts Djibouti's `+03:00`, and orders by a zero-padded key so comparison is exact without ever reformatting what we transmit |
| `source_events.py` | `SourceOrderEvent` (the transport-neutral output — **names no target**) and cohort assembly: dedupe by `(type, id, versionId)`, prove completeness against the bundle's `total`, sort **client-side** |
| `watermark.py` | Durable position, bootstrapped **once** from the server clock (ADR-015) and never re-derived; refuses to move backwards |
| `openmrs.py` | Bounded-window fetch with paging. Read-only — `ServiceRequest` is search/read only on this server anyway |
| `order_poller.py` | The cycle. Claims via the DH-010 ledger, routes stale claims through DH-025's coordinator, and advances the watermark **only** past accounted-for work |

**Two design choices worth knowing, both forced by measured behaviour rather than preference:**

1. **The window is bounded at *both* ends**, which the accepted design did not specify. Without an
   upper bound, records written *during* the fetch land inside a window whose `total` was already
   read, so the completeness check would fire forever on healthy traffic. The top is frozen at a
   **server-issued** instant — taken from the bundle's own `meta.lastUpdated`, which every search
   response carries, so no clock endpoint is needed. Both bounds were verified applied before being
   relied on: an impossible `ge`/`le` range returns 0.
2. **The ledger is only marked `completed` after a real delivery returns a target record.** Marking
   it on mere observation would be the worst possible bug here — when the target leg lands, every
   order polled beforehand would look already-done and never be delivered. Silent loss, at scale.

**Five controls, each breaking a guard deliberately:**

| Control | Result |
|---|---|
| Remove the client-side sort | **5 fail**, incl. the reversed-response test |
| Drop the completeness check | **2 fail** |
| Let the watermark advance past a halted event | **4 fail** |
| Filter to `status == active` only | **3 fail** — the cancellation-loss bug |
| Remove the instant zero-padding | **2 fail** |

⚠️ **The fifth control initially did not fail, and that is worth recording.** The original
fractional-width test compared `.10` with `.9`, where naive and padded comparison happen to agree —
for decimal fractions leading digits dominate, so plain lexicographic order is otherwise correct.
The test proved nothing and 92 tests passed with the padding removed. The real case padding fixes is
**equal moments written at different precision**: `.1` and `.10`. Under naive comparison `.1` reads
as *earlier* than a persisted `.10`, so `advance()` would reject a legitimate re-poll as a
regression and stop the poller. Both tests were rewritten around that, and the control now fails
correctly. **A guard that is never control-tested is a guess** — this is the eighth instance in this
repo of a check passing for reasons unrelated to its claim.

**Incidental confirmation:** running the migration through `docker compose run adapter` was refused
with `RecoveryFenceUnavailable: another adapter process owns startup recovery`. That is DH-025's
process-lifetime fence doing exactly its job against a live adapter — an unplanned live control.

**Remaining for DH-011:** everything downstream of BLOCKER-6 — the delivery implementation, DH-011C's
correlation binding, DH-011G/H/I mappings, DH-011K's crash-point qualification, and DH-011L's
demonstration. The 15-second interval and the loop that calls `poll_once` are deliberately not wired
either; activating a poller with no target would be pointless and would invite a false green.

#### DH-011B — full runtime spike · ✅ COMPLETE 2026-08-17 · explicitly authorized by Liban · synthetic data only

**Verdict: Option F writes genuine native OpenELIS domain state and is NOT a FHIR façade. It cannot
be completed here for two separate reasons, neither of which is an engineering choice.**

**Endpoint, and how it differs from the other one.** Two distinct FHIR servers exist in this stack and
conflating them is the mistake this spike existed to prevent:

| | Business API (Option F) | Standalone store (Option C) |
|---|---|---|
| URL | `https://oe.openelis.org:8443/OpenELIS-Global/fhir/` | `https://fhir.openelis.org:8443/fhir/` |
| Server | HAPI **7.0.2**, embedded in the webapp | HAPI **6.6.0**, separate container |
| Registered by | `AnnotationWebAppInitializer` → `fhirServlet` (`FhirRestfulServer`) at `/fhir/*` | its own image |
| Auth | **HTTP Basic** (`SecurityConfig.BasicAuthRequestedMatcher`); a form-login cookie is refused on POST with `access=denied` | mutual TLS |
| Providers | OpenELIS's own 8 business providers | generic JPA storage |

**Baseline before any write:** `patient 1, person 1, provider 0, sample 0, sample_item 0, analysis 0,
electronic_order 0`. (The single patient was created by this spike's first pass, already committed.)

**Requests made** — all through the supported FHIR surface, all clearly labelled synthetic:

| # | Request | Result |
|---|---|---|
| 1 | `POST /fhir/Practitioner` — `Dhprac Zzsynthetic`, identifier `DHSPIKEPRAC1` | **201**, and returned the created resource body |
| 2 | `POST /fhir/ServiceRequest` — LOINC `94500-6`, subject + requester + `authoredOn` | **400** `Generated lab number is invalid` |
| 3 | Same, plus `occurrenceDateTime` | **400**, identical |
| 4 | Same, plus a supplied `requisition` (synthetic `9990000000001`) | **400**, identical |
| 5 | Same, plus a supplied accession `identifier` (synthetic `9990000000002`) | **400**, identical |

**✅ Native domain changes observed — the headline.** `PractitionerProvider` logged
`Successfully created Practitioner with UUID: …` and a real `clinlims.provider` + `clinlims.person`
row exists (`id=2`, `fhir_uuid bac7daf3-…`). Combined with the earlier `clinlims.patient` row, **the
business providers write OpenELIS's own domain tables.** Reading `ServiceRequestProvider` confirms the
mechanism rather than inferring it: it calls `SamplePatientEntryService`, `SamplePatientUpdateData`,
`SampleOrderItem` and `SampleAddService$SampleTestCollection`, and builds `Sample` and `Analysis` —
the same services the human sample-entry workflow uses. Option F is real.

**🛑 But the laboratory workflow was never reached.** `sample`, `sample_item`, `analysis` and
`electronic_order` are **all still 0**. Every ServiceRequest was refused before any of it.

**Root cause of the refusal, and it is not fixable by an engineer.** The provider calls
`getNewAccessionNumber()` then `validateNewAccessionNumber()`. The configured format is
`acessionFormat = SITEYEARNUM` (upstream's typo) with `Accession number prefix = DEV01` and
`validateAccessionNumber = true`. Parsing `SiteYearAccessionValidator`'s constant pool shows its
format string is **`%013d`** — a **13-digit, all-numeric** accession. `DEV01` is alphabetic, so the
generated value cannot satisfy it. A **numeric facility site code is required**, and a laboratory
accession number is a facility identifier convention, not an engineering default —
**NEEDS LIBAN #7**. Supplying a synthetic accession does **not** work around it: variants 4 and 5
proved the value is always server-generated and a client-supplied one is ignored.

**🛑 Correlation result: the submitted identifier is discarded. (Corrected 2026-08-17 — see below.)**

| Query | `total` |
|---|---|
| `Practitioner?identifier=DHSPIKEPRAC1` — an identifier we **did** submit | **0** |
| `Practitioner?identifier=NEVER-SUBMITTED-XYZ` — **negative control** | **0** |
| `Patient?identifier=DHSPIKEPONE` — submitted on the first pass | **0** |

**The identifier is discarded on ingestion.** The returned Practitioner carries only OpenELIS's own
`provider_uuid` and `facility_id` identifiers; ours is absent, and
`clinlims.provider.external_id` is **NULL**. The submitted correlation is not persisted anywhere.

> ### ⚠️ CORRECTION 2026-08-17 — this section originally overstated the failure
>
> The first write-up concluded that **search itself was broken**, on the grounds that every search
> response's `self` link pointed at `http://fhir.openelis.org:8080/fhir/…`, a port this stack does
> not serve. From that it inferred the DH-025 0/1/2+ contract was *"inexpressible through this
> surface at all"*. **That inference was wrong, and it was wrong in the expensive direction** — it
> would have justified a far larger OpenELIS patch than the evidence supports.
>
> The `:8080` self link is a cosmetic HAPI artifact. Re-tested directly:
>
> | Query | `total` |
> |---|---|
> | `Practitioner?_summary=count` (any) | **1** |
> | `Practitioner?identifier=<OpenELIS's own provider_uuid>` | **1** — proven present |
> | `Practitioner?identifier=0000…0000` (control) | **0** — proven absent |
> | `Practitioner?identifier=NEVER-SUBMITTED-XYZ` (control) | **0** |
> | `identifier=http://openelis-global.org/provider_uuid\|<uuid>` — full `system\|value` | **1** |
> | `identifier=urn:wrong-system\|<same uuid>` — **control** | **0** |
>
> That last row is the decisive one: a **wrong system with the right value returns 0**, so the
> search performs real token matching rather than ignoring the system or substring-matching.
>
> **Corrected conclusion: exact identifier lookup on this surface WORKS, and the DH-025 contract is
> expressible.** The single remaining failure is that OpenELIS does not retain an identifier the
> *client* supplied. That narrows DH-011D from "add a searchable external identifier, including
> search support" to "**persist the incoming identifier**" — the search half already exists.
>
> Still unknown, and not inferable from Patient/Practitioner: whether `ServiceRequestProvider`
> retains a submitted identifier. It is a different provider and may behave differently. That cannot
> be tested until the accession blocker is resolved.

**Cancellation: not testable.** It requires an existing order, and no order could be created. The
provider does expose `@Delete` and `@Update`, so the surface exists; its native state transition
remains unobserved. Deferred with the accession blocker.

**Controls used:** wrong-password → 401 (auth genuinely enforced); unknown search parameter → 400
`HAPI-0524` while advertised `_id` → 200 (so a 200 means real support); the never-submitted-identifier
negative control above; and read-back rather than status code throughout, because of bug 19.

**Nothing was modified to make the spike pass.** Laboratory tests unchanged (**177 active**),
`site_information` unchanged (121 rows), no accession setting altered, no role or permission granted,
no database row written or deleted directly. Every write went through the supported FHIR surface.

**Synthetic resources created, retained deliberately, never to be confused with real data:**

| Resource | Native row | FHIR uuid |
|---|---|---|
| Patient `Dhspike Zzsynthetic` (first pass) | `clinlims.patient id=2` + person | `b010ea84-aab8-4917-ad44-7ba155dff2d5` |
| Practitioner `Dhprac Zzsynthetic` | `clinlims.provider id=2` + person | `bac7daf3-f68a-490c-bf9c-eaebfe520169` |

Both also exist in the standalone HAPI store. No sample, analysis or electronic_order was created. Not
deleted, per this repo's standing rule against removing patient rows to tidy up.

**Verdict against the stated rules.** Native domain state **is** created, so Option F does not fail —
rule 3 does not apply. Correlation **fails**, so **rule 2 applies: Option F remains preferred, and
DH-011D (narrow correlation preservation and search) is the follow-on.** With one addition the rules
did not anticipate: DH-011D cannot be *verified* end to end until a numeric site code exists, because
proving exact search needs a created ServiceRequest. So the facility answer gates the proof, not the
patch.

#### DH-011B — first pass · ✅ PERFORMED 2026-08-17 · synthetic data only

**Option F is real, not a façade — and it is not usable yet.** Both halves are demonstrated.

**The endpoint.** `AnnotationWebAppInitializer` maps `fhirServlet` (`FhirRestfulServer`, a HAPI
`RestfulServer` holding the business `IResourceProvider`s) at **`/fhir/*`** inside the webapp:
`https://oe.openelis.org:8443/OpenELIS-Global/fhir/`. It is **HAPI 7.0.2 embedded in the webapp** —
a different server from the standalone `openelis-fhir` container's HAPI 6.6.0. The compose comment
calling that container "the endpoint the adapter actually talks to" describes option C, not this.

**Authentication is HTTP Basic, and form-login is the wrong mechanism.** `SecurityConfig` has a
`BasicAuthRequestedMatcher` (`haveBasicAuth`) selecting a separate `httpBasicServletFilterChain`.
A cookie session obtained by form login reaches `GET` but is refused on `POST` with
`302 → /Home?access=denied`. With `Authorization: Basic` the same POST succeeds. Control: a wrong
password returns **401**, so the 200s are authenticated rather than open.

**✅ The providers create genuine native OpenELIS state.** `PatientProvider` logged
`Patient persisted with ID: 2` and a real `clinlims.patient` + `clinlims.person` row exists with a
`fhir_uuid`. This is the question the spike existed to answer: Option F is **not** a FHIR façade
over the generic store.

**🛑 But `PatientProvider` returns `201 Created` when the native persist FAILS.** Proven by a
control pair — two identical requests differing only in the patient's given name:

| Request | HTTP | Log | Native rows |
|---|---|---|---|
| given name `Dh011b` (contains digits) | **201** | `ERROR … invalid name format, possibly illegal character` | **0** |
| given name `Dhspike` (letters only) | **201** | `Patient persisted with ID: 2` | **1** |

Same status code, opposite outcomes, and **no `Location` header in either** — so a client cannot
learn from the response whether anything was created, nor what its id is. **An adapter that trusted
this 201 would mark an order delivered that never existed**, writing `completed` into the DH-025
ledger for a write that did not happen. That is the silent-loss direction `WATERMARK-DESIGN.md`
exists to refuse. Recorded as **known bug 19**. Any option-F implementation must verify by read-back
rather than by status code.

**🟡 `ServiceRequestProvider` behaves better and is blocked on configuration.** A synthetic
ServiceRequest (existing upstream LOINC `94500-6`, referencing the synthetic patient) returned
**HTTP 400 `Generated lab number is invalid`** and correctly created nothing — so the false-green is
specific to `PatientProvider`, not universal. The cause is deployment configuration: the accession
prefix is upstream's placeholder `DEV01` with `validateAccessionNumber=true`. **This ties Option F
to facility identity (NEEDS LIBAN #7)** — the lab number is a facility-scoped accession, so it is a
real-world value, not an engineering guess. Option F is therefore **not disproven; it is unfinished**.

**🛑 The DH-025 correlation cannot bind to ServiceRequest on this server.** `ServiceRequest` does
**not** support `identifier` search here — it is rejected with **HTTP 400**, and the controls make
that meaningful: an unknown parameter is also 400, while the advertised `_id` returns 200.
`Specimen`, `Patient`, `Practitioner` and `Organization` **do** accept `identifier` (200).

| Server | ServiceRequest `identifier` search |
|---|---|
| Standalone HAPI 6.6.0 (option C target) | ✅ supported — 200, `total: 0` |
| **Business API HAPI 7.0.2 (option F target)** | ❌ **400, rejected** |

So the two options trade off exactly against each other: the one that reaches native lab state
cannot be queried by our correlation, and the one that can be queried may not reach native lab
state. This is what **DH-011C** must resolve, and it is the concrete trigger for **DH-011D**'s
narrow patch if no alternative works. A possible alternative worth testing before any patch:
`Specimen` *is* identifier-searchable, so the correlation may be bindable there — but a specimen
identifier is an accession-numbering concern in a laboratory, so that is laboratory configuration
surface and not an agent's call.

**Synthetic data left in the throwaway stack, deliberately not deleted:** one OpenELIS patient,
`id=2`, `Dhspike Zzsynthetic`, `fhir_uuid b010ea84-aab8-4917-ad44-7ba155dff2d5`. No sample, no
analysis, no electronic_order — all still 0. Consistent with this repo's standing practice of never
deleting patient rows to tidy up (the 100 fictional OpenMRS demo patients were kept for the same
reason). This stack remains a throwaway verification stack and must never be confused with a clean
instance. **No OpenELIS laboratory test, mapping, or configuration was modified.**

**Still true:** no transport code has been added, no order has been sent, and nothing has been
written into OpenELIS. DH-006's signed-off test mappings remain required before any *pilot* order.
### DH-012 · Adapter: result path OpenELIS → OpenMRS · P0 · ⏸ (needs DH-011)
Record observed `ServiceRequest` / `DiagnosticReport` shapes here → triggers the ADR-004 revisit.

### DH-013 · WhatsApp notifier against mock · P0 · ⏸ (needs DH-012) · 🔒 gate
French default + English templates; content-free ("results are ready", never values). Notifier is
a module inside the adapter (ADR-002). **Status on completion of the mock work is `BLOCKED`, not
`DONE`** (ADR-003).

### DH-016 · Caddy, TLS, subdomains · P0 · 🔨 BUILT + LOCALLY VERIFIED 2026-08-13; public issuance pending #15
Subdomain routing per ADR-008, proxying to the retained O3 gateway per ADR-010. Caddy is the only
container publishing host ports.

**Routing verified through the proxy** (`emr`/`lab`/`id`): OpenMRS 301 (its gateway's own redirect),
Keycloak 302, OpenELIS SPA 200. Security headers (HSTS, nosniff, SAMEORIGIN, Referrer-Policy) present.

**The finding that shaped the config:** OpenELIS's SPA and API **must share one origin**. Reading the
built bundle showed it calls `/OpenELIS-Global/...` as a *relative* path, and its nginx serves static
files only with no API proxy. So `lab.<domain>/` serves the SPA while `lab.<domain>/OpenELIS-Global/*`
proxies to Tomcat. Verified by content, not status code — the API path returns Tomcat's real login
page containing `_csrf`, which the SPA fallback does not.

**The API hop is TLS-verified, not skipped.** DH-021's generated certificate is exported as a PEM so
Caddy has a real trust anchor, rather than `tls_insecure_skip_verify` leaving the edge→backend hop
unauthenticated. Proven by control: swapping in an unrelated CA produced **502 with
`x509: certificate signed by unknown authority`**, and restoring it returned 200.

⚠️ **The first run of that control was invalid and said so falsely.** `docker compose up -d caddy`
re-ran the `openelis-certs` dependency, which overwrote the bogus CA with the real one before Caddy
started — so it "passed" while proving nothing. `--no-deps` gives the real answer. Any future
control that swaps a file a dependency also writes must do the same.

**Remaining before DONE:**
1. Public certificate issuance needs the real `BASE_DOMAIN` and DNS (NEEDS LIBAN #15). With
   `localhost` this uses Caddy's **internal CA** — that proves routing, origins and cookies, and
   proves nothing about ACME.
2. The `email` directive is deliberately absent: Caddy **fails to parse** the whole file if `email`
   is present but empty, taking the proxy down rather than degrading. Add a real address with the
   real domain.
3. `openmrs-gateway`'s `127.0.0.1:8080` publish is **deliberately retained for now**. Removing it is
   part of this ticket, but with no real domain the only route in would be
   `https://emr.localhost:8443` behind a locally-trusted CA — a worse position than today. Remove it
   with #15.

### DH-017 · README for a fresh Ubuntu 24.04 VM · P0 · 🔨 BROUGHT CURRENT 2026-08-13
Now covers the whole deployed stack rather than step 1 only: purpose, honest status, doc map,
prerequisites, `.env` setup, first-boot timing, demo-data controls, maternity beds, **the reverse
proxy and how to actually reach the system**, **a whole-stack verification section**, and
**backup/restore with the drill's two traps**.

Additions this pass:

- A **required-values table** — everything that must be set before the first `up`.
- ⚠️ **"An empty value is not the same as a safe default"**, with DH-021's concrete case: empty
  `OE_KEYSTORE_PASSWORD` silently selected upstream's published `kspass` through a `:-` fallback.
- The DH-016 access model, including the constraint that OpenELIS's SPA and API **must share one
  origin**, and the warning that an empty `email` makes Caddy fail to *parse* its config.
- Restore guidance pointing at `backup/restore-drill.md` for the `--clean` data-loss trap and the
  tenant-isolation loss that drop-and-recreate causes.
- Verification commands that assert **application behaviour, not open ports** — the mistake this
  project has now made three separate times.

**All six documented verification commands were run verbatim against the live stack** and produce
the stated output.

**Not yet closed.** The DoD is "someone else could deploy a second instance without asking Liban
anything", and two things still require him: the real domain/DNS for public TLS, and the facility
values (bed counts, requester identity). A second *local* instance is now reproducible from this
README alone. It should also be re-walked on a genuinely fresh VM before being called DONE —
documentation verified only against an already-working machine is exactly the kind of claim this
repo distrusts.

### DH-018 · Golden end-to-end demonstration · P0 · ⏸ (needs DH-013, DH-015)
All eight steps of §8, demonstrated live to Liban, including the retry/restart no-duplicates check.

### DH-026 · Facility parameterisation (multi-facility foundation) · 🔨 STARTED 2026-08-15
First ticket of the provisioning API + portal work. The Compose project name is now
`daher-${FACILITY_ID:-default}`, which namespaces every container, volume and network so two
facilities can run on one host.

**A hazard found while doing it, and closed.** The running stack's 14 volumes were created under
the project name `daher-health`. An unset `FACILITY_ID` resolves to `daher-default`, so the next
`docker compose up` would have built a **new empty stack beside the existing one** and orphaned the
patient database — while appearing to start perfectly normally. `FACILITY_ID=health` is now pinned
in the local `.env`, verified to resolve back to the same 11 containers and 102 patients. The same
trap is documented in `.env.example`: this value is an identity, stable for the life of the
deployment, never edited afterwards.

**Collision survey (what actually clashes between two facilities on one host):** only the project
name and the three published host ports. The 7 network aliases live on each stack's own network and
the 12 named volumes are namespaced by project name, so both are free. Locally built image tags are
deliberately shared — build once, run many.

**Still open before facility #2 is real:** the host-port model (only one Caddy can own 80/443, so
either one shared edge proxy or per-facility loopback ports behind one), automated DNS/TLS, and the
per-facility secret generator.

### DH-027 · Provisioning API foundation · 🔨 IN PROGRESS 2026-08-15
New system under `provisioning/`, separate service and deploy, sharing this repo's decision
record. Python/FastAPI to match the adapter (ADR-020).

**Answers received 2026-08-15:** one VM per facility by default but **deploy target is a field on
the facility record**, not a hardcoded assumption; DNS built against a provider-agnostic interface
and left explicitly unconfigured until a domain exists; portal is a self-contained FastAPI + Jinja2
app rather than integrating with a website that does not exist yet.

**Built and verified — 159 tests, Ruff clean:**

| Module | What it does |
|---|---|
| `secrets.py` | Per-facility credentials for all 16 stack variables. Encodes constraints learned by *running* the stack: OpenELIS's narrow `*$#!` alphabet, OpenMRS complexity plus its refusal of "admin", the Compose project-name restriction. `__repr__`/`__str__` redact, tested by formatting into a log line |
| `models.py` | Closed (`extra="forbid"`) schemas, soft-only decommission, `dns_configured` defaulting to **False** |
| `facility_env.py` | Renders/writes a facility `.env` at 0600, created restricted rather than chmod-ed. **Refuses to overwrite** — rewriting is not rotation, it produces a stack whose passwords no longer match its own databases |
| `audit.py` | Hash-chained, `O_APPEND`+`fsync`. Written by the code that performed the action, including failures |
| `auth.py` | Scoped credentials, hashed at rest, constant-time compare, decoy hash so an unknown key id costs the same work |
| `ratelimit.py` | Per-caller token bucket on a monotonic clock |
| `hosts.py` | The deploy-host allowlist and the occupied-host refusal |
| `repository.py` | Facility records. **Holds no clinical credentials**, which is the strongest layer of the guarantee |
| `app.py` | The FastAPI surface wiring all of it together |

**The structural no-clinical-data guarantee (ADR-021).** Three layers: closed schemas; a test
walking every model against clinical vocabulary; and — the only one not depending on anyone staying
careful — **the service holds no clinical database credentials at all**. Control-verified: adding a
`patient_count` field made two independent tests fail; removing it returned to green.

**Deploy transport decided — ADR-022: pull-based host agent, outbound-only.** Options were laid out
with blast radius *before* building. Rejected: remote Docker socket (collapses to "the API is root
everywhere") and signed webhook (a bespoke listener parsing untrusted input, for the same
containment SSH forced-command already gives). Fallback is SSH forced-command.

**The constraint that decided it:** Docker access *is* root access, so the compose file must live on
the facility host and the API sends **parameters, never a spec**. Otherwise "provisioning API" and
"root on every clinical VM" are the same sentence.

**Two escalation paths closed**, both live in code written the same day and both arriving through
requests that look entirely routine:

1. `deploy_target` is resolved from an operator-registered **host allowlist**, not supplied by the
   caller. An address in the body is rejected by the closed schema, not silently ignored.
2. An already-occupied host is a **hard conflict**, never an overwrite — reusing one would clobber a
   live env file and attach a new facility to an existing patient database.

**The API no longer generates facility credentials.** Under ADR-022 the agent generates them locally
when it pulls its job, so a breached API host has **never held** any facility's database passwords.
That is what lets the walkthrough say a compromised API *cannot* reach a clinical database, rather
than that it should not.

**Deliberately incomplete, and each reports its own incompleteness rather than appearing to work:**

- **Deploy** — no host agent exists. `POST /facilities` returns 201 meaning *recorded*, not
  *running*, and lists what did not happen in `incomplete_steps`.
- **DNS/TLS** — no domain or provider (NEEDS LIBAN #15). `dns_configured` is always `false`.
- **Audit log external destination** — the chain makes tampering *detectable*, not *impossible*.
  Surviving a host rebuild needs an external append-only sink that does not exist.
- **Rate limiting** is in-memory and per-process: it resets on restart and N workers multiply the
  limit by N.
- **The portal** is not built.

**Agent job vocabulary and local validation built 2026-08-16 — 200 tests.** `jobs.py` defines a
closed job type carrying **parameters, never a spec**: no field can name an image, command, volume,
or compose file, and a test asserts that across every field of both models. Control-verified by
adding an `image` param and watching it fail with ADR-022's reasoning attached.

`daher_agent/validation.py` implements the five refusals, each tested as an attack rather than a
negative case: wrong host, unknown verb, hostile parameters (twelve `facility_id` payloads —
`; rm -rf /`, `$(whoami)`, backticks, pipes, newlines), stale/replayed jobs, and **provisioning over
a facility that already exists locally**. That last one is the check a compromised issuer cannot
skip, because it lives on the far end.

`FACILITY_ID` is validated at **both** ends, plus a second independent character filter, because it
reaches a Compose project name and the cost of being wrong there is root.

**Enrolment built 2026-08-16 — 220 tests.** The agent generates its long-lived credential **on the
facility host** and sends only the hash, so the API never sees an agent secret even during
enrolment, and a breached API host cannot impersonate any agent past or future. Passing a secret
where the hash belongs is refused, so the dangerous wiring is not reachable by mistake.

Enrollment tokens are **single-use** (replay refused even inside the lifetime), **short-lived**, and
**bound to one host** — a token for `host-a` cannot enrol `host-b`, so a leak cannot introduce an
attacker-controlled host into the fleet. Minting for an already-enrolled host is refused rather than
silently replacing a live agent's credential. Errors never contain the presented token.

**Job queue and poll loop built 2026-08-16 — 240 tests.** Jobs are addressed to one host and
claimable only by it; a claimed job is **held, not deleted**, so a crash between claiming and
finishing leaves work visible rather than silently lost (DH-025's reasoning). `refused` is a
distinct outcome from `failed` — the boundary working must never read as the deployment breaking.

The poll loop's tested property is **staying alive**: an unreachable API, a malformed payload, a
smuggled `image` param, an executor that raises, and a reporter that raises are each survived, and
a hostile job is refused *while the next legitimate job still runs*. An agent that crashes on
hostile input has been switched off by the attacker, and a host that is not polling is also not
getting backups or updates.

⚠️ **The transport is WRITTEN, NOT DEMONSTRATED.** The loop is unit-tested against injected
transport and executor. "Polls a separate host over a network and refuses hostile input over the
wire" needs two hosts, or two containers on separate networks standing in for them, and that does
not exist. Same distinction this repo applies to the WhatsApp mock and off-site backup.

**Two-container demonstration PERFORMED 2026-08-16 — `provisioning/demo/README.md`.** Real API and
real agent in separate containers on a bridge network, exercising the production poller,
validator and HTTP transport. Only the executor is substituted.

**Demonstrated:** authenticated polling over a real socket; 1 of 7 jobs reaching the executor;
6 of 6 hostile payloads refused over the wire; the agent's **own** host check firing on a
deliberately misrouted job; **no inbound port** on the agent (probes to 22/80/443/8000/8080/9000
all refused); "held, not deleted" across a real **SIGKILL** — the job stayed `claimed` and was never
re-handed-out; the agent resuming after process death; and a 14-entry audit chain that verifies.

**Three defects the unit tests structurally could not find:**

1. **The agent's own host check was never exercised.** The queue routed the misrouted job away, so
   the agent never saw it — the demo would have passed while silently never testing that refusal.
2. **A refused misrouted job could not be reported.** The report endpoint authorised on the job's
   *self-declared* `host_id`, a field a compromised issuer controls, so the agent refused hostile
   work and the API recorded nothing. Now authorised on `delivered_to`, recorded at claim time.
3. **The audit call could fail *after* the state change**, leaving a job finished with no record.
   An audit that can fail after the event it records is worse than none, because the gap is
   invisible. Now read defensively, before mutating state.

⚠️ **Honest limits — the transport is PARTIALLY demonstrated.** Five of six refusals came from
*schema* validation, not the agent's deeper `revalidate_params`/character filter, so that second
layer remains proven only by unit tests that bypass the schema. And three things are entirely
untested: **TLS** (the demo runs plain HTTP, so certificate and hostname verification are unproven
while ADR-022 assumes them), **partial/truncated reads**, and **the real executor** — nothing has
ever started a clinical stack through this path.

**TLS demonstrated 2026-08-16.** The demo now runs over HTTPS with a private CA, and **4/4
controls behave correctly**: the real CA succeeds; a bogus CA, a wrong hostname, and the system
trust store alone are each refused with genuine `CERTIFICATE_VERIFY_FAILED` reasons. The controls
are the test — presenting only the correct certificate would pass identically with verification
disabled. Full job flow re-run over TLS: 14 audit entries, 6 refusals, chain verifies, and only the
legitimate job reached the executor.

**Three more things the TLS leg caught**, none reachable over plain HTTP: the CA lacked `keyUsage`
and OpenSSL 3 refused it outright; the hostname control first passed for the **wrong reason**
(`127.0.0.1` had nothing listening, so it never reached TLS — now it hits the real listener by IP
and fails with `IP address mismatch`); and after the HTTPS switch the hostile harness still pointed
at the old plain-HTTP URL, enqueued nothing, and produced a convincing empty result that looked
like success.

**Still not demonstrated:** partial/truncated reads, and the real executor — nothing has ever
started a clinical stack through this path.

**Network exposure decided and implemented 2026-08-16 — ADR-023.** The provisioning API is
**not publicly reachable**: firewalled to the portal server's address, portal on its own domain
separate from any marketing site and from `BASE_DOMAIN`, and the browser never talks to the API.
Rejected a VPN tunnel for now — stronger, but a persistent moving part that only adds protection
against an attacker who has already stolen the portal credential *and* can spoof a source address.
Upgrade path recorded: WireGuard later, **no code changes**.

Implemented as **defence in depth**, because firewalls get edited: `network.py` enforces a source
allowlist ahead of authentication, so an unauthorised source cannot even reach credential
comparison. It uses the **real peer address and never `X-Forwarded-For`** — a test asserts a
request carrying three forwarded headers pointing at an allowed address is still refused. A missing
or unparseable peer is refused rather than allowed, so nothing that obscures the peer becomes a
bypass. 257 tests.

**Compose executor built 2026-08-16 — 276 tests.** The last seam where a parameter reaches a
command. Five rules: **never a shell** (argv list, `shell=False` — removing injection as a
*category* rather than filtering for it); the **compose file is fixed and local**, never from the
job, because an issuer who could name a spec would be root on the host; the facility id validated a
**third** time immediately before becoming an argument; secrets generated **on the host**; and
**`stop`, never `down`** — a test asserts no verb can emit `--volumes` or `-v`, so nothing here can
destroy clinical data.

Tests assert the **exact argv** rather than "it worked" — a test checking only the return value
would pass identically whether the command was a safe list or a shell string with the id
interpolated. Both guards control-verified: removing the executor's validation lets a hostile id
reach the command and fails the suite; `shell=True` fails it too.

**Persistence built 2026-08-16 — 304 tests.** SQLite, one file. Facilities, host registrations,
agent enrolments, API credentials and the job queue all survive a restart; previously an API
restart lost the entire fleet and recovery meant re-enrolling every VM by hand.

Proven end to end through the real app, not just the stores: a facility created before a restart is
still listed after one, the portal credential still authenticates, and a host claimed before a
restart still refuses a second facility with the "existing patient database" reason. "Held, not
deleted" now survives an **API** restart too, not only an agent crash.

**No clinical credential is stored** — facility secrets are generated on the facility host
(ADR-022); this database holds identities and hashes. Tests assert no column is named for a
password or secret, and that an issued key's secret is not recoverable from the table.

Two races closed with conditional updates rather than read-then-write: claiming a host updates
`WHERE facility_id IS NULL`, and redeeming an enrolment token updates `WHERE redeemed_at IS NULL`
— so single-use survives two requests arriving *together*, not just in sequence.

**Portal built 2026-08-16 — 326 tests.** Server-rendered FastAPI + Jinja2 (ADR-020): facility
list with health/backup/version, a create-facility form, and a login. **The browser never talks to
the provisioning API** — the portal calls it server-side, and a control-verified test asserts no
rendered page contains the credential, `Authorization`, or `Bearer`.

**Auth libraries, flagged as the build prompt asked:** `argon2-cffi` for password hashing (Argon2id;
the library owns salt, parameters, constant-time verify) and Starlette `SessionMiddleware` for
sessions (signed with `itsdangerous`). Both are the places hand-rolling goes wrong, so both are
delegated.

⚠️ **No MFA** — a single password protects a control plane that can create and stop clinical
deployments. That is the portal's weakest link, recorded rather than left to be discovered. Upgrade
is an external IdP via Authlib.

Hardening: `https_only` + `SameSite=strict` + HttpOnly session; session cleared and reissued on
login so a fixated cookie is useless; **CSRF on every POST including login**; login rate-limited by
source; `no-store` on fleet pages so a stale "backup succeeded" is never shown as current; CSP with
`script-src 'none'`. The login error never reveals which half was wrong.

**End-to-end demonstration performed 2026-08-16 — four containers, fixed subnet.** Signing into
the portal and submitting the create-facility form produces a facility record, a **queued
provisioning job**, an agent that claims it over TLS and validates it locally, an execution, and a
report the API records from what it observed. Audit: 6 entries, chain verifies.

**ADR-023 demonstrated against real addresses**: an outsider holding a *genuinely valid* portal
credential is refused `403 source not allowed` on both the portal and agent endpoints, recorded as
`blocked-source` naming `172.30.0.40`, and cannot reach the agent on any port.

**Two things the full chain caught that every unit test missed:**

1. **The portal cannot work over plain HTTP** — its session cookie is `Secure`+`SameSite=strict`,
   so login silently failed. The portal was failing closed *correctly*; the harness was wrong. The
   portal now has its own certificate, which is how it must run.
2. **Creating a facility did not enqueue anything.** The API recorded a record and stopped, so the
   agent never acted. Invisible until the whole chain ran. Now wired — and `incomplete_steps` still
   reports DNS and backup, because **queued is not running**.

**`rotate-credentials` built 2026-08-16 — 346 tests — and building it found that the obvious
implementation would break every facility (ADR-024).** Rewriting the env file rotates nothing
useful and destroys plenty: database passwords were set once by `initdb`, so the stack would
present new credentials to databases holding the old ones and **lose every database**;
`OMRS_ADMIN_PASSWORD` is first-boot-only, so the file would claim a change that never happened;
and the backup passphrase decrypts existing archives, so changing it without escrow makes **every
prior backup unrecoverable** — including the one needed in the disaster that prompted the rotation.

So rotation is **scoped and honest**: it rotates the TLS material (DH-021's generator self-heals)
and Keycloak client secrets (reapplied by the reconciliation job), and **refuses the rest with what
is actually required** — `ALTER USER` in the engine, a change through OpenMRS, escrow of the old
passphrase. The response carries `not_rotated`, because listing only what it rotated would imply
the whole set was replaced. Control-verified: removing the guard fails 13 tests.

**This narrows ADR-021's blast radius slightly, in the right direction:** an attacker holding the
portal credential can force TLS and client secrets to be regenerated — disruptive, loud in the
audit log — but cannot use rotation to lock a facility out of its own databases or destroy its
ability to decrypt backups.

**Per-facility backup wiring built 2026-08-16 — 360 tests — and it found a serious flaw in what
had already been built (ADR-025).**

⚠️ **The backup passphrase would have lived only on the machine being backed up.** ADR-022 has the
agent generate credentials locally; applied to `BACKUP_ENCRYPTION_PASSPHRASE` that produces
archives encrypted with a key that dies with the host — the exact disaster backups exist for. Every
off-site archive would have been permanently undecryptable. **That is not a backup, it is an
archive nobody can open**, which is worse because it looks like one. Fixed: the operator supplies it
at host bootstrap, so it exists in two places by construction, and a test asserts the generator does
not produce it.

**Backup is scheduled only when it can succeed** — a passphrase *and* an off-site destination.
Auto-enabling it everywhere would recreate DH-022's restart loop at fleet scale; quieting it with
`BACKUP_REQUIRE_OFFSITE=false` would be worse, producing local-only archives beside the data they
protect while showing green. When it is not schedulable the facility **reports having no backup**,
and the portal shows **"⚠ not protected"** with a banner — a facility with no backup is one disk
failure from losing patient records, not a neutral "nothing yet".

Scheduled is still never shown as verified: the restore-drill date sits beside the backup state, and
a facility that has never had one says `never`. DH-014's off-site verification and DH-015's
performed restore remain the pinned gates.

**A stack actually started from a provisioning job — demonstrated 2026-08-16.** The last
"written, not demonstrated" gap. The demo agent was given a **real Docker daemon** and the **real
`ComposeExecutor`**, so clicking the portal form ends with containers running.

Observed: `docker compose` genuinely invoked and succeeding; a container running under the
project-derived name `daher-demo-birth-centre`; the agent's own env file at mode `600` with 33
variables; locally generated secrets reaching the container (`OMRS_ADMIN_PASSWORD` length 24,
`OE_KEYSTORE_PASSWORD` length 32) having never been transported; `OMRS_DEMO_CONTENT=off` and
`SSO_ENABLED=false` applied; the backup passphrase correctly **absent** (ADR-025); 6 audit entries
with a verifying chain.

⚠️ Giving the agent Docker access is **by design** — it runs on the facility host and must bring
the stack up. Docker access is root access, which is exactly why the API never has it and why the
compose file lives on the host rather than arriving in a job.

**What it does not prove, and this is the remaining gap:** the compose file is a **minimal stand-in,
not the clinical stack**. No OpenMRS, OpenELIS, Keycloak or adapter has ever booted from a
provisioning job, and the ~49-minute first boot is unexercised on this path.

**Per-verb execution timeouts 2026-08-16 — 371 tests — closing known bug 17.** One 900s bound
covered every verb, and it was shorter than a single healthcheck's grace period in the compose file
the executor exists to run (`openmrs-backend` declares `start_period: 1800s`, and `up --detach`
waits on that graph). The first genuine provisioning would have reported `failed` while the stack
came up correctly — a false negative on the one operation nobody is watching.

Verbs that wait on the health graph (`provision`, `start`, `rotate_tls`) now get 5400s, ≈1.8× the
measured 49-minute cold boot; `backup_now` gets its own bound because it scales with stored data;
`status`/`stop` stay tight so a genuine hang is still visible quickly. Per-host configurable, since
facility hardware differs. An unlisted verb raises rather than inheriting the bound chosen for `ps`
— that fall-through is how bug 17 happened in the first place.

**The guard reads the real compose file**, in the same idiom as the existing env sync test: it must
clear every `start_period` and the DH-002 measured cold boot, so adding a slower gate to the
clinical stack fails this suite instead of the first deployment. Control-verified three ways
(restore 900s → the two guards fail by name; wire `provision` to the fast bound → the per-verb tests
fail; unmount the compose file → **fails rather than skips**).

⚠️ **Written and unit-verified, not demonstrated against a real boot — and be precise about what
"clears the cold boot" means.** The measured 2940s is the *backend* reaching first HTTP 200. `up`
then still waits, serialized behind it, for six setup jobs, openelis-webapp's 180s gate, Keycloak's
120s, and image pulls on a fresh host. 5400s leaves ≈2460s for that remainder, which is
**unmeasured**. The guard asserts what a test can assert — every declared grace period and the
measured boot — not the composition. Only a real clinical stack booting through a provisioning job
settles whether 5400s is right, and that is the gap DH-027 has carried throughout. Bug 18 (the
agent not polling during a long job) was fixed 2026-08-20 — see the known-bugs table. The backup
bound remains unmeasured.

**Next:** DNS/TLS when the domain lands — the last `incomplete_step` the API reports.
⚠️ The poll loop is the first component that cannot be honestly verified on this machine — proving
"polls a separate host and refuses hostile input" needs two hosts, or two containers on separate
networks standing in for them. Validation is unit-tested hard; the transport will be marked
*written* rather than *demonstrated* until that exists.

### DH-027D · Observed infrastructure status · ✅ DONE 2026-08-20

**The problem this closes.** A facility could read `RUNNING` because an agent once reported
that `docker compose up` returned 0. That is lifecycle evidence, not health. Worse, the
`/status` endpoint **fabricated** its answer — zero service counts with a **fresh
`checked_at`**, a timestamp implying a check that had never happened.

**Two concepts, kept apart (§2).** `state` is the lifecycle phase; `health` is what the last
real check saw. A facility can sit in lifecycle `RUNNING` while its stack is `DEGRADED` right
now, and the portal shows both in separate columns. `checked_at` is `None` — never `now()` —
when no check has been reported, and the portal renders that as **"never checked"**.

**How the check is made.** `docker compose ps --all --format json` **plus**
`docker compose config --format json`, both read-only, both fixed and local, run by the
existing agent through the existing `ComposeExecutor`. The control plane never touches the
host (ADR-022 unchanged). `config` is needed as well as `ps` because `ps` can only show
containers that *exist* — a declared-but-absent service is invisible without it — and because
`config` respects active profiles, so a facility with no off-site backup is not marked
degraded for lacking a backup container it was never meant to run (§6).

**One-shots are derived, not listed.** A setup job is any service another service waits on with
`condition: service_completed_successfully` — seven of them on the clinical stack. ⚠️ A restart
policy does **not** distinguish them: every service in the real compose file declares one,
including the one-shots. That was checked rather than assumed, and a hand-maintained list would
be wrong the first time someone adds a setup job.

**Runtime evidence — the real clinical stack, read-only, nothing restarted:**

```
health   : degraded
services : 10/11 long-running
reason   : caddy unhealthy
```

with all seven one-shots correctly `exited_ok` and **not** degrading anything. ⚠️ The real `ps`
output is **28 KB** — the executor's previous 4000-character stdout cap would have truncated
the JSON into something unparseable, so the cap was raised to 64 KiB.

**Caddy / bug 20 handled honestly (§7).** Caddy's routes serve correctly while its healthcheck
cannot `exec`, so Compose reports `unhealthy`. DH-027D reports exactly that and **does not**
conclude the facility is unusable, hide it, or restart it. The live stack therefore reads
`degraded` — which is the truthful answer, and it is bug 20 that needs fixing, not the reporting.

**Controls — three, each breaking a load-bearing property:**

| Control | Result |
|---|---|
| Trust the exit code; ignore parsed states | **10 fail** |
| Let an agent assert health by omission | **1 fails** — the security-shaped one |
| Treat a one-shot `exited(0)` as a failure | **5 fail** |

Plus the existing clinical-vocabulary guard, control-verified against the *new* models: adding
`patient_count` to `InfrastructureReport` fails `test_no_clinical_data` by name (§12).

**A buggy or compromised agent cannot assert health.** A STATUS report with no infrastructure
evidence, or with evidence that fails schema validation, is persisted as **UNKNOWN with the
reason** — never HEALTHY. `UNKNOWN` is rendered "warn", not "ok", because "we have not checked"
must not look like "we checked and it is fine".

**Lifecycle is not rewritten by a check (§14).** No STATUS observation moves `FacilityState`; a
transient unhealthy reading cannot drive a lifecycle transition. If that should ever change it
needs a transition table and a decision, not an implicit side effect.

**Verified:** 27 new tests, **453 provisioning tests total** (from 426), Ruff clean, all four
suites green.

⚠️ **Boundaries.** There is **no scheduler** — a STATUS job is queued when something asks for
one, so an observation is only as fresh as the last request; the portal shows the age precisely
so that is visible rather than assumed. Freshness is *displayed*, not enforced by a staleness
threshold, because how often a fleet should be polled is an operations decision. Only the
**latest** check is persisted; history is deliberately not a metrics database. And this is
verified against real `ps`/`config` output plus unit tests — **not** against a facility
provisioned through the control plane, which remains DH-027J.

### DH-027G · Backup bootstrap / off-site protection wiring · ✅ DONE 2026-08-21

**The problem this closes.** `BackupSettings`/`render_env` (ADR-025) were fully built and
unit-tested, but `ComposeExecutor._provision()` never passed `backup=` to `render_env` — a real
provisioning run wrote an env file with no backup section no matter what an operator had
configured on the host. Separately, `BACKUP_NOW` ran `docker compose --profile backup run --rm
backup` unconditionally (would fail loudly and confusingly on an unconfigured host), and
`/facilities/{id}/status` **hard-coded** `backup_state=BackupState.NEVER_RUN` regardless of
anything an agent ever reported — a placeholder wired to nothing, the exact DH-027D-shaped defect
applied to backup instead of health.

**Where the passphrase lives, and where it explicitly does not (ADR-025, unchanged by this
ticket).** The operator places it — and the off-site destination — in the facility agent's own
process environment at bootstrap, the same manual step that already delivers `AGENT_CREDENTIAL`.
`backup_settings_from_env()` (`daher_agent/runner.py`) reads `BACKUP_ENCRYPTION_PASSPHRASE`,
`BACKUP_S3_BUCKET`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`,
`BACKUP_RETENTION_DAYS` and builds a `BackupSettings` (or `None`), passed into `ComposeExecutor`.
The control plane **never receives any of it** — not transiently, not for validation — because
ADR-025 already decided delivery happens at bootstrap, not through a job or the API; this ticket
did not re-litigate that. What the control plane learns is a three-value outcome only
(`not_configured` / `success` / `failed`) plus a short operator-language reason, via a new
`BackupReport` model structurally identical in spirit to `InfrastructureReport` — no bucket name,
no archive path, no credential, covered automatically by the existing clinical-data guard
(`response_models()` auto-discovers every model in `models.py`) and by a new parallel
secret-vocabulary guard added alongside it for exactly this ticket.

**BACKUP_NOW now answers honestly, mirroring `_status`'s convention exactly:** `succeeded=True`
means "the agent correctly determined the state," not "the facility is protected" — so an
unconfigured host reports `not_configured` with `succeeded=True` and the command is never
attempted (control-tested: see below), while a real attempt that fails reports `failed` with
`succeeded=False`. `/facilities/{id}/status` now derives `backup_state` from the latest such
report (`SqliteBackupStore`, latest-only, same pattern and same table shape as
`SqliteHealthStore`), defaulting to `NEVER_RUN` — "no evidence yet" — when none has ever arrived.
A malformed or missing report is persisted as `FAILED`, never `SUCCESS` (DH-027D's fail-closed
rule, applied here). Portal's `at_risk`/`BACKUP_CLASSES` — which pre-date this ticket — did not
know about the new `NOT_CONFIGURED` value; both were extended, found and closed in this same
change rather than left silently unreachable.

**Persistence.** `SqliteBackupStore`/`InMemoryBackupStore` (`store.py`), a new `facility_backup`
table, wired into `create_app(backup_store=...)`. Directly restart-tested (open, write, close,
reopen, read back) — worth noting because auditing this ticket found `SqliteHealthStore` itself,
DH-027D's equivalent, has **zero** test coverage; this one does not repeat that gap.

**Restore-drill stays untouched and separate, by design.** `restore_drill_last_performed` is not
set by anything in this ticket — a human-performed drill (`backup/restore-drill.md`) is out of
scope here, and a test asserts a successful `BACKUP_NOW` report leaves it `None`.

**Verified:** 44 new tests, **508 provisioning tests total** (from 464), Ruff clean, all four
suites green (637 total: adapter 94, provisioning 508, seed 16, form-safety 19).

**Control-tested, four deliberate breaks on a throwaway copy, each restored after:**

| Control | Result |
|---|---|
| Disable the `NOT_CONFIGURED` refusal in `_backup_now` (always attempt the command) | **5 fail** |
| Malformed/missing evidence reported as `SUCCESS` instead of fail-closed `FAILED` | **2 fail** |
| Revert `_provision()` to not pass `backup=` to `render_env` (the exact original defect) | **1 fails** |
| Revert the portal's `at_risk` tuple to the pre-ticket three values (drop `not_configured`) | **1 fails** |

Plus the existing secret-vocabulary guard, control-verified the same way DH-027D's clinical guard
was: a throwaway model with a literal `passphrase` field fails the check by name.

⚠️ **Boundaries, stated rather than glossed.** No API endpoint yet queues `BACKUP_NOW` on demand —
matches `STATUS`'s existing state (DH-027D never built one either); both currently require
enqueuing a job directly (demo harness or a test, as `tests/test_backup_status.py` does). More
significantly: `backup/scheduler.sh`'s nightly run is autonomous, inside the backup container,
**independent of the provisioning job queue** — the control plane has no visibility into those
runs unless a `BACKUP_NOW` job is explicitly issued, so `backup_state` reflects the last
*requested* check, not necessarily last night's real backup. Reading `last-run.status`
(`backup/README.md`) into a report would close that gap; deliberately not built here — it is a
second, separable piece of surface (a new read-only executor command, new parsing, new tests), and
DH-027G's own scope was the wiring gap the ticket named, not every path to full visibility. Not
exercised against a real S3-compatible target or a real `docker compose run --rm backup` — DH-014
already established that boundary (local path verified 2026-08-13, off-site still unexercised) and
this ticket did not re-open it; verification here is at the wiring/executor level, proving
configuration reaches the real path, not re-proving backup mechanics DH-014 already owns.

### DH-027H · Scheduled backup evidence reaches the control plane · ✅ DONE 2026-08-21

**The gap this closes — named explicitly in DH-027G's own boundary note above.** `backup/scheduler.sh`
runs entirely inside the backup container, independent of the provisioning job queue, calling
`/usr/local/bin/daher-backup` nightly at `BACKUP_HOUR_UTC` and writing
`${BACKUP_OUTPUT_DIR:-/backup}/last-run.status`. Before this ticket the control plane had no way to
see that file: `backup_state` only ever moved on an explicitly-issued `BACKUP_NOW`. A facility could
show a green manual test run from last week while the actual nightly backup had been silently
failing every night since.

**Design: extend `STATUS`, not a new verb.** `STATUS` is already the fleet's one read-only
observation seam — it runs alongside a mutating job (bug 18's status slot), and DH-027D's `config`/`ps`
pair already reads compose state this same way. `_status` now composes two independent observations,
`_observe_infrastructure` (renamed from the old `_status` body, unchanged logic) and the new
`_observe_backup`, into one `ExecutionResult` carrying both `infrastructure` and `backup`. No new
`JobVerb`, no new entry in the closed job vocabulary, no change to `validation.py`/`jobs.py`/`queue.py`.

**How the read works, and why it cannot become arbitrary file access.** `_observe_backup` checks
`self.backup` locally first — unconfigured hosts report `NOT_CONFIGURED` without ever touching Docker,
exactly like `_backup_now` already does. A configured host runs exactly one more fixed, read-only
command: `docker compose --profile backup exec -T backup cat /backup/last-run.status` — the service
name (`backup`) and the path (`BACKUP_STATUS_PATH`, a module constant) are hard-coded in
`executor.py`, never derived from a job or any caller input, the same guarantee `compose_file` already
has. This is not a new capability; it automates the exact manual check `backup/README.md` already
documented (`docker compose exec backup cat /backup/last-run.status`).

**Parsing is a pure function, strict, fail-closed** (`daher_agent/backup_observation.py`,
`parse_last_run_status`). `last-run.status` is `<STATE> <TIMESTAMP>` on exactly one line — anything
else (empty, extra fields, an unrecognised state word, an unparseable or future-beyond-tolerance
timestamp, more than one line) raises `BackupStatusError`, which `_observe_backup` converts to
`NEVER_RUN`, never `SUCCESS`. The future-tolerance window (60s) reuses `JobValidator`'s existing
clock-skew constant rather than inventing a second one. `BackupState` gained no new value: exec
failures, an absent file, and malformed content all report the same `NEVER_RUN` — deliberately, since
all three carry the identical actionable meaning to an operator ("no confirmed evidence"), and the
`reason` field carries the specific truth. **The timestamp is the backup's own run time, parsed from
the marker — never `datetime.now()`.** `backup.sh`'s `TS` is captured once at script start, so
`BackupReport.checked_at` for a scheduled observation is when the backup actually ran, not when the
agent happened to check; the portal's existing `_describe_age` now shows real backup age for exactly
this reason.

**`backup.sh` writes the marker atomically now** (`status()` in `backup/backup.sh`): the old `>
"$STATUS_FILE"` truncates before writing, so a reader landing in that window would see an empty file.
Now written to `${STATUS_FILE}.tmp` and `mv -f`'d into place — `mv` within one directory is a
`rename(2)`, atomic on POSIX. Small, in-scope fix: this file is the one artifact this ticket's
trustworthiness depends on.

**Out-of-order evidence cannot regress the fleet's displayed state.** Two independent paths report
backup evidence now — manual `BACKUP_NOW` and scheduled `STATUS` observation — with no guarantee they
arrive in the order the backups they describe actually happened. `SqliteBackupStore.record` is now
conditional in SQL (`WHERE excluded.checked_at >= facility_backup.checked_at`), the same
conditional-UPDATE pattern `SqliteHostStore.claim`/`release` already use to avoid a read-then-write
race — applied here to a document-then-write race instead. `InMemoryBackupStore` enforces the
identical guard in Python. Ties go to the incoming report (a defined, if rare, choice). Real newer
evidence — good or bad — always supersedes older evidence in either direction; only a genuinely stale
report is dropped.

**The API accepts backup evidence from `STATUS` too, but not on the same fail-closed terms as
`BACKUP_NOW`.** For `BACKUP_NOW`, producing backup evidence is the job's entire purpose, so absence is
as untrustworthy as malformed content — both record `FAILED`, unchanged from DH-027G. For `STATUS`,
backup evidence is additive: a mixed-version agent that has not been upgraded to send it yet must not
make backup look `FAILED` merely because *this particular check* did not carry it, so a `STATUS`
report with no `backup` field simply does not touch `backup_records`. Present-but-malformed is
fail-closed either way.

**Restore-drill stays untouched and separate**, proven for the scheduled path the same way DH-027G
proved it for the manual one: a test asserts a successful scheduled observation leaves
`restore_drill_last_performed` at `None`.

**Verified:** 41 new tests, **549 provisioning tests total** (from 508), Ruff clean, all four suites
green (678 total: adapter 94, provisioning 549, seed 16, form-safety 19).

**Control-tested, four deliberate breaks on a throwaway copy, each restored after:**

| Control | Result |
|---|---|
| Parser always returns `SUCCESS` regardless of the marker's actual state word | **2 fail** |
| Remove the out-of-order guard from both `SqliteBackupStore` and `InMemoryBackupStore` | **5 fail** |
| Trust the `exec` command's own exit code instead of the marker's content | **5 fail** |
| A successful backup observation sets `restore_drill_last_performed` | **2 fails** |

Plus the existing secret-vocabulary guard (`test_no_clinical_data.py`, DH-027G) — unchanged and
re-verified as still passing, since `BackupReport`'s shape did not change; a fifth deliberate break of
that specific guard was not re-run because it is verb-agnostic and model-structural, not something
this ticket's logic could plausibly disable as a side effect.

⚠️ **Boundaries, stated rather than glossed.** No API endpoint yet queues `STATUS` or `BACKUP_NOW` on
demand — unchanged from DH-027D/G; both still require enqueuing a job directly (demo harness or a
test). `NEVER_RUN` is used as the fail-closed target for three different underlying causes (absent
file, exec failure, malformed content) rather than a fourth `BackupState` value — a deliberate choice
to avoid enum proliferation, with the distinction preserved in `reason` rather than in the type. Not
exercised against a real backup container's `last-run.status` — proven against the exact documented
command and file format, with a real (if synthetic) marker text, not a live nightly run. DH-014's
off-site verification boundary is unchanged and not re-opened by this ticket.

### DH-027C · Facility lifecycle state machine · ✅ DONE 2026-08-17

**The gap:** nothing in the codebase ever set `RUNNING` or `DEGRADED`. Every facility stayed
`PROVISIONING` forever, even after its stack was confirmed up — honest, but it meant the fleet
view could never show a working facility.

**Now one explicit table**, `next_facility_state(verb, reported, current)`, driven only by an
**agent report about work it actually ran**:

| Verb | Report | From | To |
|---|---|---|---|
| `PROVISION` | succeeded | `PROVISIONING` | `RUNNING` |
| `PROVISION` | failed / refused | `PROVISIONING` | `DEGRADED` |
| `START` | succeeded | `PROVISIONING` / `DEGRADED` | `RUNNING` |
| `STOP` | succeeded | `DECOMMISSIONING` | `DECOMMISSIONED` |
| anything else | — | — | no change |

**What `RUNNING` means, stated precisely because it would otherwise be over-read:** the agent
reported its `docker compose up` returned 0. Given this compose file gates the frontend, Caddy,
the adapter and every setup job behind `service_healthy`, that is meaningfully more than
"containers created" — but it is still one claim at one moment, not continuous health. Observed
health is DH-027D, and until then `DEGRADED` is reachable only from a failed report.

`STATUS`, `BACKUP_NOW` and `ROTATE_TLS` change nothing, with a test — otherwise reading status
would rewrite the thing it reports.

**Verified:** 19 new tests, **422 provisioning tests total** (from 403), Ruff clean. The table is
tested directly as well as through HTTP, because the table *is* the specification.
Control-verified: making `PROVISION` always mean `RUNNING` — the mistake the roadmap explicitly
warns against — fails 3 tests.

⚠️ **A control that did NOT fail, recorded rather than glossed.** Removing the
"`DECOMMISSIONED` is terminal" guard changed no test result, because every row already requires
a specific `current` state that `DECOMMISSIONED` never matches. The guard is therefore redundant
*today*. It is kept, and the code now says why: it stops being redundant the moment someone adds
a row that checks `current` less narrowly, and "a successful START always means RUNNING" is an
easy and reasonable-looking row to add. The invariant is asserted by tests either way.

### DH-027E · Real soft decommission · ✅ DONE 2026-08-17

**The defect:** `POST /facilities/{id}/decommission` set `DECOMMISSIONED` on request and
**queued nothing**. The fleet therefore reported a facility as decommissioned while its stack
was still serving patients — the same "queued is not running" mistake the create path had
already been fixed for, in the direction that matters more, because an operator who believes a
stack is stopped stops watching it.

**Now a real lifecycle.** A new `DECOMMISSIONING` state is entered on request, a **STOP** job is
enqueued for the host that actually holds the claim (the claim *is* the mapping — the host is
not taken from the request), and `DECOMMISSIONED` is reached **only** when that host's agent
reports the STOP succeeded. A failed or refused report leaves the facility in
`DECOMMISSIONING`, which is the honest state: requested, not confirmed stopped.

**Two guards worth naming.** The terminal transition is gated on the job's verb being `STOP`
*and* the facility currently being in `DECOMMISSIONING`, so a successful `PROVISION` report
cannot retire the facility it just brought up. And **the host claim is deliberately not
released** — the stack's volumes, i.e. the patient database, are still on that host, so freeing
the claim would let a different facility be provisioned on top of this one's data. That is
exactly the overwrite ADR-022 refuses, and it has its own test.

No data is destroyed: the executor runs `docker compose stop`, never `down`, and a pre-existing
test already asserts no verb can emit `--volumes`.

**Verified:** 9 new tests, **403 provisioning tests total** (from 394), Ruff clean.
Control-verified twice — restoring the jump straight to `DECOMMISSIONED` fails 3 tests, and
removing the STOP enqueue fails 5.

⚠️ **One existing assertion was changed, not deleted:** `test_a_credential_with_the_scope_may_decommission`
in `test_app.py` expected `"decommissioned"`. That expectation encoded the bug. It now expects
`"decommissioning"`, with a comment saying why, and the test still checks the scope enforcement
it was actually written for.

### DH-027F · Atomic facility env writes · ✅ DONE 2026-08-17 (rotation rollback still open)

**The defect, confirmed in the code rather than taken from the roadmap.** `write_env` opened
the target with `O_TRUNC`, which empties the file *before* the new contents are written. A
process that died mid-write therefore left a **torn env file** — and on the rotation path
(`overwrite=True`) that file is a live facility's credentials. There was also no `fsync`, so a
power cut could leave a partial or empty file after a write that had already returned
successfully.

**Fixed with write-temp-then-rename:** `mkstemp` in the target's **own directory** (so
`os.replace` is atomic — it is only atomic within one filesystem), contents flushed and
`fsync`ed **before** the rename, then a best-effort directory `fsync` so the rename itself
survives a power cut. `mkstemp` creates 0600 with `O_EXCL`, so there is no world-readable
window and no predictable name to pre-create. A reader now sees either the whole old file or
the whole new one.

The directory fsync is deliberately best-effort: the contents are already durable and renamed
by that point, so a filesystem refusing to fsync a directory must not turn a completed write
into a reported failure.

**Verified:** 6 new tests, **394 provisioning tests total** (from 388), Ruff clean.
Control-verified by restoring the `O_TRUNC` writer: 4 tests fail, including the one that
matters — with `O_TRUNC` the original file is already destroyed when the failure arrives.
The fsync-ordering test asserts fsync happens *before* replace, not merely that both occur.

⚠️ **Still open from DH-027F's scope:** rotation rollback. If `rotate_tls` writes a new env
file and the subsequent `docker compose up --force-recreate` or Keycloak reconciliation fails,
the env file is already replaced and is not rolled back. The write is now atomic, so the file
is never *torn* — but it is the new one. Restoring the previous file needs a retained copy,
which is a credential-custody question (where does the old file live, and for how long?)
rather than a code detail, so it is not invented here.

### DH-027B · Compensated facility creation · ✅ DONE 2026-08-17

**The orphan.** `POST /facilities` claims the host *before* inserting the facility record.
If the insert failed, the claim survived — that host was marked occupied by a facility that
was never created, could never be deployed to again, and an operator had to unpick it by hand.

**Fixed by conditional compensation, not by reordering.** Reordering only moves the orphan:
insert-then-claim leaves a facility record holding an id that a retry then collides with.
So `HostRegistry.release(host_id, facility_id)` and `SqliteHostStore.release(...)` undo a
claim, and `create_facility` calls it when the insert fails, on **any** exception rather than
only `FacilityExists` — a storage error leaving the claim behind is the same orphan with a
less obvious cause.

**`release` is conditional on the exact facility id**, in SQL for the durable store so a
concurrent claim cannot slip between read and write. That is what stops compensation becoming
an eviction tool: a stale or hostile call naming another facility changes zero rows and
returns `False`. There is deliberately **no unconditional release** — "free up that host" is
not a safe thing to expose, because the host holds a patient database.

**The audit log cannot contradict the result.** A rolled-back create records `FAILURE` and
never `SUCCESS`, and the message distinguishes `rolled back` from `NOT rolled back —
investigate`, so a failed compensation is visible rather than assumed. Asserted directly.

**Verified:** 17 new failure-injection tests, **388 provisioning tests total** (from 371),
Ruff clean. Both backends are parametrised — in-memory *and* SQLite — because a fix that
worked only in the test double would be no fix. Control-verified: removing the compensation
line fails exactly the three HTTP-level tests, and the store-level tests still pass, which is
how the control shows the HTTP tests are the ones carrying the property.

⚠️ **Residual, stated rather than hidden:** this is compensation, not a single ACID
transaction. A process killed between the claim and the release still orphans the claim. The
shared SQLite `Database.write()` could wrap both operations for the durable path and close
that window; it is not done here because the in-memory backend cannot participate and a
half-transactional path would be harder to reason about than an explicit compensation. The
window is microseconds wide and the fix is `release`, which an operator can now reach.

### DH-027M · Continuous integration · ✅ DONE 2026-08-17

**The finding that motivated it:** the repository held **500 tests across four suites** and nothing
enforced any of them. (DH-027B added 17 the same day, so CI gates **517** today — the count in this
entry is what it was when CI landed.) Every verification claim in this file was produced by a human remembering to
run something — which is not a gate, it is a habit.

`.github/workflows/ci.yml`, five jobs, all well under a couple of minutes:

| Job | Gates |
|---|---|
| `adapter` | `pip check`, Ruff lint + format, **94 tests** |
| `provisioning` | `pip check`, Ruff lint + format, **388 tests** |
| `clinical-config` | seed safety **16 tests** (ADR-007 additive-only beds, the empty prenatal panel) and the ADR-006 form guard **19 tests** |
| `static` | `.env` untracked, tracked-secret scan, image pinning, shell syntax, JSON/XML/CSV parse, relative-link resolution |
| `compose` | default and `backup` profiles interpolate and parse, plus the demo compose |

**Every step was executed locally before being committed** — the Python jobs were simulated in the
same pinned `python:3.13.14-slim-bookworm` the images are built from, confirming both suites run
natively outside Docker (94 and 371, Ruff clean).

**Four guards control-tested by breaking them on a throwaway tree:** an unpinned `:latest` is caught;
a broken relative link is caught; a corrupt tracked JSON is caught; a suite discovering zero tests is
refused. That last one matters most — `unittest discover` **exits 0 on "Ran 0 tests"**, verified
against an empty directory, so a renamed directory would otherwise switch the clinical-safety guards
off silently. Both suites now assert a floor on tests actually executed.

**Two defects found in my own checks by running them**, which is the whole argument for controls:
the secret scan flagged a legitimate test fixture (`BACKUP_ENCRYPTION_PASSPHRASE=a-long-operator-…`
in provisioning's backup tests), and the image-pinning check flagged all five multi-stage
`FROM base AS test` lines as unpinned images when they reference an earlier *stage*. Both fixed;
stage aliases are now collected and skipped.

**Deliberately excluded, and this is a decision not an omission:** the ~50-minute clinical cold boot.
Running it per push would make CI useless within a week, and a check people learn to ignore is worse
than no check. Whole-stack verification stays the README's manual procedure plus DH-027J's fresh-VM
demonstration.

**Verified green on GitHub, and the first run failed for the right reason.** Run `32030788360`
failed on the tracked-secret scan flagging **this workflow's own** throwaway compose values — the
check working correctly on its author. Excluding `.github/` would have been the lazy fix and would
blind it to a real token pasted into CI, so the dummy values were shortened below the 12-character
threshold instead (`config` only interpolates, it never validates password content). Control-verified
in both directions: a real-looking `ghp_…` token appended to the workflow **is** still caught. Runs
`32030956269` and `32031078854` are green across all five jobs in 23–33 seconds, the latter after
bumping `checkout@v5`/`setup-python@v6` to clear GitHub's Node 20 deprecation warning.

**Honest limits.** The secret scan **excludes test trees** because fixtures legitimately contain fake credentials, so a
real secret pasted into a test would be missed there; the `.env`-untracked check is the control that
actually matters. Compose parsing uses a generated throwaway env written outside the checkout, so it
proves interpolation and syntax, **not** that the stack boots.

### P1 / P2
P1: monitoring, digest-pinning automation, `docs/RUNBOOK.md` depth. (`docs/CLINICAL-SIGNOFF.md`
now exists — created at DH-006.) P2: graphical partogram plotting recorded values only (still needs the ADR-006
conversation), RapidPro, Orthanc/PACS, multi-facility.

---

### UI-001 · Unified clinical UX + data-contract map · ✅ DONE 2026-08-21

**Why now.** DH-011 (numeric OpenELIS site code), DH-017/DH-027J (fresh VM), and DH-014's off-site
leg (approved external storage) all remain blocked on external input; none cleared as of this
ticket's audit. Rather than adding another optional provisioning feature, this ticket defines the
unified Daher clinical application precisely enough that a future frontend agent can implement it
without inventing architecture — an architecture/product deliverable, not code.

**What it is not:** no frontend code, no new backend endpoint, no clinical content. Lab panel
contents, ANC/partograph fields, and staff role assignments remain exactly as unresolved as before
this ticket — reserved to the clinical lead and Liban, per `AGENTS.md`.

**The one finding worth flagging on its own:** ADR-017 already decided the authorization model —
exactly three Keycloak realm roles (`clinician`, `lab-tech`, `sys-admin`), with midwives and
doctors deliberately folded into one `clinician` role and **no separate reception/front-desk
role**. The document's persona map (Réception, Sage-femme, Médecin, Lab tech, Admin) had to be
built as a **UX layer over that existing model**, not a new one — three of the five personas share
one auth role, and reception's restricted view is explicitly documented as a navigation
convenience with **no server-side enforcement**, not a security boundary. Inventing a fourth realm
role to make reception "real" would have been exactly the kind of clinical/access decision
`AGENTS.md` reserves for a human — flagged as a backend gap instead (§1, §24 of the document).

**Second load-bearing finding:** the adapter's own code comment
(`adapter/src/daher_adapter/openmrs.py`) states FHIR `ServiceRequest` is search/read-only on this
OpenMRS server — so a lab order placed through the Daher UI cannot be written via FHIR at all; it
must go through OpenMRS's native REST order resource, unverified against this exact pinned build.
Recorded in the data-contract matrix rather than assumed compatible with the adapter's read path.

**Deliverable:** `docs/UI-001-CLINICAL-UX.md` — role map, role-aware navigation, French
terminology glossary, the full patient-arrival-to-discharge journey with per-step system/API/
permission/failure-mode, patient/maternity/lab/result information architecture, a central
data-contract matrix (source system, read/write API, permission, audit actor, known-vs-unverified,
per UI element), the candidate Daher UI API/BFF boundary (explicitly not the adapter, and not
implemented), auth/identity propagation design (one real unresolved seam: OIDC+SAML dual-protocol
propagation through one BFF), network-loss/write-ambiguity behavior, accessibility requirements,
and nine backend gaps found and recorded rather than built around.

**Verified, not invented:** every OpenMRS/OpenELIS capability claim was checked against source
(`docs/ARCHITECTURE.md`, the adapter's own code, DH-011B's spike, ADR-017/018) or explicitly
marked `UNVERIFIED` — patient search/registration, bed-status vocabulary beyond create/list, the
order-write path, and the admission-aggregate queries needed for two dashboard cards are all
marked unverified rather than assumed. Two dashboard metrics ("En travail", "Ordonnances en
attente") are marked `UNAVAILABLE` by design rather than backed by an invented query, per
instruction.

**Tests/static checks:** documentation only — relative-link check re-run and passing (16
documents); no code changed, so the provisioning/adapter/seed/form-safety suites are unaffected
and were not re-run for this ticket.

**Documentation updated:** `docs/UI-001-CLINICAL-UX.md` (new), this entry, the ticket-status table
above. No drift found beyond what DH-027H already corrected.

**Not done, by instruction:** no frontend implementation (UI-002), no HIE, no new provisioning
ticket, no clinical configuration touched.

**Recommended next ticket:** UI-002 (design-system + first screen implementation) **only if**
still the best unblocked path when picked up — re-audit first, since any of DH-011/DH-014/DH-017's
external blockers clearing would make the clinical golden flow or real provisioning the higher
priority again, per the brief's own stated preference order.

---

## Critical path

```
BLOCKER-1 ✅ → DH-002 ✅ → DH-005 ✅ → DH-007 ✅ → DH-008 🟢 ─┐
                       │           (activation: staff names)  ├→ DH-018
DH-009 ✅ → DH-010 ✅ → DH-025 🟢 → DH-011 ─────────────────────┤
                       │            │                         │
                       │            ├─ DH-011A ✅  ADR-026     │
                       │            ├─ DH-011B ✅  spike       │
                       │            ├─ DH-011E 🟢  poller      │
                       │            │     (built, NOT active)  │
                       │            └─ 🛑 BLOCKER-6 ── target leg
                       │                  (human decision)     │
                       │                     ↓                 │
                       │            DH-011C/J/K/L → DH-012 → DH-013 ─┘
                       │
        DH-014 → DH-015  (run parallel with SSO, not after)
```

**The critical path now runs through a human decision, not through code.** BLOCKER-6 gates
DH-011's target leg and therefore DH-012, DH-013 and DH-018 — five of the eight golden-flow steps.
Everything buildable on the OpenMRS side of that boundary is done.

Longest poles: BLOCKER-6's resolution plus the target leg behind it, and DH-008's activation (staff
names). DH-011E removed the poller/watermark from this list on 2026-08-17.

## Known bugs

| # | Bug | Status |
|---|---|---|
| 1 | Gateway could not resolve `backend`/`frontend` — hard-coded upstream names in its nginx template | Fixed (network aliases) |
| 2 | Backend healthcheck reported healthy 8s into a 49-minute boot | Fixed (explicit `= 200` on the REST session endpoint) |
| 3 | `OMRS_CONFIG_ADD_DEMO_DATA` inert in this image | Documented, not removed |
| 4 | Demo-patient trigger had two writers; `off` later allowed a second 50-row batch | **Fixed — DH-024**; local base removes only upstream's trigger, mounted `on/off` is authoritative; throwaway volume retains 100 fictional rows and two deliberate patients because no patient data was deleted |
| 5 | Admin password defaults to the published `Admin123` | **Fixed and empty-volume verified — DH-019** |
| 6 | The old throwaway volume still accepted `Admin123` after DH-019 landed | **Fixed in live state 2026-08-13** — rotated through the OpenMRS API; current configured password passes and default is refused |
| 7 | Default `docker compose up -d` selected an unconfigured backup scheduler and would restart-loop | **Fixed — DH-022**; backup is an explicit profile until production settings exist |
| 8 | OpenELIS Horiba Micros60 and Pentra60 imports abort on a unique-description conflict | **Closed by scope — ADR-016.** v1 is manual result entry; analyzers deferred to v1.1. Left untouched on purpose: rollback is clean, uniqueness holds, endpoints answer. Not a defect to fix — a log behaviour to recognise |
| 9 | PostgreSQL health passed against the temporary init server, racing hardening into shutdown | **Fixed and exact empty-volume graph verified — DH-024** |
| 10 | OpenELIS ignores `DEFAULT_PW` and shipped `adminADMIN!` even on empty volumes | **Fixed and empty-volume form-login verified — DH-024** |
| 11 | Optional OpenELIS Odoo test-product mapping CSV is absent and logs an error | **Documented, not fabricated** — no Odoo service or billing integration is in v1; adding product/test mappings would be scope and laboratory configuration work |
| 12 | A stale adapter `processing` claim cannot distinguish a pre-write crash from a post-write/pre-ledger crash | **Core fixed and runtime-verified — DH-025**; concrete OpenELIS correlation attach/query remains a DH-011 activation gate. No transport exists yet, so no message has been affected |
| 13 | **The OpenELIS internal TLS certificate expired 2026-07-23** and nothing on that path validates expiry, so it kept serving | **Fixed and live-verified — DH-021.** Material is now generated on this deployment, valid to 2028-11-16, and the generator renews early (`OE_CERT_RENEW_BEFORE_DAYS`) rather than at expiry |
| 14 | **`OE_KEYSTORE_PASSWORD`/`OE_TRUSTSTORE_PASSWORD` are empty in `.env`**, so `${VAR:-kspass}` silently selected upstream's published passwords while the variables looked configured | **Fixed — DH-021.** Both are now `:?` required; an empty value fails loudly at `up` instead of quietly selecting a public password |
| 17 | **The provisioning executor's 900s timeout is far short of a real OpenMRS first boot (~49 min)**, so the first genuine facility provisioning would time out and be reported as failed while the stack was still coming up | **Fixed 2026-08-16 — per-verb timeouts.** Liban chose the longer-timeout option over detaching. Verbs that wait on the compose health graph (`provision`, `start`, `rotate_tls`) get 5400s; `backup_now` gets its own bound because it scales with stored data, not boot time; `status`/`stop` stay at 900s. Both long bounds are per-host configurable. The bound is asserted **against the real `docker-compose.yml`** — it must clear every `start_period` (the largest is 1800s) and the measured 49-minute cold boot — so a future gate with a longer grace period fails the suite rather than the first deployment. 371 tests, Ruff clean. Control-verified three ways: restoring 900s fails the two guards by name, wiring `provision` to the fast bound fails the per-verb tests, and running without the compose file mounted **fails instead of skipping**. ⚠️ Still unfixed by this: bug 18 below, and the fix has **not** been exercised against a real ~49-minute boot — no clinical stack has ever booted through the provisioning path |
| 18 | **The agent does not poll while a long job runs**, so a `stop`/`status`/`backup_now` issued during a ~50-minute provision is refused as stale when finally claimed (`JobValidator` rejects jobs older than 900s) | **Fixed 2026-08-20 — `execute` moved off the poll loop's thread (`poller.py`).** `poll_once` dispatches to a background execution slot and returns immediately; the loop's `fetch` cadence continues while a long job runs. Two slots, not one: `status` is read-only (`docker compose ps`/`config`) and always runs, including alongside an in-flight mutating job; every other verb (`provision`, `start`, `stop`, `rotate_tls`, `backup_now`) shares one **mutating** slot, so at most one runs at a time — a second one claimed while the first is in flight is refused immediately (`"busy"`) rather than racing two `docker compose` invocations against the same project. Staleness protection (ADR-022) is untouched: `JobValidator` still runs synchronously on the calling thread, before dispatch, with the same 900s window — the fix is that a `status` issued mid-provision is now validated and run promptly instead of waiting behind it. `executor.py`, `validation.py`, `jobs.py` and `queue.py` are all unchanged; the queue's per-host FIFO dispatch order is unchanged too. ⚠️ **A companion lifecycle-table fix was required to ship this safely.** Concurrent polling makes it newly possible for a duplicate `PROVISION` targeting a facility still `PROVISIONING` to be claimed and refused (busy, or the pre-existing `FacilityExistsLocally` check) *while the real attempt is genuinely still running* — under the old, blocking loop this could never happen, because nothing else could be claimed until the first job's `execute()` returned. `next_facility_state`'s `PROVISION` row previously treated any non-`succeeded` report as `DEGRADED`; a busy-refused duplicate would have flipped a healthy, still-provisioning facility to `DEGRADED` **permanently** (the row only fires from `PROVISIONING`, so the real attempt's later `succeeded` report would arrive too late to match it and correct it). Fixed in `app.py`: the row now only degrades on a genuine `failed` report; `refused` is a no-op, matching how every other row already treats a non-`succeeded` report. Only reachable via a compromised/buggy API bypassing `create_facility`'s conflict check — ADR-022's own stated residual risk — but the codebase's own precedent (`FacilityExistsLocally`) treats exactly that threat as real, so it was closed rather than logged as a caveat. 11 new tests (poller concurrency, staleness regression, lifecycle-table symmetry), **464 provisioning tests total** (from 453). **Control-tested, three breaks, each on a throwaway copy of the tree:** disabling the busy-refusal check (any verb dispatches regardless of what is already in flight) — **3 fail**; restoring the old binary `PROVISION` lifecycle row (`RUNNING if succeeded else DEGRADED`) — **2 fail**; reverting `poll_once` to call `execute` inline instead of dispatching to a thread, reproducing bug 18 exactly — **6 fail**, one unaffected (`test_the_mutating_slot_frees_once_the_job_finishes`, correctly: slot-clearing-after-completion holds regardless of the execution model, so that control has nothing to break). ⚠️ **The demo harness (`demo/agent_runner.py`) was checked, not just assumed safe.** It now dispatches non-blocking, so it can claim a second job while the first is still executing, and its `agent_outcomes.jsonl`/`agent_executed.jsonl` files can interleave writes from two threads. Checked what reads those files: nothing committed does (`grep` across `demo/harness_*.py` and `demo/README.md` finds no consumer) — they are write-only instrumentation for manual inspection today, so there is no ordering assumption this fix could violate. Worth re-checking if a harness is later built to read them. Not exercised against a real ~50-minute boot — no clinical stack has ever booted through the provisioning path, so the concurrency fix is proven at the poller/queue level, not against real `docker compose` timing |
| 16 | **Three upstream demo forms loaded here perform clinical interpretation** — `structured_soap_note` (an `alert` key, `calculateExpression`, expression validators), `mental_health_assessment_form`, `form-engine-cookbook` | **Open — raised 2026-08-13 by DH-004's new guard.** Demo content, not maternity forms; nothing changed. `OMRS_DEMO_CONTENT=off` removes them, but that is a decided switch (BLOCKER-2) — **Human Decision Required: Yes** |
| 20 | **The Caddy container reported `unhealthy` for seven days while serving perfectly.** Root cause found 2026-08-20: the image runs `caddy` as **PID 1**, and Caddy does not reap orphaned children. Every healthcheck is a `docker exec`; each exited child was reparented to PID 1 and never `wait()`ed, leaking one PID per probe — ~5,760/day at one probe per 15s. The container reached `pids.current 37,499` against a cgroup `pids.max` of **37,503**, so no new process could start at all and every probe failed with `procReady not received` | **Fixed and verified 2026-08-20 — `init: true` on the caddy service.** Diagnosis was evidence-led, not plausible: `cgroup.threads` was **17** while `pids.current` was **37,499**; every other container in the stack had `threads == pids.current`; and 37,499 matches the ~40,460 healthchecks the container had run. The adapter already used `init: true` and sat at 3 PIDs, which is why it never leaked. After recreating with `--no-deps` (DH-016's lesson — `up -d caddy` re-runs `openelis-certs`): PID 1 is `/sbin/docker-init`, `pids.current` fell 37,499 → **19** and stayed flat across 8 healthcheck cycles, health went **healthy**, and all three vhosts still answer 301/200/302. DH-027D's parser now reports the live stack **healthy 11/11** — the signal it built was correct all along. A CI compose-invariant guard now fails if `init: true` is removed, control-verified in both directions |
| 19 | **OpenELIS `PatientProvider` returns `201 Created` when the native persist fails**, with no `Location` header in either case. Demonstrated 2026-08-17 by a control pair differing only in the patient's given name: an invalid name logged `ERROR … invalid name format` and created **0** rows; a valid one created 1. Both returned 201 | **Open — upstream defect, not ours; do not "fix" OpenELIS.** The consequence is ours to defend against: an adapter trusting this 201 would write `completed` into the DH-025 ledger for an order that never existed — silent loss, the one direction `WATERMARK-DESIGN.md` refuses. **Any Option-F implementation must confirm by read-back, never by status code.** Also blocks learning the created resource id from the response, which compounds DH-011C |
| 15 | **The OpenELIS webapp healthcheck is a false green.** It asserts only that `/OpenELIS-Global/` returns `302`. A completely failed Spring context still returns 302, so the container reported **healthy** while every page was a redirect loop and the application was down | **Fixed and verified 2026-08-13.** The check now requires `LoginPage` to return 200 *and* contain the Spring Security `_csrf` token — something only a live application context produces. Proven in both directions: it passes against the healthy webapp and **fails against the bare 302** (0-byte body) that the broken context returned. `start_period` raised 60s → 180s because the context must genuinely be up, not merely bound |

## Notes for next session

- See **▶ START HERE** near the top of this file for the immediate next action.
- Read `DECISIONS.md` before touching anything. ADR-005 is accepted and load-bearing; ADR-003,
  ADR-006, ADR-007 are pinned and not to be "simplified". **ADR-026 is OPEN** — Option F is a
  recorded candidate, not a selection.
- **The adapter's OpenMRS side is built but nothing runs it** (DH-011E). If you are tempted to wire
  up the 15-second loop to "finish the job", stop: there is no delivery target until BLOCKER-6 is
  decided, and a poller that claims work it cannot deliver would mark orders `completed` that never
  reached a laboratory. The ledger is deliberately marked `completed` only after a delivery returns
  a target record.
- **Two upstream behaviours to defend against, not fix:** OpenELIS's `PatientProvider` returns
  `201 Created` when its native persist fails (bug 19 — verify by read-back, never by status code),
  and OpenMRS `fhir2` 4.2.0 accepts `_sort` and silently ignores it (`WATERMARK-DESIGN.md` §3).
- ~~**Caddy reports `unhealthy` while serving correctly**~~ — ✅ **diagnosed and fixed 2026-08-20**
  (bug 20). It was zombie-PID exhaustion from exec healthchecks with a non-reaping PID 1; `init: true`
  fixes it. The instinct to restart first would have cleared the evidence and the fault would have
  returned in a week — worth remembering next time something looks like "just needs a restart".
- **Step 1 has now genuinely run.** API verification and Liban's SPA registration are separate
  evidence and both are complete; DH-003 verified French configuration, while the human-visible
  SPA language observation remains explicitly unreported.
- `docker compose config` passing still means only that the syntax parses. Every one of the five
  findings above survived that check.
- Digests are recorded from a real pull (ARCHITECTURE §12). **Never fabricate a digest.**
- Timing: cold boot ≈49 min, warm ≈38s. Never `docker compose down -v`.
- Nothing here authorizes real patient data. Pilot-ready ≠ clinically live (ADR-012). This local
  stack now holds 100 fictional demo patients plus two deliberate test patients in OpenMRS, **and one
  synthetic patient in OpenELIS** (`id=2`, `Dhspike Zzsynthetic`, created by DH-011B and retained on
  purpose), which makes that distinction concrete rather than theoretical.
- **An open direction, not yet an ADR:** the 2026-08-17 roadmap moves the project to **cloud-primary**
  deployment. That changes where patient data physically lives, which `AGENTS.md` makes a
  stop-and-ask, so it needs an ADR before RES-001/002 or DH-027J/K proceed. `docs/ARCHITECTURE.md`
  still says data lives "on the facility's own VM" — deliberately left until the decision is made
  rather than quietly rewritten.
