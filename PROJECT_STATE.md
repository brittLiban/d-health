# Project State — Daher Health

**Updated:** 2026-08-13 · **Phase:** Steps 1 and 3 built; adapter safety skeleton, local backup path, and tenant isolation runtime-verified
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

**DH-007** (Keycloak foundation) is built and runtime-verified: the `daher` realm imports with
French as default; roles and all three clients exist; environment placeholders resolve to the
expected client secrets; no staff users are committed; and restart returns healthy without
re-importing the existing realm. **This is not SSO** — application wiring remains DH-008.

The running stack surfaced multiple findings that `docker compose config` could never have caught
(`CURRENT_STATE.md` §3a and DH-005's ticket entry). Buildable work remains alongside the decisions
gathered under **NEEDS LIBAN** below.

Step 2 (DH-003, maternity config) has not started. Its demo-patient dependency is resolved; it is
now technically ready, with real bed counts still a facility placeholder rather than something an
agent may invent.

**Quality verification 2026-08-13:** the current Compose stack passed config validation; all ten
long-running project services had zero restarts and zero OOM kills; OpenMRS REST/FHIR, OpenELIS
webapp, OpenELIS FHIR mutual TLS, the OpenELIS frontend's internal HTTP endpoint, Keycloak, and the
adapter readiness path answered. The adapter test image again passed dependency consistency, Ruff,
and all 14 tests. Shell syntax, realm JSON, OpenMRS XML, Git integrity/whitespace, tracked-secret
patterns, image-tag policy, and every relative Markdown link passed static checks. A completed
two-container `daher-demotest` password-verification stack was found and stopped without deleting
its containers or volumes. The backup scheduler remains intentionally absent from the running
stack until production encryption/off-site settings are supplied; DH-015 remains unperformed.

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

## Ticket status

| Status | Tickets |
|---|---|
| ✅ Done | DH-001, **DH-002** (Liban confirmed 2026-08-11), **DH-007**, **DH-009a**, **DH-009b**, **DH-010**, **DH-019** (empty-volume verified; old live volume rotated), **DH-020**, **DH-022** |
| 🟢 Built + verified, awaiting human input | **DH-005** (Liban's own look) |
| 🆕 Raised while building | DH-021 (public keystore private key) |
| 🔨 In progress | DH-014 (local encrypted path verified; off-site unverified), DH-017 (README) |
| 📋 Ready | DH-003 (maternity configuration) |
| 🛑 Blocked on human decisions | **DH-011** (first-sync scope, requester identity, cancellation contract), **DH-023** (Horiba analyzer scope/configuration) |
| ⏸ Waiting on dependencies | DH-004, DH-006, DH-008, DH-012, DH-013, DH-015, DH-016, DH-018 |

## ▶ START HERE (next session)

Work through **NEEDS LIBAN** below while continuing only the unblocked tickets listed here.

1. **Resolve BLOCKER-3 before activating DH-011.** The watermark algorithm is accepted, but its
   first-sync boundary and the OpenELIS requester/cancellation contract are human decisions. Do not
   implement an apparent default; see the evidence and options below.
2. **Unblocked technical work:** DH-003 and the remaining DH-014 backup layers. DH-014 is pinned P0
   and `CURRENT_STATE.md` §7 warns it is the thing that gets squeezed if left late.
3. **Resolve BLOCKER-4 before changing OpenELIS analyzer tests.** Confirm whether the facility uses
   a Horiba Pentra60; the upstream import is failing, but altering lab mappings is not an agent call.

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
| 6 | **OpenELIS default requester identity** — upstream ships a sample clinician ("Felix Dr. Mauritius") as the default order requester. Left unmodified rather than invented | Clinical content. Never invented (AGENTS.md) | DH-006 / clinically live |
| 7 | **Facility identity** — country, state, district, city, postal code, facility id are empty, exactly as upstream ships them | The birth centre's real details are yours | `openelis-config/properties/common.properties` |
| 8 | **Maternity bed counts** — committed defaults are placeholders (ADR-007) | Facility numbers | DH-003 |
| 9 | **Prenatal panel + partograph fields** — untouched, both need the clinical lead | Hard gate for *clinically live* (ADR-012) | DH-004, DH-006 |
| 10 | **DH-011 first-sync boundary** — import every historical order, only orders created after activation, or explicitly selected existing active orders plus new orders | This decides which patient orders enter the laboratory; it is clinical/scope surface, not a scheduler default | DH-011 |
| 11 | **OpenELIS requester identity for routed orders** — provide the facility's real requester identity; the upstream sample clinician must not silently own pilot orders | Order attribution is clinical/audit data. The current upstream value is deliberately only a placeholder | DH-006, DH-011 |
| 12 | **Cancellation contract once an order reaches OpenELIS** — revoke while retaining history, delete only while unaccepted, or leave it for explicit manual handling | Each choice changes laboratory workflow and audit/history. Upstream does not provide a completed reference behavior | DH-011 |
| 13 | **Horiba Pentra60 analyzer scope** — does the birth centre laboratory use or plan to use this analyzer? Its upstream CBC test import currently aborts on a unique-description conflict | Disabling it or changing its test names/mappings changes laboratory configuration. The audit cannot infer facility equipment or clinical mappings | DH-023 / DH-006 |

**ADR-005 review completed 2026-08-12.** Liban approved the custom adapter and 15-second poll
interval after the evidence and watermark design were presented. OpenELIS outbound delivery and
corrected-result behavior remain empirical gates, not assumptions.

**Two security items I fixed without asking** (disabling exposure needs no permission; restoring it
would): upstream's `mail.bcc`/`mail.from` default of a third-party gmail address is blanked, and
every published default credential found so far is now guarded so it fails loudly instead of
shipping — OpenMRS admin (DH-019), the OpenELIS DB roles, and OpenELIS's `DEFAULT_PW`.

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

**Built:** `OMRS_DEMO_PATIENTS` (default `off`) in `.env`, selecting which version of the distro's
`globalproperties-core_demo.xml` the container sees. Mounted at the **distribution source**, because
the entrypoint re-copies the live config tree on every boot — mounting the destination crash-loops
the container. Verified on an empty volume: 0 permission errors, 0 restarts, live config reads 0.
Final acceptance (patient count 0 after a full cold boot) was running at the time of writing.

**Split into two knobs deliberately.** The concept-content knob is NOT built yet, and its default is
"as shipped": turning the demo concept tree off may remove the `BasicLabTests` vocabulary DH-006's
prenatal panel maps to. Building it belongs to DH-003, and the seam is identified — a
`referenceapplication-demo` overlay directory across 37 Initializer domains.

**Below is the original entry, kept for the reasoning.** ⚠️ Its framing as a decision to be made is
superseded; only the concept-content default remains open.

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

### BLOCKER-3 — DH-011 activation and cancellation contract · 🛑 OPEN · **Human Decision Required: Yes**

**Ticket:** DH-011

**Problem:** ADR-005 fully specifies how an existing watermark advances, but not how the first
watermark is established. Starting from the oldest available order can route historical orders;
starting at activation time can omit orders that are still active. Selecting either behavior
changes which patient orders enter the laboratory.

The target contract has two additional human gates. OpenELIS requires an attributable requester,
while this deployment still carries upstream's sample clinician placeholder. For discontinuation,
ADR-005 proves the adapter will see a new `ServiceRequest.replaces`, but it does not decide the
laboratory-side state transition after the original order has been received.

**Primary evidence checked 2026-08-13:** deployed OpenELIS 3.2.1.11 bytecode and its exact source
tag (`e2a6bdf7`) show the incoming workflow is Task/ServiceRequest based and resolves requester and
test mappings. The official Ozone `eip-openelis-openmrs` source inspected at `7f994594` labels panel
support and discontinuation unsupported. Its current cancellation path deletes only a still-
`REQUESTED` Task/ServiceRequest and carries a TODO that it should revoke rather than delete. That
is evidence of an unresolved upstream contract, not permission to choose one here.

**Options requiring Liban/clinical workflow confirmation:**

1. First sync: all historical orders; activation-time-forward only; or explicitly selected existing
   active orders followed by all new events.
2. Requester: supply the real facility requester identity before routing; the upstream sample may
   be used only if Liban explicitly authorizes it for this throwaway test stack.
3. Cancellation: retain and mark revoked; delete only before acceptance and require manual handling
   afterward; or never mutate the lab order automatically and raise an operator-visible exception.

No poller or clinical order was activated. DH-011 remains blocked; DH-010's safety skeleton remains
healthy and unchanged.

### BLOCKER-4 — OpenELIS Horiba Pentra60 analyzer import · 🛑 OPEN · **Human Decision Required: Yes**

**Ticket:** DH-023

**Problem:** on every observed OpenELIS webapp boot, upstream's `horiba-pentra60-cbc.csv` import
attempts to update several test rows to the already-existing unique description
`LYM#(Whole Blood)`. PostgreSQL rejects the update on `test_desc_uk`, the transaction aborts, and
OpenELIS logs failed test-editor bridging for subsequent tests. Four occurrences were observed
across 2026-08-11 and 2026-08-13. The live table has zero duplicate descriptions and the web/FHIR
endpoints still answer, so uniqueness is preserved; the analyzer configuration is not verified.

**Decision needed:** confirm whether the facility uses or plans to use the Horiba Pentra60.

- If yes, the laboratory/clinical lead must confirm the intended test identities/mappings before
  an upstream-compatible import fix is attempted and the analyzer workflow must be demonstrated.
- If no, explicitly authorize excluding that analyzer configuration from this deployment; do not
  infer that unused-looking clinical configuration may be removed.

No test, mapping, or analyzer configuration was changed during the audit.

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
on the SPA's display language, so the French-locale question below is still open — it is a DH-003
item either way.

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

### DH-003 · OpenMRS maternity config as code · P0 · 📋 READY (DH-002 complete)
Initializer config: French locale list + default, Maternité ward, bed types (delivery /
postpartum), additive-only bed seed driven by `MATERNITY_*_BEDS` (🔒 ADR-007). Confirm whether
Initializer covers bed domains; if not, an idempotent REST seed — and record which, in
`ARCHITECTURE.md` §4.
**Acceptance:** fresh boot produces the ward and beds with no admin clicking; changing a count in
`.env` + restart adds beds; **lowering a count deletes nothing**.

### DH-004 · ANC + partograph forms · P0 · ⏸ (needs DH-003) · **Human Decision Required: Yes**
Data-entry only. 🔒 No alert lines, no action lines, no prolonged-labour flagging (ADR-006).
Fields from unmodified WHO/OpenMRS reference defaults, marked *needs sign-off from clinical lead*.
**Cannot be marked DONE without a recorded human response**, even when technically complete.

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

**Not done here:** the frontend is unexercised, and no OpenELIS login has been performed. Both need
either Caddy (DH-016) or a temporary port, and neither is required by this ticket.

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

### DH-021 · Replace the shipped keystore · P1 · 📋 READY
Raised at DH-005. `itechuw/certgen` generates nothing — it ships **pre-built** keystores inside a
public image, so the private key protecting the internal 8443 connectors is publicly downloadable,
and its passwords are the published `kspass`/`tspass`. Acceptable only while nothing outside the
Docker network speaks to 8443. Interacts with ADR-005 Q5: the adapter authenticates with a client
certificate from this same keystore, so replacing it is a coordinated change, not a drop-in.

### DH-022 · Keep unconfigured backup out of the default Compose path · P0 · ✅ DONE 2026-08-13
The documented `docker compose up -d` previously selected `backup` even when the required
encryption/off-site variables were absent, producing a credential-failure restart loop that
contradicted `CURRENT_STATE.md`. The scheduler now uses Compose's opt-in `backup` profile. Default
foundation startup remains clean; operators enable it explicitly only after approved settings
exist. This does not weaken the pinned gate: DH-014 production off-site verification and DH-015's
performed restore remain mandatory.

### DH-023 · Resolve OpenELIS Horiba analyzer import conflict · priority pending scope · 🛑 BLOCKED
See BLOCKER-4. This cannot be sized or changed until Liban confirms whether the analyzer is in
facility scope; any test/mapping change then requires laboratory/clinical confirmation.

### DH-006 · Prenatal panel order set + LOINC mappings · P0 · ⏸ (needs DH-005) · **Human Decision Required: Yes**
Reference panels as a starting point, as code. Clinical sign-off gate — draft early (week 1) so
the clinical lead has time.

### DH-007 · Keycloak + realm import · P0 · ✅ DONE 2026-08-13
Keycloak 26.7.1 is digest-pinned and running in production mode against its PostgreSQL co-tenant.
The committed `daher` realm imports automatically with French as default and English selectable;
roles `clinician`, `lab-tech`, and `sys-admin`; confidential browser clients for OpenMRS and
OpenELIS; and a non-browser adapter service account. No staff users are committed.

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

**Not DH-008:** neither OpenMRS nor OpenELIS authenticates through Keycloak yet. One-login SSO is
still unbuilt and must not be inferred from a healthy identity-provider container.

**Session lifetimes approved by Liban 2026-08-12 (ADR-013):** 1h idle / 10h maximum / 5m access
token. These are the already-verified committed values. Revisit after observed pilot workflow;
changing them remains a human workflow/security decision.

### DH-008 · SSO wiring · P0 · ⏸ (needs DH-003, DH-005, DH-007)
✅ **Re-estimate WITHDRAWN at DH-007 — the risk was mine, not real.** I briefly raised this ticket
to "two federation protocols" after seeing only `org.itech.login.saml.*` keys in OpenELIS's shipped
`common.properties`. Checking the deployed webapp instead of the config file shows OIDC is fully
supported (`spring-security-oauth2-client-6.2.8.jar`; `SecurityConfig` reads
`org.itech.login.oauth.clientID` / `.clientSecret` / `.config`, scope `openid`). **One protocol,
OIDC, for both apps — as originally planned.** The keys just have to be added to
`common.properties`, which ships without them. DH-008 returns to its original estimate.
**Requires joint understanding of: Keycloak realm config, OpenMRS auth, OpenELIS auth.** Not
split — a working realm and a working OpenMRS that don't authenticate against each other is the
exact failure this ticket exists to prevent. Includes the documented break-glass local admin.
**Acceptance:** one login, both apps, no second prompt.

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
| PostgreSQL `adapter` | ✅ DH-014 test archive captured the empty pre-DH-010 database; it now has the DH-010 schema and zero operational rows, so the next backup must capture that migration |
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

### DH-015 · Restore drill, performed and documented · P0 🔒 · ⏸ (needs DH-014)
Restore into a scratch stack; verify a known test patient, a known lab result, and a working SSO
login. **"The dump ran" is not this ticket.** A restore that only covers PostgreSQL would return a
working lab system and no patient records; that check must be explicit in the still-unwritten
`backup/restore-drill.md`.
**DoD:** drill actually performed, output recorded, dated.

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

### DH-011 · Adapter: order path OpenMRS → OpenELIS · P0 · 🛑 BLOCKED (BLOCKER-3)
The accepted 15-second inclusive watermark remains the implementation design. Activation is blocked
on the first-sync boundary, a real OpenELIS requester identity, and the cancellation state contract;
see BLOCKER-3. DH-006's signed-off test mappings are also required before an end-to-end pilot order
can be claimed. No transport code has been added and no order has been sent.
### DH-012 · Adapter: result path OpenELIS → OpenMRS · P0 · ⏸ (needs DH-011)
Record observed `ServiceRequest` / `DiagnosticReport` shapes here → triggers the ADR-004 revisit.

### DH-013 · WhatsApp notifier against mock · P0 · ⏸ (needs DH-012) · 🔒 gate
French default + English templates; content-free ("results are ready", never values). Notifier is
a module inside the adapter (ADR-002). **Status on completion of the mock work is `BLOCKED`, not
`DONE`** (ADR-003).

### DH-016 · Caddy, TLS, subdomains · P0 · ⏸ (needs DH-008)
Last, deliberately — not fighting certificates while debugging applications. Removes the
loopback port from step 1.

### DH-017 · README for a fresh Ubuntu 24.04 VM · P0 · 🔨 IN PROGRESS
Started 2026-08-10 — covers project purpose, status warning, doc map, prerequisites, `.env` setup,
and step-1 run + verification. Grows with each subsequent step.
**DoD:** someone else could deploy a second instance without asking Liban anything. Not there yet —
it currently documents step 1 only, and says so.

### DH-018 · Golden end-to-end demonstration · P0 · ⏸ (needs DH-013, DH-015)
All eight steps of §8, demonstrated live to Liban, including the retry/restart no-duplicates check.

### P1 / P2
P1: monitoring, digest-pinning automation, `docs/RUNBOOK.md` depth, `docs/CLINICAL-SIGNOFF.md`
tracking. P2: graphical partogram plotting recorded values only (still needs the ADR-006
conversation), RapidPro, Orthanc/PACS, multi-facility.

---

## Critical path

```
BLOCKER-1 (human) → DH-002 → DH-005 → DH-007 → DH-008 ──┐
                       │                                 ├→ DH-018
DH-009 ✅ → DH-010 ✅ → DH-011 → DH-012 → DH-013 ─┘
                       │
        DH-014 → DH-015  (run parallel with SSO, not after)
```

Longest poles: DH-008 (SSO) and DH-011–012 (adapter transport and observed message shapes).

## Known bugs

| # | Bug | Status |
|---|---|---|
| 1 | Gateway could not resolve `backend`/`frontend` — hard-coded upstream names in its nginx template | Fixed (network aliases) |
| 2 | Backend healthcheck reported healthy 8s into a 49-minute boot | Fixed (explicit `= 200` on the REST session endpoint) |
| 3 | `OMRS_CONFIG_ADD_DEMO_DATA` inert in this image | Documented, not removed |
| 4 | 50 fictional demo patients created on first boot | **Resolved** — `OMRS_DEMO_PATIENTS=off|on`, default `off`; old verification volume still contains them |
| 5 | Admin password defaults to the published `Admin123` | **Fixed and empty-volume verified — DH-019** |
| 6 | The old throwaway volume still accepted `Admin123` after DH-019 landed | **Fixed in live state 2026-08-13** — rotated through the OpenMRS API; current configured password passes and default is refused |
| 7 | Default `docker compose up -d` selected an unconfigured backup scheduler and would restart-loop | **Fixed — DH-022**; backup is an explicit profile until production settings exist |
| 8 | OpenELIS Horiba Pentra60 test import aborts on a unique-description conflict | **BLOCKED — DH-023 / BLOCKER-4; Human Decision Required: Yes** |

## Notes for next session

- See **▶ START HERE** near the top of this file for the immediate next action.
- Read `DECISIONS.md` before touching anything. ADR-005 is accepted and load-bearing; ADR-003,
  ADR-006, ADR-007 are pinned and not to be "simplified".
- **Step 1 has now genuinely run.** API verification and Liban's SPA registration are separate
  evidence and both are complete; the unreported French-locale observation belongs to DH-003.
- `docker compose config` passing still means only that the syntax parses. Every one of the five
  findings above survived that check.
- Digests are recorded from a real pull (ARCHITECTURE §12). **Never fabricate a digest.**
- Timing: cold boot ≈49 min, warm ≈38s. Never `docker compose down -v`.
- Nothing here authorizes real patient data. Pilot-ready ≠ clinically live (ADR-012). This local
  stack now holds 50 fictional patients, which makes that distinction concrete rather than
  theoretical.
