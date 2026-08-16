# Project State — Daher Health

**Updated:** 2026-08-15 · **Phase:** provisioning API + portal work started (DH-026). Four policy calls decided (ADR-017/018/019); BLOCKER-3 closed, BLOCKER-5 down to names.

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
| ✅ Done | DH-001, **DH-002** (Liban confirmed 2026-08-11), **DH-003**, **DH-007**, **DH-009a**, **DH-009b**, **DH-010**, **DH-019** (empty-volume verified; old live volume rotated), **DH-020**, **DH-022**, **DH-024** |
| 🟢 Built + verified, awaiting human input | **DH-005** (Liban's own look) |

| 🔨 In progress | DH-014 (local encrypted path verified; off-site unverified), DH-017 (README brought current; needs a fresh-VM walk-through), **DH-025** (recovery core verified + committed; concrete OpenELIS binding remains with DH-011) |
| 🟢 Built + verified, activation blocked | **DH-008** (protocol/client plumbing; BLOCKER-5 — 🗓 meeting) |
| 🔨 Performed, partial coverage | **DH-015** (restore drill performed 2026-08-13; re-run needed for the lab-result and SSO legs) |
| 🔨 Built + locally verified, external input to finish | **DH-016** (routing/TLS verified locally; public certificates wait on #15 DNS) |
| 🗓 Blocked on the birth-centre meeting | **DH-004** (ADR-006 guard built; fields await sign-off), **DH-006** (mechanism built; contents await sign-off), **DH-008** activation (role matrix), **DH-011** (requester identity, cancellation contract) |
| ⏭ Deferred to v1.1 by decision | **DH-023** (analyzer interface — ADR-016) |
| ⏸ Waiting on dependencies | DH-012, DH-013, DH-018 |

## ▶ START HERE (next session)

Work through **NEEDS LIBAN** below while continuing only the unblocked tickets listed here.

1. ~~**DH-015, the performed restore drill.**~~ ✅ **PERFORMED 2026-08-13** — see the ticket below and
   `backup/restore-drill.md`. It found two real defects. It is **not closed**: re-run it once a lab
   result exists and SSO is active, and answer the §6 question about hand-entering a result.
2. **DH-016 and DH-017 are both built and locally verified.** What remains on each needs Liban:
   the real domain/DNS for public certificates, and a fresh-VM walk-through of the README.
   DH-021 and the healthcheck false green are closed.
3. **Do not activate SSO or DH-011 transport.** BLOCKER-5 and BLOCKER-3's two remaining legs are
   meeting-blocked (see the 🗓 table above). ADR-015 closed the first-sync boundary only — that is
   one leg of three, not permission to build the poller.
4. **Bind DH-025 before any DH-011 transport activation.** The recovery core and process-lifetime
   fence are verified and committed. The concrete OpenELIS writer must attach the exact correlation
   and its probe must demonstrate exactly-one/zero-match lookup against the selected target
   resource. Never steal a claim on elapsed time alone.
5. **Do not touch the Horiba imports.** ADR-016 deferred analyzers to v1.1 and explicitly did not
   authorize disabling, repairing, or log-suppressing shipped laboratory test configuration.

**If you restart the stack:** a cold boot is ~50 minutes, a warm boot ~38 seconds. Never
`docker compose down -v` — that destroys the volumes and the evidence.

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
| 7 | **Facility identity** — ✅ **PARTIALLY SET 2026-08-14:** country `Djibouti`, facility id `DJ-BC-001` (deliberate internal placeholder). ⏳ **Still needed: city**, plus state/district/postal code. These are real-world facts, not guesses | The birth centre's real details are yours | `common.properties.template` |
| 8 | **Maternity bed counts** — ✅ **PLACEHOLDERS SET 2026-08-14:** 2 delivery / 4 postpartum, clearly marked. ⚠️ Additive-only means these CANNOT be removed later; set real numbers before the pilot deployment | Facility numbers | DH-003 unblocked |
| 9 | **Prenatal panel + partograph fields** — untouched, both need the clinical lead | Hard gate for *clinically live* (ADR-012) | DH-004, DH-006 |
| ~~10~~ | ~~**DH-011 first-sync boundary**~~ — ✅ **RESOLVED 2026-08-13 by Liban (ADR-015):** no historical backfill; the watermark starts at deployment time. The pilot is forward-going patients, not a paper-record digitisation | — | BLOCKER-3 leg 1 closed |
| ~~11~~ | ⏳ **OpenELIS requester identity** — **INTERIM DECIDED 2026-08-14** (controlled test provider; real name after the meeting). Was: 🗓  — provide the facility's real requester identity; the upstream sample clinician must not silently own pilot orders | Order attribution is clinical/audit data. The current upstream value is deliberately only a placeholder | DH-006, DH-011 |
| ~~12~~ | ~~**Cancellation contract**~~ — ✅ **DECIDED 2026-08-14 (ADR-018):** revoke if the lab has not accepted/started; leave in-progress work for manual handling. Interim policy, to be **validated against real workflow** at the meeting | — | BLOCKER-3 closed |
| ~~13~~ | ~~**Horiba Micros60/Pentra60 analyzer scope**~~ — ✅ **RESOLVED 2026-08-13 by Liban (ADR-016):** v1 is manual result entry only; no analyzer/HL7/ASTM interface. DH-023 deferred to v1.1. The shipped imports are left untouched — the decision does not authorize editing lab test rows | — | BLOCKER-4 closed |
| ~~14~~ | ~~**SSO authorization matrix**~~ — ✅ **ROLE TYPES DECIDED 2026-08-14 (ADR-017).** `clinician`→`Provider`+`Organizational: Nurse` (provider=yes); `lab-tech`→`Reception`,`Results`,`Validation`; `sys-admin`→`Organizational: System Administrator`+`Global Administrator`. ⏳ **Only the NAMES still need the meeting.** Was:  — map `clinician`, `lab-tech`, and `sys-admin` to exact OpenMRS and OpenELIS native roles, and decide which staff classes are OpenMRS providers | These mappings grant access to clinical records and laboratory functions and change clinical attribution. An agent may not infer them from role labels | DH-008 / BLOCKER-5 |
| 15 | ⏳ **Pilot domain and DNS** — *in progress 2026-08-13; Liban is registering it.* Provide/control the real `BASE_DOMAIN` and point `emr`, `lab`, and `id` records at the pilot VM | Public certificate issuance cannot be demonstrated against `localhost` or invented facility DNS | DH-016 |

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

---

## Golden end-to-end flow — status

The one flow that makes Day 1 mean anything (sprint spec §8). Demonstrated directly to Liban, not
reported as passing.

| # | Step | Status |
|---|---|---|
| 1 | Register test patient in OpenMRS | ✅ Liban registered a patient in the O3 SPA himself, 2026-08-11 |
| 2 | Same SSO session into OpenELIS, no second login | ⏸ |
| 3 | Place prenatal panel lab order in OpenMRS | ⏸ |
| 4 | Order appears in OpenELIS worklist | ⏸ |
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

### DH-011 · Adapter: order path OpenMRS → OpenELIS · P0 · 🗓 BLOCKED (BLOCKER-3 legs 2–3 + DH-025 target binding)
The accepted 15-second inclusive watermark remains the implementation design.

**Decided (ADR-015):** the first watermark starts at deployment time — no historical backfill. It
must be read from the OpenMRS server clock, persisted once, and never re-derived on restart; the
inclusive lower bound still applies at the boundary. Nightly reconciliation counts only from that
bootstrap watermark.

**Still blocked** on a real OpenELIS requester identity and the cancellation state contract
(BLOCKER-3, 🗓 birth-centre meeting), plus DH-025's concrete correlation binding. DH-006's
signed-off test mappings are also required before an end-to-end pilot order can be claimed. No
transport code has been added and no order has been sent.
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

### P1 / P2
P1: monitoring, digest-pinning automation, `docs/RUNBOOK.md` depth. (`docs/CLINICAL-SIGNOFF.md`
now exists — created at DH-006.) P2: graphical partogram plotting recorded values only (still needs the ADR-006
conversation), RapidPro, Orthanc/PACS, multi-facility.

---

## Critical path

```
BLOCKER-1 (human) → DH-002 → DH-005 → DH-007 → DH-008 ──┐
                       │                                 ├→ DH-018
DH-009 ✅ → DH-010 ✅ → DH-025 → DH-011 → DH-012 → DH-013 ─┘
                       │
        DH-014 → DH-015  (run parallel with SSO, not after)
```

Longest poles: DH-008 (SSO) and DH-025/DH-011–012 (crash-safe adapter transport and observed
message shapes).

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
| 16 | **Three upstream demo forms loaded here perform clinical interpretation** — `structured_soap_note` (an `alert` key, `calculateExpression`, expression validators), `mental_health_assessment_form`, `form-engine-cookbook` | **Open — raised 2026-08-13 by DH-004's new guard.** Demo content, not maternity forms; nothing changed. `OMRS_DEMO_CONTENT=off` removes them, but that is a decided switch (BLOCKER-2) — **Human Decision Required: Yes** |
| 15 | **The OpenELIS webapp healthcheck is a false green.** It asserts only that `/OpenELIS-Global/` returns `302`. A completely failed Spring context still returns 302, so the container reported **healthy** while every page was a redirect loop and the application was down | **Fixed and verified 2026-08-13.** The check now requires `LoginPage` to return 200 *and* contain the Spring Security `_csrf` token — something only a live application context produces. Proven in both directions: it passes against the healthy webapp and **fails against the bare 302** (0-byte body) that the broken context returned. `start_period` raised 60s → 180s because the context must genuinely be up, not merely bound |

## Notes for next session

- See **▶ START HERE** near the top of this file for the immediate next action.
- Read `DECISIONS.md` before touching anything. ADR-005 is accepted and load-bearing; ADR-003,
  ADR-006, ADR-007 are pinned and not to be "simplified".
- **Step 1 has now genuinely run.** API verification and Liban's SPA registration are separate
  evidence and both are complete; DH-003 verified French configuration, while the human-visible
  SPA language observation remains explicitly unreported.
- `docker compose config` passing still means only that the syntax parses. Every one of the five
  findings above survived that check.
- Digests are recorded from a real pull (ARCHITECTURE §12). **Never fabricate a digest.**
- Timing: cold boot ≈49 min, warm ≈38s. Never `docker compose down -v`.
- Nothing here authorizes real patient data. Pilot-ready ≠ clinically live (ADR-012). This local
  stack now holds 100 fictional demo patients plus two deliberate test patients, which makes that
  distinction concrete rather than theoretical.
