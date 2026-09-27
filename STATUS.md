# STATUS — Daher Health

**This is the canonical, present-tense status document for this repository.** It is short by
design and does not replace the detailed historical records it summarizes. Where this file and any
other document disagree, see §J (Documentation precedence) before assuming either is wrong.

## A. As-of

**2026-09-27, DHEALTH-CLINIC-VISIT-001, directly on `master`.** From `7810b7c`. **DHealth's Native
clinic has a clinical thread.** A clinician can register that a patient arrived and why, take them into
consultation, write a note and correct it (append-only revisions, earlier ones retained and visible),
order analyses *during that consultation*, and finish — and see that history next time. `Visites` is a
staff section; `exchange_visits`/`exchange_visit_notes` are real tables. **ADR-065.** Alembic head is
now **`0034_clinic_visits`**.

Qualified, as measured: **63** adapter tests, **11** real-PostgreSQL concurrency/constraint tests
(two clinicians taking one visit, two completing it, concurrent note revisions, cancel racing take, the
composite cross-tenant foreign key, the timestamp CHECKs, `RESTRICT`, audit/transaction coupling, one
idempotency key under a real race), **9** real-browser phases end to end, **20** UI unit tests, and
**6 negative controls** each reddening a named test and reverting byte for byte. Migration `0034` was
applied and **downgraded** against real PostgreSQL: refused while visit audit rows exist, exactly as its
docstring claims, then clean once cleared, then re-upgraded.

**The lifecycle is four states, not the five the ticket's own wording implied**, and that is a recorded
deviation rather than an omission: `CHECKED_IN` → `IN_PROGRESS` → `COMPLETED`, plus `CANCELLED` **from
`CHECKED_IN` only**. `PLANNED` and `NO_SHOW` need a calendar to be reachable and no appointment table
exists, so nothing could enter either; the database CHECK constraint refuses both. See `PROJECT_STATE.md`'s
entry and `docs/NATIVE-OPERATING-PRODUCT.md` §38 — **if five states are wanted, it is a small ticket.**

**The decision worth knowing: visit context travels on the order *intent*, not the order.** `visit_id`
is nullable on `exchange_lab_order_intents` **and** `exchange_lab_orders`. It is validated once at
intent creation (visit exists here, is `IN_PROGRESS`, is about this patient), frozen on the immutable
intent, joined to that intent's replay comparison, and copied to the order by `commit` **without
re-validation**. That placement is what makes all three of these true at once: a retry cannot move an
order to a different visit; a retry naming a *different* visit is a loud conflict rather than a silent
substitution; and a retry sent after the consultation ended still succeeds.

**Nothing completes itself and nothing waits for a laboratory.** `exchange/visits.py` contains no
reference to lab order status in either direction. A visit with an outstanding order completes normally
— asserted at both layers, and the browser phase first proves the order really is unfinished at that
moment so the assertion is not vacuous.

**Human usability is still not claimed.** No clinician has used this surface and nothing has been timed
against what it replaces, so product law 3 ("faster than what it replaces") remains a validated target.
Post-completion note amendment is deliberately **not built** — the codebase already has an explicit
versioned amendment workflow for the analogous case (ADR-048), and a second weaker one is not invented
here. Still not production-deployed, pilot-ready-on-the-Exchange-track, clinically live, or
Connect-integrated; all data synthetic, no real patient data used.

<!-- The previous as-of block follows, preserved as history rather than deleted. -->

### Previous as-of — 2026-09-27, DHEALTH-RESULT-DELIVERY-001

**2026-09-27, DHEALTH-RESULT-DELIVERY-001, directly on `master`.** From `b4093ae`. **"Delivered" is
no longer ambiguous in DHealth, and the first real Connect adapter can no longer define it by
accident.** ADR-064 assigns exactly one authoritative owner to each delivery-shaped fact and retires
`ResultDelivery` as **DEPRECATED / COMPATIBILITY-ONLY**: it owns nothing, no new code writes or reads
it, and an adapter must never touch it.

| Fact | Owner |
|---|---|
| A released clinical result exists | `LabResultReleaseVersion` |
| Available to a Native provider | **derived** — no stored state, by decision |
| A clinician reviewed one exact version | `DoctorReview` (version-scoped) |
| The provider's external system received it | `ConnectorDeliveryObligation` → `ConnectorDelivery` |
| The patient was messaged / confirmed delivered | `PatientNotification` (+ signed provider receipt) |
| A patient read or understood anything | deliberately not modelled |

**The decisive argument was structural, not redundancy.** `ResultDelivery` is unique per *order*, but
since ADR-048 a result is released as a *version* and an amendment produces another — so the fact is
version-scoped (v1 delivered while v2 is owed) and a single order-level status **cannot express it at
all**. It is the wrong shape for its own stated meaning. `ConnectorDeliveryObligation` is unique per
`release_version_id`.

**This closed a decision that was already overdue.** ADR-043 left it open on 2026-09-12 instructing
that a future ticket must decide *before any delivery mechanism is implemented*; Connect's delivery
runtime then shipped first, so that deadline had already passed unnoticed.

**No migration, no schema change, no API change, no data touched.** Alembic head stays
`0033_staff_access_lifecycle`. The inert `PENDING` row is still created, deliberately: three qualified
atomicity/idempotency tests use that insert as their proof that order creation commits as one
aggregate, one of them patching the class as its failure-injection point. Staged retirement — phase B
(stop creating) and phase C (drop the table) are named in ADR-064 and left to future tickets with their
own evidence.

**Enforced rather than merely documented:** nine structural tests
(`adapter/tests/test_dhealthresultdelivery001_ownership.py`) fail if a production module reaches the
repository, if a second writer appears, or if someone "rescues" the model by making it version-scoped.
**Three negative controls executed and reverted**, each one line, each reddening exactly one test.

**Terminology is now a product rule:** never write bare "delivered" without naming who received what.
Prefer `released`, `available to the provider`, `reviewed`, `provider-system delivery acknowledged`,
`patient notification sent`, `patient notification delivered`.

**`DH-CONNECT-ADAPTER-001` is no longer blocked by this ambiguity** and now carries an explicit rule:
its responsibility ends at the Connect delivery contract's acknowledgement.

---

**2026-09-26, DHEALTH-NATIVE-ACCESS-001, directly on `master`.** From `191dd5b`. **A facility
administrator can now actually revoke staff authority through DHealth, and the same still-valid
browser token loses that authority on its very next request.** This closes DHEALTH-NATIVE-SEC-001's
**P1** (see below): the authorization engine was always correct, but nothing in the product could
produce a withdrawn membership or a removed role, so that ticket's own suite had to reach into
PostgreSQL with `psql`.

Four organization-scoped, role-scoped, audited operations — deactivate a membership, reactivate it,
add one allowed organization role, remove one — reached by a facility's own administrator on an
**`Équipe et accès`** screen. Each is one transaction that locks the organization row, re-checks the
actor's own `ORG_ADMIN` *inside* that lock, proves the target membership belongs to the organization,
and writes its audit row alongside the change. **Administrative lockout is prevented**: an operation
may not take an organization from one-or-more active administrators to zero, a rule that no policy in
this repository previously defined. **ADR-063 (2026-09-27) decides that platform authority is subject
to it too** — platform administrators do not silently bypass the invariant on the ordinary lifecycle
operations, and emergency administrator recovery, if ever required, will be an explicit, separately
audited break-glass capability rather than an implicit exemption. That capability is **not built**.

Qualified with **16 real-browser tests** against real Keycloak 26.7.1, real Caddy, real adapter and
real PostgreSQL 16 (`scripts/demo/qualify-access.sh`), **ten real-PostgreSQL concurrency tests**
including two administrators removing each other's `ORG_ADMIN` simultaneously, and **1708 adapter
unit tests** and **920 UI tests across all 59 UI test files**. **Five negative controls executed and
reverted**, each naming what reddened — including an organization driven to **zero active
administrators** and a clinic administrator deactivating a laboratory's technician. Full record:
`PROJECT_STATE.md`'s DHEALTH-NATIVE-ACCESS-001 entry, disposition in
`docs/NATIVE-SECURITY-QUALIFICATION.md` §5.1.

**Regressions, in one invocation at `568b8f6`:** the staff access suite **16 passed**, the
DHEALTH-NATIVE-SEC-001 security suite **31 passed**, and the native golden journey **12 passed**
(including its no-console-error/no-failed-request check). Alembic head `0033_staff_access_lifecycle`,
exactly the native five containers running, `test_native_deployment_topology.py` 11 passed, and the
demo baseline back to `patients=1 memberships_inactive=0 roles=5 org_admins=2` — `roles` is 5 rather
than the 3 recorded previously because of this ticket's two `ORG_ADMIN` grants.

**What it does not claim.** Not penetration tested, not audited, not certified. No production
deployment, no public CA, no real domain, no real patient data. **No pilot gate is created or
cleared.** Withdrawing organization access is emphatically **not** disabling a Keycloak account: a
physician working at two clinics keeps their sign-in and their other clinic, which is qualified
rather than asserted.

**One gap it deliberately leaves open, and it is the largest one:** a facility still cannot create a
staff member's *first* membership. Attaching somebody new requires a platform administrator, because
DHealth has no invitation flow and no account creation at all. Out of scope by instruction, recorded
as the next admin ticket's material.

**The two `HUMAN DECISION REQUIRED` items below are unchanged and unanswered** — successful-read
audit policy, and session/timeout policy. This ticket added mutation audit only. The **one** item it
raised itself is answered: ADR-063, above.

---

**2026-09-26, DHEALTH-NATIVE-SEC-001.** From `3d058c6`. **DHealth Native's
security boundaries have been exercised against the real runtime, and the load-bearing guards were
deliberately broken to prove the qualification can see them fail.** Real Chromium, real Caddy, real
Keycloak 26.7.1, real adapter, real PostgreSQL 16: **31 security tests** covering authentication
(real PKCE, wrong audience, genuine token expiry, refresh failure, a disabled account),
authorization under change (a withdrawn membership and a removed release role, each refused on the
next request **with the same still-valid token**), tenant isolation, patient-grant scope and
revocation, the public edge, logout, response caching and log leakage. **Six negative controls executed and reverted**, each naming what reddened — including a cross-tenant read that **answered
200** and a revoked patient grant that **opened another patient's order**. The golden journey is
green afterwards (12/12), and the production Native topology still names no OpenMRS/OpenELIS
service. Runbook and full findings: `docs/NATIVE-SECURITY-QUALIFICATION.md`.

**The claim, stated at exactly its real size.** *Key Native security boundaries have been exercised
against the real runtime, including deliberate negative controls.* **Not** penetration tested, not
independently audited, not certified against any regime, not production secure.
`BASE_DOMAIN=localhost`, Caddy's internal CA, one workstation, synthetic data.

**One P1 finding, and it is a gap in the product rather than in a guard.** **[CLOSED 2026-09-26 by
DHEALTH-NATIVE-ACCESS-001 — see §A's entry above and
`docs/NATIVE-SECURITY-QUALIFICATION.md` §5.1. Kept unedited below because the finding was correct and
the record of it is worth more than a tidy page.]** **Authority can be
granted and never withdrawn.** `OrganizationMembershipRepository.set_inactive` and
`OrganizationRoleAssignmentRepository.remove` both exist and **neither has a caller** anywhere in
`api.py` or the UI. `POST .../memberships` grants access; nothing revokes it. The backend is
correct — every `require_*` re-reads membership and roles from the database on every request, so a
withdrawn membership fails closed immediately, which SEC-6 and SEC-7 prove. What they do **not**
prove is that an administrator can reach that state, and the suite has to produce it with direct
SQL for that exact reason. This is the same absence DH-MVP-ONBOARD-001 already recorded on the
onboarding side ("grants access and never removes it"); it is now a measured security finding
rather than a UI limitation.

**Two P2 findings, both pinned by tests rather than quietly carried.** Staff clinical API responses
(orders, results, release versions, review queue, patient directory) carry **no cache directive at
all** — `no-store` is set on exactly eight handlers, every one of them patient-facing. And
`app.<domain>`'s SPA catch-all answers **200 text/html** to API-shaped paths like `/health/ready`
and `/notifications/v1/*`: no data is disclosed, but a misconfigured callback or monitor looks
healthy, which is the defect class DH-NOTIFY-META-001R already found once. The `exchange.` vhost is
correctly a bare 404 on everything it does not publish.

**What was confirmed working, against the real runtime rather than by reading the code.** PKCE S256
is enforced and direct-access grants are disabled on both production clients; `JWKSKeySource` — not
the unit suite's `StaticKeySource` — validated the real browser tokens; every authentication failure
collapses to one generic 401 with no stack trace, SQL or tenant detail; anti-enumeration holds
(404 for no relationship, 403 for a member lacking the permission); a **real platform-admin token** (realm role held, zero memberships) is refused **403** on provider
orders, the lab work queue and the release queue — network administration is not clinical
authority, and that is now runtime-verified rather than read off the code; the edge publishes three
vhosts plus exactly one Meta callback path and 404s every health, docs, metrics, Connect and
notification-namespace probe with a zero-byte body; the host publishes only 80 and 443; and **no
JWT, bearer header, password, clinical value or patient name appears in the Caddy, adapter or
Keycloak logs.**

**No pilot gate is created or cleared.** This strengthens the evidence behind gates 1 and 2, which
DHEALTH-STATE-001 cleared on 2026-09-26; re-testing them is not a new gate. Gate 3 still requires a
**manual** demonstration, gate 5 still requires real Meta credentials, and DH-USABILITY-001 remains
`INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED` — security automation says nothing about a person.
**Two items go to Liban as `HUMAN DECISION REQUIRED`**: whether successful sensitive *reads* should
be audited (nothing audits them today, and no ADR decides it), and what the clinic's real session
and idle timeouts should be (the current 5-minute token / 30-minute idle / 4-hour maximum are
Keycloak-shaped defaults, not a clinical choice). §I carries both.

**2026-09-26, DHEALTH-STATE-001, directly on `master`.** From `5dcdaa9`. **A documentation
reconciliation, not a product change — no application code, no Compose file, no migration, no
workflow file was touched.** Four Native tickets landed on 2026-09-25 (golden journey, its two owed
negative controls, the canonical deployment, the destructive restore drill) and this file's §C–§I
had not caught up with them. Statements that were true when written — *real Keycloak has never been
exercised*, *no browser has completed a PKCE round trip*, *the Native frontends are jsdom-qualified
only*, *DHealth Connect has no code of its own*, *Launch 1's wired-and-deployed frontend count is
none* — had become false as **present state** while remaining accurate as **history**. They are now
superseded by dated overlays rather than rewritten, per this repository's own convention.

**Three pilot gates were re-evaluated from their exact wording and two of them cleared.** Gate 1
(real-Keycloak token validation/authorization) and gate 2 (Native browser authentication +
same-origin Exchange routing) are **CLEARED for the Native/Exchange track**, on the same evidence
and with the same caveats gate 4 already carries: `BASE_DOMAIN=localhost`, Caddy's internal CA, one
workstation, synthetic data. **Gate 3 did not clear and was not reinterpreted to make it** — it says
*manual* live/browser demonstration, and what exists is an automated Chromium run. The truthful
distinction now stated in §H is: **automated real-browser golden journey: QUALIFIED; manual live
golden-path demonstration: still owed.** Gates 5 (WhatsApp real credentials) and 6 (clinical
sign-off) are untouched and uncleared. **Three of six gates cleared is not pilot-readiness**, and no
real-patient pilot exists on either track.

**What did not change, and is worth stating because a reader could over-read the above.** No
production deployment. No off-site backup durability. No real Meta endpoint contacted. No real
vendor adapter for DHealth Connect, so no CONNECT facility can be production-routed. No human
usability qualification — DH-USABILITY-001 stays `INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED`.
The patient status *screen* has still never been rendered in a real browser (only its clinic-side
handoff and an API-level grant replay). The correction/amendment lifecycle (DH-LAB-005) and the
platform/facility-enrollment screens have never been browser-exercised either. The
maternity/reference-connector track keeps its own separate recovery status. **Three** items are
carried to Liban as **HUMAN DECISION REQUIRED** rather than decided here — two in §I (the
`DHEALTH-NATIVE-MVP-1.0` name collision, and gate 2's "DHealth Clinic" wording) and one in
`docs/MVP-1.0.md` §8 (whether its per-row cells should now be re-stamped).

**2026-09-25, DHEALTH-NATIVE-RECOVERY-001, directly on `master`.** From `7e082a7`. **DHealth Native
has been destroyed and brought back.** A populated synthetic deployment was backed up to one
encrypted archive; **every durable volume was then deleted**; a clean deployment was rebuilt from
source control and proved not to hold the source state; the archive was restored; PostgreSQL tenant
isolation was re-proved over TCP **before** anything was started; and then, in a real Chromium, the
**pre-backup staff identity signed in through real Keycloak** and the **same order, release version
and clinician review** were found through the real product — followed by a complete **new** clinical
workflow on the recovered deployment. 13 browser tests, seven negative controls executed and
reverted.

**Pilot gate 4 — a *performed* restore drill rather than a script that exists — is CLEARED for the
Native/Exchange track (§H).** It is the first of the six to clear. Two caveats travel with it and are
stated there: **off-site durability is still unexercised** (no archive has left the host, Native has
no uploader, and the production contract still *requires* off-site — so an unconfigured production
run ends `FAILED` with its local archive retained), and the **maternity/reference-connector track
keeps its own recovery status**, its 2026-08-13 drill still partially verified and still owing a
re-run. Decision: **ADR-062**. Runbook: `docs/NATIVE-RECOVERY.md`.

**What is backed up, and what is deliberately not.** Two databases and one file volume: the Exchange
database, the Keycloak database, and the adapter's file-backed audit trail. Whole databases, never a
table list — so notification, patient-access, Connect and audit state are covered by construction,
proved by a whole-database row census that matched table for table. Topology, migrations, the realm
and client definition, PostgreSQL roles and Caddy's certificates are rebuilt from this repository
instead; every password and the backup passphrase stay in the operator's own custody, and the
passphrase is named in no Compose file at all.

**One defect in the previous ticket's own work, found by needing it.**
`docker-compose.native.yml` passed the adapter **no notification environment**, so DH-NOTIFY-001's
whole feature was unreachable in the canonical Native deployment and the Meta webhook route
DHEALTH-NATIVE-DEPLOY-001 published at the edge could only ever answer 403. Exactly the
two-files-must-change-together cost ADR-061 wrote down. Now present, **off by default**. ADR-003 is
untouched: **WhatsApp stays `BLOCKED` and pilot gate 5 is not cleared** — the notification state this
drill recovered was created on the `fake` provider, which has no network code at all, and its
notification reached `SENT`, never `DELIVERED`, because only a signed provider receipt writes that.

**What this does NOT claim.** Off-site recovery. A Native backup schedule. Restore at realistic data
volume (the archive was ~118 KB). Production deployment, pilot readiness, clinical liveness, security
qualification, real-provider messaging, Connect vendor qualification. DH-USABILITY-001 remains
`INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED`. `BASE_DOMAIN=localhost`, Caddy's internal CA, one
workstation, synthetic data throughout, no real patient data, no real Meta endpoint contacted, and no
GitHub Actions file touched.


**2026-09-25, DHEALTH-NATIVE-DEPLOY-001, directly on `master`.** From `f5ad78f`. **DHealth Native
has a canonical production-shaped deployment of its own: `docker-compose.native.yml`.** Five
services — PostgreSQL, Keycloak, the Exchange adapter, the UI, Caddy — and nothing else. Clean-booted
from empty volumes, migrated to `0032_patient_notifications`, qualified with real Keycloak and a
real browser carrying the full synthetic golden journey, and restarted without losing a row.

**The repository now has two deployment tracks, deliberately distinct (ADR-061).** The historical
OpenMRS/OpenELIS maternity/reference deployment in `docker-compose.yml` is untouched and remains
reproducible; the Native product deployment describes only itself. Neither pretends to be the other,
and neither is obsolete.

**What this separation actually fixed.** The native demo had proved Native could boot without
OpenMRS/OpenELIS, but it built its topology by `extends:`-ing services out of the historical file —
and Compose merges `depends_on` from the base with no way for an override to remove an entry. Every
extended service therefore arrived carrying the maternity track's dependency graph, and the visible
symptom was a PostgreSQL service **named `openelis-db`, aliased to `db.openelis.org`, in a
deployment running no OpenELIS at all**. Inherited configuration, not product truth. The canonical
file inherits nothing: the database is `dhealth-db`, Keycloak's JDBC URL and the adapter's DB host
both name it, and `adapter/tests/test_native_deployment_topology.py` (11 tests) asserts the graph
mentions no OpenMRS/OpenELIS service, image, dependency or hostname at all.

**The demo is now an overlay on that topology**, not a second definition of it: production file plus
demo users, the demo database name and the seed authority. DH-DEMO-001's three-fact destructive-reset
boundary is intact and is now *structural* — the demo marker table moved out of the shared
`init-tenant-databases.sh` into a demo-only script, so a production deployment has no line that could
write it. Verified on a clean production boot: no marker table, **0 organizations, 0 patients,
0 orders**.

**Qualification, by level.** Compose renders (both files); clean boot from empty volumes with every
service healthy and no OpenMRS/OpenELIS/MariaDB container anywhere on the host; Alembic reaches head
on an empty database through the existing entrypoint; PostgreSQL tenant isolation proven over
**TCP with password authentication** in six cases including wrong-password denial; real Keycloak
Authorization Code + PKCE in a real Chromium; the 12-test golden journey and authorization suite
green against this topology; and a stop/start that kept the journey's order, release and review.
The public edge publishes exactly `app.`, `id.` and `exchange.` plus the one Meta callback path —
`/health/ready` answers **404** at the edge while the container is healthy behind it.

**Five negative controls executed and reverted**, each naming what reddened: a legacy `depends_on`,
an `openelis` hostname alias, `ADAPTER_DEMO_ENVIRONMENT=1` in the production file, a relaxed database
GRANT, and an authentication bypass that made an **unauthenticated request return 200**.

**Known consequence, deliberately not repaired: CI's two native-demo jobs will fail at `config`.**
`.github/workflows/ci.yml` passes `-f docker-compose.native-demo.yml` alone in four places; that file
is now an overlay and cannot render by itself. This ticket's instructions forbade touching GitHub
Actions, so it is recorded instead — the fix is one added `-f docker-compose.native.yml` per call,
exactly as `scripts/demo/*.sh` already do. Nothing reports it today because the billing refusal means
no job starts.

**One README command is written, not demonstrated.** Every boot here added an uncommitted override
remapping two digest-pinned images to locally loaded tags, because this environment's dockerd cannot
reach Docker Hub's blob CDN. The bare `docker compose -f docker-compose.native.yml --env-file
.env.native up -d --wait` has therefore never been executed verbatim. `postgres:16-alpine` in that
file also remains **not digest-pinned** — an open P1 inherited from the demo file, which must be
recorded from a machine that can pull.


**What this does NOT claim.** Not production deployed, not pilot ready, not clinically live, not
disaster-recovery qualified, not security qualified, not human-usability qualified, not Connect
integrated. `BASE_DOMAIN=localhost` means Caddy's internal CA — nothing here is evidence about
public certificate issuance. DH-USABILITY-001 remains `INTERNAL WORKFLOW REVIEW — NOT
USER-QUALIFIED`. Backup/restore is the next separate concern and was deliberately not touched.

**2026-09-25, DHEALTH-NATIVE-MVP-1.0, directly on `master`.** From `08b9bc9`. **A real browser has
carried one synthetic diagnostic order through the whole native product for the first time.** Real
Chromium, real Keycloak login, the real UI behind the real Caddy edge, the real adapter, real
PostgreSQL 16.15: clinic order → routing → laboratory acceptance → collection → processing →
structured entry → explicit release by a separate authority → the clinic seeing the released
version → a clinician review recorded against it. 11 browser tests pass, and the run asserts it
produced no console error and no failed request. Reproduction: `docs/NATIVE-GOLDEN-JOURNEY.md`.

**Five defects stood between the demo and its own golden journey, and every one of them blocked it
completely.** They are listed in full in `PROJECT_STATE.md`; the shape of them matters more than the
list: `scripts/demo/up.sh` could not interpolate its compose file (a variable DH-NOTIFY-001 added
later), its Caddy healthcheck could never pass, its Keycloak built every login form action from an
internal hostname the browser cannot resolve, its demo users lacked the profile fields Keycloak's
own VERIFY_PROFILE demands, its adapter validated tokens against the wrong issuer, and its seed
never created the facility enrollment that `find_whole_order_eligible_labs` requires — so the
routing step offered an empty list and **the journey was impossible to finish**. DH-DEMO-001 was
`WRITTEN` and had never been `DEMONSTRATED`. Now it has.

**The stack is genuinely native.** PostgreSQL, Keycloak, adapter, UI, Caddy — no OpenMRS, no
OpenELIS, no MariaDB, no maternity connector infrastructure. That separation was DH-DEMO-001's own
work and it holds: the service named `openelis-db` in the demo file is a plain `postgres:16-alpine`
keeping the name the adapter's config expects. The *main* `docker-compose.yml` remains coupled —
its `adapter` depends on the OpenELIS database image and its `caddy` on `openmrs-gateway` and
`openelis-frontend` — which is why the demo file exists and why `scripts/qualify-native-auth.sh`
still boots the whole clinical stack.

**Authorization was exercised negatively, not just positively.** Five refusals with real tokens from
real logins: unauthenticated browser, unauthenticated API, clinician reading the laboratory's queue,
technician acting as the provider, invented organization id. **Four negative controls have now been
executed and reverted** — two with the ticket, two closed the next day by DHEALTH-NATIVE-MVP-1.0R:
pointing the adapter at a different OIDC audience made the journey fail before any workflow
proceeded; deleting every `LAB_RESULT_RELEASER` assignment made the release phase fail while entry
still worked; weakening the tenant check in `require_clinician` made the clinician's cross-tenant
read answer **200** and turned `NEG-3` red; and removing the `LabOrder.status == COMPLETED` gate
from the provider review queue put an **entered-but-unreleased** result in the clinician's
"Résultats à examiner" list, turning the new `PHASE B3` red. Each mutation was in real product code,
each was reverted, and the suite is green against the restored implementation.

**What this is not.** Not usability qualification — DH-USABILITY-001 stays `INTERNAL WORKFLOW
REVIEW — NOT USER-QUALIFIED`, no F1/C1/L1 session has happened, and automated execution is not
evidence about a person. Not a patient-surface qualification: the assisted-digital path was not
exercised in a browser. Not an amendment qualification. Not a public deployment: `BASE_DOMAIN=
localhost` with Caddy's internal CA. **Pilot gate 3 (manual live/browser demonstration of the golden
path) is advanced by this and is not declared cleared** — that gate says *manual*, and this is
automated; a human has not yet driven it end to end. Gates 1, 2, 4, 5 and 6 are untouched.

**2026-09-25, DH-NOTIFY-META-001R2, directly on `master`.** From `e081fe6`. The second correction
to DH-NOTIFY-META-001, and the one its own source comment had been promising since the day it
shipped. `_MAX_CALLBACK_BODY_BYTES = 256 * 1024` was introduced so that "an unauthenticated caller
must not be able to make the server allocate arbitrary memory before the signature has even been
checked", and the route did:

```python
raw = await request.body()          # aggregates the entire body
if len(raw) > _MAX_CALLBACK_BODY_BYTES:
    raise HTTPException(413, ...)   # ... and only then consults the limit
```

The limit was real and enforced. It was also applied *after* DHealth had asked Starlette for
everything the caller chose to send, on the one unauthenticated POST surface the product has.

**Now bounded while reading.** `_read_bounded_body` consumes `request.stream()` chunk by chunk,
checks the running total **before** appending, and refuses at the bound without adding the offending
chunk and without reading on to compute a length nobody needs. Exactly 256 KiB is legal; one byte
more is a 413 that never reaches JSON parsing and never touches a notification row.

**The claim is deliberately narrow.** DHealth never asks for the whole body and never accumulates
more than the limit it advertises. Nothing here bounds what uvicorn, Caddy or the kernel may hold —
a single ASGI message can arrive larger than the entire limit, and no application code can prevent
that. The docstring, the docs and the tests all say so rather than implying the stronger property.

**Caddy applies the same 256 KiB on the same one route**, so an oversized internet request need not
reach the application at all. Defence in depth, not the control: the reader refuses whatever any
proxy did or failed to do. The two values are identical on purpose — an edge stricter than the
application would reject callbacks DHealth considers legal, and the error would show only on Meta's
delivery dashboard, never in DHealth's logs. A test fails if one is changed without the other.

**Verified through the same real proxy the previous ticket used.** Caddy 2.11.4 against this exact
Caddyfile, real `create_app()` behind it: the challenge handshake still echoes verbatim; a signed
callback is accepted; the same signature with one byte changed is refused; a signed body of exactly
262,144 bytes is **accepted** (the two bounds agree); 262,145 bytes is refused **by Caddy**, with
Caddy's own empty body; an unsigned 8 MiB body never reaches the application; `/exchange/v1/*`,
the health endpoints and the unpublished notification paths all behave exactly as before.

Four negative controls, executed and reverted. Restoring `await request.body()` reddened the two
tests that observe consumption — and **left every HTTP-level 413 test green**, which is the point:
the pre-existing oversized-callback test could never have caught this, because `TestClient` has
already assembled the body before the route runs. An off-by-one at the bound reddened the
exact-maximum tests. Authenticating normalised JSON instead of raw bytes reddened the byte-different
encoding test. Removing the edge limit left the live 413 in place but changed *who answered it* from
Caddy's empty body to the application's JSON — which is how the two boundaries are told apart.

Adapter suite re-measured; counts in `PROJECT_STATE.md`. No GitHub Actions work of any kind, and no
claim that GitHub CI passed. No Meta credential, no Meta endpoint, no message, no handset: **pilot
gate 5 remains uncleared.**

**2026-09-24, DH-NOTIFY-META-001R, directly on `master`.** From `7e9ff01`. A correction, not a
feature: the Meta webhook existed and the runbook told operators to point Meta at it, and
**`caddy/Caddyfile` did not route the path**. `exchange.<BASE_DOMAIN>` forwarded `/exchange/v1/*`
and answered 404 to everything else, so a correctly configured callback terminated at the proxy.
Nothing detected it, and nothing could have: a subscription that never verifies is
indistinguishable from a provider that has sent nothing — which is also the true state of this
deployment.

**One exact path, on the Exchange host.**
`https://exchange.<BASE_DOMAIN>/notifications/v1/meta/webhook`, GET and POST, forwarded to
`adapter:8000`. Not `/notifications/v1/*`: publishing the namespace would hand the public edge
every route the adapter later grows into it, which is the same argument that scoped this vhost in
the first place. Not `app.<BASE_DOMAIN>` either — that vhost ends in a catch-all serving the UI, so
a mistyped callback path there would answer 200 with an HTML page, a routing mistake wearing the
disguise of a working endpoint. On `exchange.` anything unpublished is a bare 404.

**Proven through a real proxy, not asserted from the file.** Caddy 2.11.4 was run against this
exact Caddyfile with the real `create_app()` behind it, over HTTPS: the `hub.challenge` handshake
echoed `dh-edge-challenge-42` verbatim as `text/plain`; a wrong verify token was refused 403; a
synthetic callback signed with a test app secret was **accepted 200 through the proxy**, and the
same signature with one byte changed in the body was refused `invalid provider signature` — which
is what proves the proxy delivered the raw bytes Meta's HMAC is computed over. `/exchange/v1/*`
still answered 401 from the adapter, `/health/live`, `/health/ready` and
`/notifications/v1/something-else` all returned Caddy's own empty 404 rather than reaching the
application.

Three negative controls, executed and reverted. Removing the route reddened three of the seven new
tests **and** made the live edge 404 the webhook — the defect reproduced on demand. Broadening it to
`/notifications/v1/*` reddened four, and the live probe showed `/notifications/v1/something-else`
answering with FastAPI's `{"detail":"Not Found"}` instead of Caddy's empty body, which is how the
wildcard's cost is visible rather than theoretical. Mutating a signed body one byte after signing
was refused at the application, through the proxy.

`adapter/tests/test_public_edge_routing.py` (7 tests) asserts the published set — exactly
`/exchange/v1/*` and the one webhook path, a 404 catch-all, no health endpoint, no namespace
wildcard — and pairs it with the application actually serving that path on both methods. It is a
**static** check and says so in its own docstring: it cannot prove Caddy parses the file or that
bytes survive a hop. Those were proven by the run above, which the suite cannot repeat — there is no
Caddy binary and no Docker daemon in CI or in this environment.

**Nothing about the provider gate changed.** No Meta credential was used, no Meta endpoint was
contacted, no message was sent, no handset received anything, and **pilot gate 5 remains
uncleared**. The HMAC boundary, the verify-token comparison, `wamid`-only resolution, the ignored
`read`, the ignored inbound messages and the content-free release wording are all untouched. The
known concern that the POST body-size limit is checked after `await request.body()` has buffered the
request **was deliberately not fixed here** and remains open.

**2026-09-24, DH-USABILITY-001, directly on `master`.** From `2395da2`. The native clinic and
laboratory workflows were reviewed screen by screen in a real browser at three widths, and the
defects that review found were fixed. **It is an `INTERNAL WORKFLOW REVIEW`, not a usability
qualification.** No clinician, no front-desk user and no laboratory technician took part in
anything; §40 of the ticket decides the label and it is not negotiable —
`component tested ≠ browser tested ≠ human tested`, and the third was not done.
Full record: [`docs/USABILITY-QUALIFICATION-001.md`](docs/USABILITY-QUALIFICATION-001.md), whose
Part B is the human-session instrument, written out and deliberately empty.

**One P0 and five P1s, all fixed.** The P0 is the one that could have changed a clinical decision:
a corrected result's version tabs said which release was *later* and never which is **current**, so
a clinician opening version 1 after an amendment saw a complete, plausible result with nothing
contradicting it. The tab now reads `Version 1 — Remplacé`, the current one reads `— Résultat
actuel`, and a superseded version leads with *"Ce résultat a été remplacé"* and a control that goes
to the one that counts. The standing is in the **text**, never a colour, and is derived from the
highest `release_version_number` rather than array order. The laboratory's own version history had
the identical gap and got the identical words. Reviewing an older version stays permitted — ADR-048
Model 3 gives every version its own obligation; what changed is which action leads.

**The other four P1s were invisible to 844 passing tests because jsdom has no layout.** At 768px
the laboratory's fixed `1fr 1fr` panel pushed `Enregistrer les valeurs` and `Terminer la saisie`
off the screen (101px of overflow; 251px at 390px). `overflow-wrap: anywhere` broke patient names
mid-word — `Fatou / ma / Robleh` — and *caused* the narrow column it was breaking to fit. The clinic
orders table dragged the whole page 235px sideways at tablet and 385px at phone, carrying the
navigation itself out of view. Three error messages shipped in unaccented French written from
inside the idempotency mechanism (`Impossible de verifier la recuperation sure…`) and never
answered §32's third question, *what should I do now*.

**Found by looking at the screens, which had never been photographed.** The existing harness covered
platform, shell and patient surfaces only; 14 clinic/laboratory scenes were added, giving 132
captures at 1280/768/390 with per-scene checks for overflow, off-screen controls, navigation
clipping and unlabelled inputs. Page-level overflow on every affected scene is now **0**.

**Two P2s are open and say why** (`§A.4` of the document): at tablet and phone the clinic order
actions are reachable only by scrolling the table sideways — the honest fix is a card layout, and
somebody should first decide whether staff phones are a target at all — and the result detail opens
below the queue with no scroll or focus move, which belongs to a deliberate focus-management pass
across all four detail panels rather than to one screen.

14 regression tests with **5 negative controls executed and reverted**, each naming exactly which
tests reddened. One of them found that the first version of the French-copy guard was worthless and
passing: it matched quoted spans with a regular expression, desynchronised on apostrophes in JSX
text, and never saw JSX text at all — it now reads the copy through the TypeScript parser with
Unicode-aware word boundaries, because `\b` is ASCII-only and reports `enregistrée` as an error.

Counts, re-measured: UI **882 tests across 58 files in 10 batches, 881 passing**, plus the one
pre-existing un-runnable test (`src/features/auth/logout.test.ts`, untouched). `tsc` clean; oxlint
48 → 49 warnings, 0 errors — the new one is the same rule, file and shape as three that already
exist in the harness. The design-token test also caught `--radius-2`, referenced by
DH-NOTIFY-001's handoff stylesheet and never defined; fixed here. No adapter change, no migration:
this ticket touched the browser only.

**§42's preferred stack could not run.** The Docker daemon is permission-denied in this
environment, so there was no Keycloak, no Caddy and no authenticated session. Nothing here is
evidence about login, session expiry, routing or latency, which leaves §31 (performance perception)
**entirely unassessed** — stub promises resolve instantly and can show neither a slow search nor a
loading flash.

**2026-09-24, DH-NOTIFY-META-001, directly on `master`.** From `f9339e9`. WhatsApp now goes
**directly to Meta's WhatsApp Cloud API** — `DHealth → Meta → WhatsApp → patient`, no intermediary
(ADR-059). **It still has not sent a message.** No real Meta endpoint has been contacted, no
credential exists, and ADR-003's gate is unchanged: WhatsApp stays `BLOCKED`, not `DONE`, and
**pilot gate 5 is not cleared**. This is a transport change, not a redesign: the domain, the durable
obligation, the idempotency key, the claim lease, the retry budget, consent, authorization and audit
are all DH-NOTIFY-001's and untouched.

**Twilio is removed entirely** rather than kept as an untested second adapter — module,
configuration, webhook route and test group. Naming `twilio` as a provider is now a startup refusal.
Git history preserves it; ADR-058 decision 10's reasoning was sound for its scope and is superseded,
not corrected.

**SMS has no selected provider, and the system says so.** The domain still understands the channel;
*availability* is now server-derived from the configured transport and returned as
`available_channels`, so the staff UI offers what the deployment can actually send on instead of a
hard-coded list. With Meta configured that is `["WHATSAPP"]`: no SMS control is rendered, and the
enrolment endpoint refuses the channel. Choosing an SMS vendor was explicitly out of scope.

**The provider boundary was tightened rather than bypassed.** `NotificationTransport.send` now takes
the notification *event* instead of a rendered body — a WhatsApp Business message is an approved
template plus parameters, not free text, so the intent crosses the boundary and each transport
decides how it becomes a wire payload. The server owns the event → template mapping and `send` has
**no `template_name` parameter at all**, asserted on both the Protocol and the implementation rather
than trusted.

**A′ is untouched and now enforced in the payload as well as the template.** The issuance message
carries the only link; the release/amendment binding takes **zero parameters**, so its payload has
no `components` key at all — there is no array a value could be threaded through. A URL supplied for
a release event is refused outright rather than dropped. `read` is ignored (a chat thread being open
is not acknowledgement), inbound messages are ignored (§29), and the webhook resolves by `wamid`
alone: WABA id, phone-number id, recipient and any order/notification id in a payload are never
authority.

**A limitation that shaped the design, recorded rather than buried.** `developers.facebook.com` was
unreachable from the implementing environment, so **Meta's primary documentation was never read**.
The wire shapes are corroborated across independent secondary sources and are **not** verified
against Meta's own current text. The response is structural: `META_GRAPH_API_VERSION` is required
with no default and shape-validated, template names are configuration, and the Graph `error.code`
table is a short refinement over an HTTP-status classification that carries the load. Confirming the
shapes against the live API is the first qualification task.

**The real-provider qualification path now exists and has never been run.**
`.github/workflows/qualify-meta-whatsapp.yml` plus `adapter/scripts/qualify_meta_whatsapp.py`:
`workflow_dispatch` only, credentials from repository secrets, refuses to run without an explicit
non-patient test destination, refuses a non-E.164 one, refuses a database holding non-synthetic
organizations, and writes an evidence artifact of wamids and states carrying no token, body or
destination. Its delivery-receipt leg is **manual** and says so — a CI runner has no public URL for
Meta to call back.

Seven negative controls executed and reverted. **One found a real test weakness**: NC-META-2's first
run reddened only one of the two out-of-order tests, because `test_w10`'s callback sequence ended on
a favourable status and could not see an intermediate regression. It now asserts after *each* late
callback across five statuses; re-running the control reddens both, as it always should have.

Counts, re-measured: adapter **1608 passed, 160 skipped** (from 1565/160), of which 140 are the
notification files and 15 the real-PostgreSQL qualification. UI **844 passed** across three
batches (324 + 264 + 256), plus the one pre-existing un-runnable test
(`src/features/auth/logout.test.ts`, untouched by this ticket). No migration:
this changed a transport, not a schema.


**2026-09-24, DH-NOTIFY-001, directly on `master`.** From `f861008`. DHealth can now compose and
hand a patient message to a transport. **It still has not sent one.** No real provider has been
contacted, no credential exists, and ADR-003's pinned gate is unchanged: WhatsApp/SMS stays
`BLOCKED`, not `DONE`, and **pilot gate 5 is not cleared**. What exists is the domain, the content
policy, the durability and the transport boundary — all qualified against a deterministic in-process
fake and real PostgreSQL 18.6, which is a claim about code, not about delivery.

The ticket's own §4 and §16/§30 could not both hold, and the conflict was escalated rather than
guessed: the raw `PatientAccessGrant` secret is returned exactly once and is unrecoverable
(`docs/ARCHITECTURE.md` §19), so a message composed at *release* time cannot carry a working link
without either persisting the secret or re-issuing the grant — the first forbidden by §4, the second
fatal to the printed QR already in a patient's hands. Liban chose **A′** (ADR-058): the link is sent
at issuance, while the secret is still in the request's memory; release and amendment messages carry
**no link at all** and point at the one the patient already holds, or at their facility.
`PatientAccessGrant` is unchanged in every particular, and `DISPATCHABLE_EVENT_TYPES` excludes
`ACCESS_LINK_ISSUED` structurally so no worker can claim the one message needing a URL it cannot
rebuild.

**The message is not the result.** `notify/templates.py` holds every string that can ever be sent,
and the release/amendment templates contain **no substitution slot**, so no value, unit, reference
range, interpretation, follow-up disposition, test name or identifier can reach a handset. Release
and amendment wording is identical on purpose — "your result has been corrected" is itself a
clinical fact about a named person, read on a lock screen. **`ResultDelivery` is untouched**: it
means the ordering *provider organization* received the result, its semantics remain an open product
decision (`docs/MVP-1.0.md` §10 item 6), and nothing in this ticket reads or writes it — asserted by
a before/after snapshot diff across the whole release → dispatch → receipt path, which `NC-NOTIFY-3`
turns red.

Obligations commit **inside the release transaction** and the vendor call happens outside it, so a
messaging outage can never roll back a clinically valid release. Idempotency is a UNIQUE index keyed
on the immutable `LabResultReleaseVersion`; keyed on the order, an amendment would look like a
replay of the release it corrects and be silently dropped. `SENT` means a vendor's API accepted the
request and nothing about a handset; `DELIVERED` is written in one place only, after an HMAC check
against the **configured** callback URL, never one derived from a request `Host` header.

**Real PostgreSQL 18.6 found a real bug that SQLite structurally could not**: `enqueue` called
`session.add()` before opening its savepoint, so entering `begin_nested()` flushed the INSERT into
the *outer* transaction — a unique-key collision would have aborted the caller's transaction, which
is a clinical result release. Fixed, and now pinned by a deterministic SQLite regression test as
well as the concurrency one. Eight negative controls executed and reverted, each red exactly where
predicted; two stayed green where a reader might expect red and both are recorded with the reason.

Counts, re-measured at the end rather than carried forward: **1565 passed, 160 skipped** in the
adapter suite (baseline measured before any change: 1467/145), of which 97 are new SQLite tests and
15 are the real-PostgreSQL qualification file. UI: **841 passed**, plus one pre-existing test that
cannot be run in this environment at all and is untouched by this ticket
(`src/features/auth/logout.test.ts` — see `PROJECT_STATE.md`). **Not done:** no real provider, no
credential, no handset, no browser demonstration of the send flow, no facility using it, and **no
real-provider qualification path exists yet either** — when credentials arrive the first step is
writing one, not running it.


**2026-09-23, DH-CONNECT-AGENT-001 (lifetime correction), directly on `master`.** From `45130f3`.
The provider half of the runtime shipped with two defects of the same kind — it confused how long a
fact is true with how long the thing that reported it exists. A correlation was keyed on the
connector *installation*, so a connector upgrade erased the partner's identifier namespace; and a
delivery that could not be made produced a log line rather than a state. Both are corrected:
correlation is scoped to **organization + source system** with the installation kept as provenance,
and every release that owes a provider a notification now leaves a durable
`ConnectorDeliveryObligation` behind — queued, or recorded as owed and why. Suspension is a pause
rather than an erasure, retirement without a successor leaves the work owed rather than dropped, and
a successor's first heartbeat discharges what its predecessor left. Migration
`0031_connect_namespace`, qualified on real PostgreSQL including a refused ambiguous backfill and a
downgrade round trip. **No vendor adapter was started, and nothing about routing changed.** See
ADR-057 decisions 32–41.

**2026-09-23, DH-CONNECT-AGENT-001, directly on `master`.** From `ddac950`. DHealth Connect has a
**runtime**: a durable delivery engine on the server (`connector_deliveries`, claim leases,
exponential retry, dead letters), a service-identity boundary, an on-premises agent with its own
SQLite inbox/outbox/receipts, and health **derived** from what an agent reports rather than declared
by anyone. The consequence that matters is the one DH-CONNECT-CONTRACT-001's correction was waiting
for: `ORDER_RECEIPT` is now proved by *delivering an order through the real path* — queued centrally,
claimed over HTTP under a lease, handed to an adapter, acknowledged back — and checked in three
independent places before it counts. **The synthetic laboratory suite therefore reaches
`connector_qualified = true` for the first time in this codebase**, and the suite version moved 2 → 3
so every older PASS reports `SUITE_VERSION_OUTDATED` rather than being re-credited with a claim it
never made. §8's rule is structural: the installation is derived from the credential
(`token → azp → service_client_id → installation`) and **no runtime endpoint has an
`organization_id` or `connector_installation_id` parameter at all**; the envelope's organization is
compared and a mismatch is refused, audited and not written. Human and agent tokens differ by OIDC
audience, so neither works where the other belongs, and DHealth stores the client *identifier* —
never a secret, which it never receives. Health has no setter anywhere: no column, no endpoint, no
UI control, and `DEGRADED` blocks routing exactly as `UNAVAILABLE` does. Seven negative controls
executed and reverted; **one stayed green and found a real gap** — the agent's local replay check was
protected only by the synthetic adapter's own idempotency, so the test now asserts the call count and
a non-idempotent partner adapter would be safe too. Real PostgreSQL 18.6 covered the one property
SQLite structurally cannot: two concurrent `claim`s under `FOR UPDATE SKIP LOCKED` never take the
same message. **No vendor adapter exists, no real EMR or LIS has been qualified, and no facility is
live through Connect** — a CONNECT facility still cannot reach `READY_FOR_ROUTING`, and the refusal
now names the real reason instead of the one that became false.

**2026-09-23, same ticket, provider side.** The runtime is symmetric by construction and had been
exercised in one direction only, leaving `ORDER_SUBMISSION`, `RELEASED_RESULT_RECEIPT` and
`AMENDMENT_RECEIPT` permanently `DECLARED` — the same hole `ORDER_RECEIPT` was in, for the same
reason. Contract **1.2** adds `RESULT_NOTIFICATION` and `AMENDMENT_NOTIFICATION` (additive; major
unchanged, so nothing requalifies), migration `0030_connect_provider` admits them, and a release hook
queues the notification **inside the release transaction** — so a released result and the ordering
EMR not being told cannot disagree. A provider connector now reaches `connector_qualified = true`
through a journey nothing shortcuts: the synthetic EMR emits an order, the agent submits it, a native
laboratory does the work and releases, the hook fires, the agent delivers, and an amendment follows
the same path as a *correction* rather than a second result.

Writing the end-to-end test found three defects the nine unit tests had passed over, one of them a
design error: DHealth was doing the code translation, which §34 makes impossible because it stores
the mapping's fingerprint and never the mapping. Translation is the connector's job in both
directions, as the laboratory side already had it. The other two: a patient reference opening its own
transaction inside the applier's, and no correlation being bound for a submission at all — an order
whose results could never be filed back into the system that asked for them.
`QUALIFICATION_SUITE_VERSION` deliberately did **not** move: no existing scenario changed and no
provider connector has ever recorded a PASS, so there was nothing stale to invalidate.

**Nothing about routing changed.** No vendor adapter, no real EMR or LIS qualified, no facility live
through Connect; a qualified CONNECT facility of either kind still cannot reach `READY_FOR_ROUTING`,
pinned from both sides by `RQ8` and `PQ12`. Next: DH-CONNECT-ADAPTER-001.

**2026-09-22, DH-CONNECT-CONTRACT-001 correction, directly on `master`.** From `a7e4d5e`. A hole in
the ticket above: `ORDER_RECEIPT` was recorded as a passing required capability with "declaration
only" written in prose, and the verdict counted every `passed=True` row. Nothing was routable, so it
looked harmless — and DH-CONNECT-AGENT-001 is precisely the ticket that supplies the missing
runtime, on whose arrival a connector that had merely *claimed* it could receive orders would have
become production-routable without DHealth ever watching it receive one. **A limitation the verdict
does not consult is decoration.** Every capability row now carries `evidence_level` — `DECLARED` or
`EXERCISED`, non-null, CHECK-constrained (`0028_connect_evidence`) — and a required capability
counts only when it is both passed and exercised. Two new gaps keep the reasons honest:
`REQUIRED_CAPABILITY_UNEXERCISED` (the connector claims it, nothing has driven it — DHealth's own
missing transport, not the partner's bug) and `SUITE_VERSION_OUTDATED` (the suite version moved from
1 to 2 and is now part of applicability, so old evidence fails closed rather than being re-credited
with a claim it never made). **The honest result is that the synthetic laboratory suite no longer
qualifies anything**: it passes every scenario it can execute, the run stays `PASSED` forever, and
production qualification is *incomplete* — those are different claims and the screen now states them
on two separate lines. Provider connectors likewise cannot qualify at all in this build, there being
no provider harness. Three negative controls executed and reverted, each red exactly where the
ticket predicted. Real PostgreSQL 18.6 found a bug SQLite structurally cannot: the first revision id
was 35 characters against a `VARCHAR(32)` `alembic_version`, so the whole SQLite suite passed a
migration that could never be stamped on production. **`connector_operational` is unchanged and
still unconditionally false** — this correction changed what qualification *means*, not what DHealth
can observe, and no transport, runtime, heartbeat or delivery worker was added.


**2026-09-22, DH-CONNECT-CONTRACT-001, directly on `master`. The ticket is CLOSED.** Starting point
was `master` at `80af8d4`. **DHealth Connect's transport is still not built** — no FHIR client, no
HL7 parser, no MLLP, no SFTP runtime, no webhook receiver, no agent, no delivery worker, and nothing
in this repository has ever contacted an external EMR or LIS. What exists now is the **contract**
any future connector must obey and the **durable evidence model** that records whether one did:
a versioned canonical message set (a technical acknowledgement is a *different message type* from a
business acceptance, not a flag on it), four-part scoped external identities with no demographic
field a fuzzy match could use, retryable-versus-not failure categories, fail-closed mapping, and a
qualification harness that drives real Exchange mutations rather than asserting them. Evidence lives
in three append-only tables (`0027_connect_contract`, backfilling nothing); **there is no `qualified`
column anywhere**, no endpoint that records a qualification, and no UI control that marks one — the
absences are pinned by tests. A CONNECT facility now carries two readiness findings where it carried
one: `connector_qualified`, genuinely derived, and `connector_operational`, **unconditionally false
and blocking**, so a fully qualified connector still leaves `production_routing_allowed` false. That
pair is the whole point — qualification proves a build *could* work; only a runtime reporting in
proves one *is* working, and no runtime exists until DH-CONNECT-AGENT-001. One design decision was
**reversed by the code**: `COLLECTION_EVENT` is required, not optional, for a laboratory connector,
because the Exchange refuses `ACCEPTED → IN_PROGRESS` without a recorded collection and refuses
result entry on anything else — so a partner LIS with no reliable collection signal cannot have a
qualified laboratory connector, which is a real constraint on who is integrable. Five negative
controls were executed and reverted; one (a technical acknowledgement falling through to acceptance)
left the suite green and exposed a scenario that was *asserting* rather than *exercising* — now
fixed, and the control turns it red. Provider-side qualification and `ORDER_RECEIPT` are
**declared-only** in this build, stated as limitations in the docs and carried into the stored
evidence rather than left in a comment.


**2026-09-22, DH-MVP-ONBOARD-001 closing change, directly on `master`. The ticket is CLOSED.** The
last onboarding hole: `first_administrator` is optional at enrollment — correctly, since a partner
is often signed up before the intended administrator has ever logged in — so a facility could exist
that **nobody could administer, with no screen to fix it**. Attaching somebody afterwards meant the
API and that person's identity UUID. Every facility detail now carries an **Équipe et accès**
section listing who belongs to it, what each may do, and whether that access is live, with
*Ajouter une personne* → search → select → roles → membership. **Frontend only**: it calls the
`GET`/`POST /organizations/{id}/memberships` pair that already existed, which a platform
administrator reaches through `require_membership`'s `via_platform_admin` bypass — no new endpoint,
no new model, no migration. The search/select/roles panel was **extracted** from the enrollment
wizard rather than copied, so both routes to a membership stay one implementation. A successful
attach re-reads readiness, so the `administrator_present` blocker clears because the server says so.
**Nothing on the screen removes access**: `POST .../memberships` is ensure/add and this API revokes
nothing, so held roles render ticked and disabled, inactive memberships are listed but not editable,
and there is no control whose label implies a removal the server would refuse. ~~Revocation,
invitations and Keycloak provisioning remain out of scope and unbuilt.~~ **[Corrected 2026-09-26,
DHEALTH-NATIVE-ACCESS-001: revocation is built, on the *facility-owned* `Équipe et accès` staff
screen, not this platform-scope section — which still does not remove anything, now as a scope
decision rather than a missing capability. Invitations and Keycloak provisioning remain out of scope
and unbuilt.]**

**2026-09-21, DH-MVP-ONBOARD-001 correction, directly on `master`.** Two ticket-owned gaps closed,
from `1621ca6`. **The wizard's first-administrator step now performs the action it offered**: the
selected person's membership and role assignments are created in the *same transaction* as the
organization and its enrollment, so there is no state where a facility exists and nobody can
administer it. An illegal role for the facility type, or a person who has never signed in, creates
nothing at all. Role legality stays in `OrganizationRoleAssignmentRepository` — not restated in the
endpoint and not in React. **And a Native laboratory may now only be sent work it can finish**:
DHealth's human result entry runs against a controlled active template, so an active offering
without one is refused *per service* — not eligible, and direct assignment refused by test name —
while the lab's other services route normally. Zero template coverage blocks the facility outright,
because a lab that can complete nothing it advertises is not a destination. CONNECT is untouched by
that rule, by construction rather than by convention.

**2026-09-21, DH-MVP-ONBOARD-001, directly on `master`** (this ticket's own explicit instruction: no
branch, no PR). Starting point was `master` at `85f2cba` (DH-MVP-POLISH-001's final commit). This
ticket builds the **facility enrollment control plane**: a platform operator can enrol a clinic or
laboratory, attach its first administrator, record whether it will use DHealth directly or keep its
existing software, capture what that software is, and read a server-derived answer to "can clinics
send orders here, and if not exactly why". **Migration `0026_facility_enrollment`** adds three tables
and backfills every existing organization as `NATIVE`/`ORGANIZATION_READY` — a statement of fact,
since Connect does not exist and every organization operating today *is* Native.

The distinction the ticket exists to protect: **enrolled ≠ configured ≠ operationally ready ≠
production-routable**. Production routing to a Native facility now requires real operational
authority — a provider needs an active clinician, a laboratory needs both an active technician and
an active release authority, because entry and release are separate authorities (ADR-044) and a lab
without a releaser can accept work it can never finish. One person may hold both lab roles.
Losing that staffing blocks new routing **without** rewriting the configured `ProviderLabRoute`;
restoring it makes the same route usable again. A **CONNECT facility cannot reach production
readiness at all**, however complete its discovery, because no connector exists — enforced in the
repository, not at one endpoint.

**Evidence level: local qualification only, because GitHub Actions executes nothing.** Every job of
every run ends in 2–3 seconds with zero steps and no logs; the previously-green run `35527353831` on
SHA `5983da9` was re-run as a control and **failed identically**. GitHub finally named the cause
itself, in an annotation on run `35621071589`: *"The job was not started because recent account
payments have failed or your spending limit needs to be increased."* An account-billing condition,
not a workflow or repository defect, and nothing in this repository can clear it. **CI conclusion
for this ticket is unavailable: repository code was never evaluated, so it is neither failed nor
green.**

Measured locally: adapter **1168 passed, 118 skipped** (from 1122), ruff check and format clean
across 156 files; **811 UI tests across all 54 test files**, run in seven batches because a
whole-suite run loses a nondeterministic subset of files to worker-start failures; `tsc` and
`tsc -p e2e/tsconfig.json` clean; oxlint 0 errors / 45 warnings; build clean with no secrets across
28 files; migration re-qualified against **real PostgreSQL 18** (upgrade, backfill with zero orphans,
CHECK constraints refusing bad values, duplicate enrollment refused, downgrade and re-upgrade,
`current == heads == 0026_facility_enrollment`); and seven platform scenes captured in **headless
Chromium at 1280, 768 and 390** with no console error, failed request, overflow, off-screen control
or unlabelled input. **Five negative controls executed and reverted** (activation gate, assignment
gate, eligibility filter, refusal-telemetry failure, and the frontend's offer guard).
**Eleven defects found and fixed**, including one class worth naming: tests of mine that passed for
the wrong reason because a fixture used a different token issuer than the API resolved.

Production routing is also **switched on from the interface**, on a care facility's detail screen: a
two-gesture confirmation that restates what activation permits, no activation control at all for a
Connect laboratory or an unready one, deactivation never gated, and a server refusal rendered reason
by reason with no success reported. A laboratory that loses its release authority reads *Relation
configurée · Routage indisponible* — both halves, because the relationship survives and the routing
does not.

**Not proven:** no DHealth Connect transport exists and none was built; no real browser has been used
by a person; no E2E ran; no CI conclusion; and the first-administrator bootstrap still requires the
person to sign in once (no invitation system, by scope). *Corrected 2026-09-21: the wizard now
creates that membership and its roles atomically — see the correction entry above.*

**2026-09-20, DH-MVP-POLISH-001, directly on `master`** (this ticket's own explicit instruction: no
branch, no PR). Starting point was `master` at `50a7eac` (DH-PATIENT-001's own final commit), verified
against `origin/master` by a fresh `git fetch` rather than trusted from the prompt. This ticket made
the native surfaces **one authenticated staff application** (`ui/src/features/staff/`): the
Clinic/Lab application switch is gone, `/me` is loaded once for the whole application instead of once
per product, and which workflows a person sees derives from their active organization and roles in it.
It also introduced the central design-token layer, self-hosted typefaces (no font CDN at runtime), a
workflow relay derived from real order state, a two-gesture review confirmation, an honest
unsaved-value indicator in result entry, and the patient surface restyled to the same design language
at its own density. **No backend route, no schema change, no migration** — Alembic head stays
`0025_native_result_binding`. The adapter was not edited at all; its suites were run as a regression
check and match the previous baseline exactly (**1122 passed, 118 skipped**). Evidence level: **unit
tests, static analysis, and the CI-equivalent checks runnable locally** — the remote CI run this
ticket's single push triggers is reported in the session handoff, not claimed here, since these words
were written before that push existed. **[Updated after the push: CI run `35526256367` on SHA
`5057b6d` completed with all ten jobs green, `conclusion=success`, queried per job rather than read
from a watch command's exit code. A second push follows it carrying one review fix — the masthead
role labels — so that run's own result is not yet known at the time of writing either.]** **794
frontend tests across all 52 test files**, measured by
reconciling a whole-suite run (which reported only 24 files and left 27 workers unstarted, a known
flake of this 9p-mounted checkout) against the 27 remaining files run by name in three batches —
24 + 27 = 51 on disk, 421 + 366 = 787, plus 7 tests in one new file added after review. **13 negative controls actually executed** (each claimed
property deliberately broken, the specific test observed going red for the specific reason, then
reverted); one of them did not fire on first attempt and was rewritten until it did. **Fourteen bugs were
found and fixed during this ticket** — including a pre-existing one (21 CSS custom properties
referenced and defined nowhere, so "muted" labels had been rendering at full strength), a
working-looking `Se reconnecter` button that could not have reconnected anything, an axe suite that
had been auditing the wrong surface, and this ticket's own review tests passing green over a submit
path that threw before it ran. See `PROJECT_STATE.md`'s DH-MVP-POLISH-001 entry for all fourteen. The twelfth came from review, not from me: the masthead was rendering the authorization enum (`LAB_TECH · LAB_RESULT_RELEASER`) in the one band that is always on screen.
**Not proven:** no real browser rendered any of this, at any width, on any device; no font was
observed loading; no focus ring or contrast ratio was measured; no E2E ran (no live stack, and
`typecheck:e2e` — which does pass locally — is not part of CI); and the organization `<select>` on
which this ticket's whole premise rests has never been used by a person who belongs to two
facilities. jsdom cannot measure layout or resolve a cascade of custom properties, which is exactly
why the token guards are source scans.

**2026-09-20, DH-PATIENT-001, directly on `master`** (this ticket's own explicit instruction: no
branch, no PR). Starting point was `master` at `a5992bd` (DH-DEMO-001's own final commit), verified
against `origin/master` by a fresh `git fetch` rather than trusted from the prompt. This ticket
added the **first DHealth Patient frontend** (`ui/src/features/patient/`) plus a clinician-side
patient handoff (QR + printable sheet + revoke/rotate), a provider-authorized patient-access
metadata read, and a server-owned patient follow-up projection. **No migration** — Alembic head
stays `0025_native_result_binding`, confirmed against CI's own `EXPECTED_HEAD`. Evidence level:
**unit tests plus the CI-equivalent checks runnable locally** — the remote CI run this ticket's
single push triggers is reported in the session handoff, not claimed here, since these words were
written before that push existed. 18 new adapter tests (1104 → **1122 passed**, 118 skipped) and 92
new frontend tests (**684 passed across all 46 files**, measured in eight small batches — a single
whole-suite `vitest` run is not a reliable count on this machine, and the `PROJECT_STATE.md` entry
explains why, along with the pre-existing CI weakness that measurement exposed), plus **18 negative
controls actually executed** (each claimed property
deliberately broken, the specific test observed going red for the specific reason, then reverted).
**Three real bugs were found and fixed during this ticket** — see §C. **Not proven:** no real
browser rendered this patient screen, no real phone scanned one of these QR codes, no printer
produced one of these sheets, and no real-PostgreSQL qualification was needed or run (this ticket
adds no new concurrency surface — the one new backend read is a single-row, lock-free,
non-mutating GET). Responsive behaviour was written mobile-first and reasoned about at 320/375/430px
but **not measured in a real browser** — jsdom cannot measure layout.

**2026-09-18, DH-DEMO-001, directly on `master`** (this ticket's own explicit instruction: no
branch, no PR). Starting point was `master` at `0916afd` (DH-LAB-005's own final commit). All
facts below were re-verified directly against the current repository and fresh local test runs on
this exact commit. **DH-CLINIC-004B's own claims (§C/§I) are unchanged by this ticket** — this
ticket's `git diff` touched no authentication/Keycloak/Caddy production code at all; the only
pre-existing files modified are `.github/workflows/ci.yml` (additive) and one UI test file, every
other file is new. **Real-PostgreSQL qualification of this ticket's own reset-safety boundary and
baseline/reset idempotency was actually run this session** (2 tests, 4 executions, zero flakes,
including a genuine positive control and a genuine permission bug found and fixed) against the
same native PostgreSQL 18 instance prior tickets located inside the sandbox. **This sandbox's
Docker still cannot pull real image blobs from the CDN** (documented since DH-LAB-002), so this
ticket's own central claim — a native-only Compose stack reaching healthy without ever waiting on
OpenMRS/OpenELIS — has **not** been observed booting anywhere yet; the CI job this ticket's single
push triggers is its first real attempt, not a confirmation of something already proven locally.
This ticket adds no new migration — Alembic head stays `0025_native_result_binding`.

**2026-09-18, DH-LAB-005, directly on `master`.** Starting point was `master` at `eefc1f2`
(DH-LAB-004's own final documentation commit). Real-PostgreSQL qualification of this ticket's own
four new concurrency races (concurrent Native corrections of the same observation, Native
correction vs. original release, amendment Native correction vs. amendment release, two releasers
racing the same amendment) was run against the identical native PostgreSQL 18 instance DH-LAB-004
first located inside the sandbox, migrated to head via real `alembic upgrade head`, all 4 tests run
clean 4x in a row. See the DH-LAB-005 entry in `PROJECT_STATE.md` for exact commands and evidence.
This ticket added no new migration — Alembic head stayed `0025_native_result_binding`, confirmed
against CI's own `EXPECTED_HEAD`.

## B. Product direction

DHealth is an interoperable diagnostic network: providers, laboratories, and patients, either on
native DHealth apps or through an existing EMR/LIS connector. The current product contract is
`DECISIONS.md`'s **ADR-043** and **`docs/MVP-1.0.md`**. The pre-pivot OpenMRS/OpenELIS
maternity/birth-centre system is preserved as the **first reference connector track**, real and
still binding wherever it is touched, not the definition of DHealth's overall scope. Four product
laws govern the Exchange track: no forced marketplace, no double entry, faster than what it
replaces, no digital-literacy requirement for care (`docs/MVP-1.0.md` §6). Nothing in this ticket
changes, weakens, or reinterprets any of the four.

## C. PROVEN / QUALIFIED

*Qualified at the specific evidence level stated per item below — CI/unit/integration and, where
noted, real-PostgreSQL. This is not a claim of production deployment, and where a ticket's own
formal closure is a separate reserved human decision, that is stated explicitly in its bullet.*

**[Dated banner, 2026-09-26, DHEALTH-STATE-001 — read this before any per-bullet qualifier below.]**
Many bullets in this section and in §I end with *"Not real-Keycloak-qualified, not
runtime/browser-demonstrated"* or *"same evidence level as the rest of DHealth Clinic"*. Those
sentences were accurate when written and are **now false for the parts of the product the native
golden journey actually walked**. They are left in place as each ticket's own record rather than
edited one by one; this banner is what overrides them. Precisely what a real Chromium, signed in
through real Keycloak, has driven on the canonical Native topology (`docker-compose.native.yml`,
`BASE_DOMAIN=localhost`, synthetic data — DHEALTH-NATIVE-MVP-1.0/1.0R, DHEALTH-NATIVE-DEPLOY-001,
DHEALTH-NATIVE-RECOVERY-001):

| Surface | Browser-exercised? |
|---|---|
| The one staff application shell — navigation derived from membership, establishment name, sign-out (ADR-054) | **yes** |
| Patient directory list/open and the `PatientIdentity` projection (DH-IDENTITY-001 read path, DH-CLINIC-005) | **yes** |
| Native patient *registration* form (DH-IDENTITY-001 write path) | no |
| Order wizard: catalog selection, review, durable commit (DH-CLINIC-003C) | **yes** |
| Routing step and order-scoped eligible-labs read (DH-CLINIC-006 / ADR-052) | **yes** |
| Lab queue, accept, collection milestone, start-processing (DH-LAB-001/002) | **yes** |
| Template-driven native result entry and gated completion (DH-LAB-003/004) | **yes** |
| Entry-vs-release authority separation, incl. a releaser-only navigation (ADR-044) | **yes** |
| Least-privilege release queue and fingerprint-gated release (DH-LAB-004) | **yes** |
| Provider review queue, exact-version tab, review disposition (DH-CLINIC-007, DH-EX-014A) | **yes** |
| Clinic-side patient-access handoff: issue, notification enrolment, active-grant confirmation (DH-PATIENT-001 clinic half, DH-NOTIFY-001 on the `fake` provider) | **yes** |
| Patient status *screen* itself (`ui/src/features/patient/`) | **no** — the restored grant was replayed at the API only |
| Correction and post-release amendment lifecycle (DH-LAB-005) | no |
| Platform / facility-enrollment screens (DH-MVP-ONBOARD-001) | no |
| Facility-owned `Équipe et accès`: an ORG_ADMIN-only account reaching a workspace at all, and a real click withdrawing a colleague's access (DHEALTH-NATIVE-ACCESS-001) | **yes** |

**What the table does not say.** It is runtime qualification on synthetic data with one operator —
not usability qualification, not production deployment, not a public-certificate claim, and not
evidence about any surface marked *no*. Detail: `docs/NATIVE-GOLDEN-JOURNEY.md`,
`docs/NATIVE-RECOVERY.md`.

- **Organization staff access lifecycle, DHEALTH-NATIVE-ACCESS-001** (2026-09-26): the supported
  product path for withdrawing staff authority, which is what closes DHEALTH-NATIVE-SEC-001's P1.
  Four operations (`exchange/staff_access.py`) — deactivate a membership, reactivate it, add one
  allowed organization role, remove one — each **one transaction** that locks the organization row,
  **re-checks the actor's own `ORG_ADMIN` inside that lock**, proves the target membership belongs to
  the organization, then mutates and audits together. The organization row rather than the membership
  row is the lock because two administrators removing *each other's* `ORG_ADMIN` touch two different
  rows, and a per-row lock lets both pass and the organization reach **zero** administrators. The
  **last-active-administrator invariant is a transition rule, not a floor** (an organization that
  already has none is not frozen, since that state is reachable through the existing grant endpoint),
  it binds a platform administrator too, and no policy for it existed anywhere in the repository
  before this ticket. Reactivation restores the roles still attached — the pre-existing model,
  observed rather than redesigned, and stated in both the API response and the UI confirmation.
  Nothing touches Keycloak: withdrawing organization access leaves the person's sign-in and their
  other establishments alone, qualified rather than asserted. `PLATFORM_ADMIN` cannot be granted
  because it is not an `OrganizationRole`; the `{role}` path parameter is a plain `str` validated in
  DHealth's own code precisely so that check is reachable by a negative control rather than enforced
  invisibly by request parsing, with the database's CHECK constraints as the second layer. UI:
  `Équipe et accès` (`ui/src/features/team/`), the first facility-owned administration surface, which
  required making an `ORG_ADMIN`-only membership *usable* — its previous exclusion was the same P1 in
  user-facing form. Migration `0033_staff_access_lifecycle` widens one CHECK constraint and creates no
  table (`membership_deactivated`, `membership_reactivated`, `role_removed`). Evidence: **1708
  adapter unit tests**, **920 UI tests across all 59 test files** (measured in reconciled batches:
  one whole-suite invocation silently ran only 41 of the 59 and would have produced a false total),
  **ten real-PostgreSQL
  concurrency tests** (`test_dhealthaccess001_postgres_qualification.py`, covering §25-A/B/C/D and
  the lockout races — `FOR UPDATE` is a no-op on SQLite, so this claim is PostgreSQL-only by
  necessity), and **16 real-Keycloak/real-Chromium tests** (`scripts/demo/qualify-access.sh`). A
  second defect was found and fixed en route: `reset.sh` could not recover a deactivated membership,
  which made this document's own stated recovery path false — reproduced, fixed, pinned. **Not**
  penetration tested, **not** audited, no pilot gate created or cleared, and a facility still cannot
  create a staff member's *first* membership (§F).

- **Patient notification domain, transport and dispatcher, DH-NOTIFY-001** (2026-09-24,
  ADR-058): a durable patient-notification domain separate from `ResultDelivery` in every respect
  (`patient_notifications`, `patient_notification_enrollments`, `patient_notification_attempts`,
  migration `0032_patient_notifications`); a content policy enforced structurally rather than
  editorially — the release/amendment templates have **no substitution slot**, so no value, unit,
  reference range, interpretation, disposition or identifier can reach a handset; obligations that
  commit inside the release transaction while the vendor call happens outside it; idempotency as a
  UNIQUE index keyed on the immutable `LabResultReleaseVersion`; a vendor-independent transport
  with one implementation (**Meta WhatsApp Cloud API, direct** — ADR-059 replaced DH-NOTIFY-001's
  Twilio adapter and removed it); a dispatcher using `FOR UPDATE SKIP LOCKED` with claim leases,
  bounded retry and one transaction per message; and a delivery-receipt webhook whose authority is
  an HMAC signature over the **configured** callback URL. Evidence: 97 SQLite tests
  (`test_patient_notification_domain/api/transport.py`) and 15 real-PostgreSQL 18.6 tests
  (`tests/integration/test_dhnotify001_postgres_qualification.py`), including two-thread claim
  disjointness, concurrent-hook convergence on one row, lease expiry, the PostgreSQL-only E.164
  CHECK and the audit-vocabulary constraints read back out of `pg_constraint`. Eight negative
  controls executed and reverted. Real PostgreSQL found a genuine bug SQLite structurally could not
  — `enqueue` flushed its INSERT outside the savepoint, so a unique-key collision would have
  aborted the caller's clinical release transaction — now fixed and pinned by a deterministic
  SQLite regression test as well. **This is a claim about code. No real provider was contacted and
  no message was sent** (§E).

- **DHealth Connect provider-delivery durability, DH-CONNECT-AGENT-001** (2026-09-23,
  correction): correlation scoped to the organization and source system rather than to the connector
  installation, so an external identifier survives a connector replacement and never crosses a
  source-system boundary; and `connector_delivery_obligations`, which makes *"this release owes a
  provider a notification"* a durable, queryable state instead of a log line. Evidence: CL1–CL13,
  SD/SD8/SD9, RT1–RT6, BF1–BF6, PQ13–PQ16, and AC1–AC3 driving the full replacement-and-suspension
  journey through the real agent; four negative controls run and reverted; migration
  `0031_connect_namespace` qualified on real PostgreSQL 18.6 with a seeded backfill, a deliberately
  refused ambiguous upgrade, and an upgrade → downgrade → upgrade round trip (PG24–PG27).

- **DHealth Connect runtime, DH-CONNECT-AGENT-001** (2026-09-23): a durable delivery engine
  (`connector_deliveries`, claim leases, exponential retry, dead letters that are unresolved rather
  than deleted), a service-identity boundary on its own OIDC audience where the installation is
  derived from the credential and **no runtime endpoint accepts an organization or installation
  parameter at all**, an on-premises agent (`daher_adapter.connect_agent`, stdlib SQLite, no partner
  PostgreSQL required) whose persist/act/acknowledge ordering closes every crash window, exact
  correlation with no fuzzy matching, and **health derived** from observations with no column,
  endpoint or control that could set it. `ORDER_RECEIPT` is exercised by delivering a real order
  through the real path and checking three independent places, so **the synthetic laboratory suite
  reaches `connector_qualified = true` for the first time in this codebase** and the suite version
  moved 2 → 3. Seven negative controls executed and reverted; one stayed green and exposed a real
  gap (the agent's replay check was protected only by the synthetic adapter's own idempotency — the
  test now asserts the call count). Real PostgreSQL 18.6 covered `FOR UPDATE SKIP LOCKED` under two
  concurrent claims, which SQLite structurally cannot test. Migration `0029_connect_runtime`, with
  a new `tests/test_migration_hygiene.py` measuring every revision id against `VARCHAR(32)`.
  **No vendor adapter exists, no real EMR or LIS has been qualified, and no facility is live through
  Connect** — a CONNECT facility still cannot reach `READY_FOR_ROUTING`. See `docs/CONNECT-AGENT.md`,
  ADR-057.

- **DHealth Exchange backend, DH-EX-001 through DH-EX-014B**: organization onboarding; patient/order
  creation; provider- and patient-driven lab assignment; accept/reject/processing; sample
  collection; multi-observation result entry/completion (`LabOrderItem → LabResult → LabObservation[]`,
  ADR-047); an explicit, separately authorized release step (ADR-044); immutable pre-release
  observation correction (ADR-045 Qs 1-3); an immutable release-version manifest and versioned
  `DoctorReview` (ADR-048 Model 3, DH-EX-014A); post-release amendment preparation/release under the
  same model (DH-EX-014B); a provider-scoped native Patient directory, separated from
  `PatientReference` source-identity provenance (ADR-051, DH-IDENTITY-001); explicit,
  platform-admin-configured provider-lab routing relationships, distinct from marketplace listing
  (ADR-052, DH-CLINIC-006); a provider-scoped, one-obligation-per-review result-review queue
  (`GET .../result-reviews`, DH-CLINIC-007) exposing that same ADR-048 machinery operationally; a
  lab-safe minimal patient-identity projection on the lab work-queue response, resolved from
  `Patient` (never `PatientReference`'s own snapshot fields) via a new N+1-safe batched
  multi-provider resolution path (DH-LAB-001); the lab work-queue's own remaining `LabOrder.items`
  lazy-load N+1 (disclosed but left unfixed by DH-EX-011/DH-LAB-001) is now closed via
  `selectinload(LabOrder.items)` — one bounded query per queue read regardless of order count, not
  one per row (DH-LAB-002); a controlled, immutable, `LabOffering`-scoped Native Lab result-structure
  template layer (`LabResultTemplate`/`LabResultTemplateObservation`, ADR-053) plus a batched,
  read-only order-scoped result-entry-plan resolving each `IN_PROGRESS` item's active template
  (DH-LAB-003) — additive to, and never constraining, the existing source-flexible
  `LabObservation`/`record_observation` model Connect will eventually use; DH-LAB-004 closes
  the native loop with first-write exact template-version pinning (survives later activation
  changes), a Native controlled batch value endpoint, required-definition-gated completion, a
  least-privilege release queue, and a release-inspection fingerprint that closes the
  inspect-then-stale-release race; **DH-LAB-005** closes the two gaps DH-LAB-004 deliberately left
  open rather than build unsafely — a Native controlled *correction* endpoint (browser submits only
  the new value; every structural field, including the pinned template context, is derived
  server-side from the predecessor's own template definition, both pre-release and inside an
  amendment) plus a complete post-release amendment lifecycle (discovery that survives a page
  refresh, controlled amendment correction, a fingerprint gate on amendment release mirroring
  DH-LAB-004's original-release gate exactly, a least-privilege amendment release queue that never
  lists an amendment with zero revisions, and a lab-facing exact-per-version content read). The raw
  free-structure observation/amendment-correction APIs both now refuse a template-bound
  predecessor outright (fail closed, proven with genuine red→green discrimination on both), and no
  new `LabOrderStatus`/amendment status was added — an order stays `COMPLETED` after an amendment
  release; PREPARING/RELEASED remain the only two amendment states, with no "cancel" state of any
  kind. DH-LAB-002/003/004 are CI-qualified and CI-PostgreSQL-qualified where CI runs it (SQLite +
  disposable `postgres:16-alpine`); **DH-LAB-005 is fully locally qualified** (adapter + UI suites,
  real-PostgreSQL concurrency races) **but not yet CI-qualified** — this ticket's own
  GitHub-Actions-budget-mode instruction targets one push for the whole ticket, so the push-and-CI
  step happens after this documentation, not before; see `PROJECT_STATE.md`'s DH-LAB-005 entry and
  the handoff for the actual CI run result. Migration head unchanged at
  `0025_native_result_binding`, current == heads == CI's `EXPECTED_HEAD` (this ticket needed no
  schema change). DH-LAB-004's and DH-LAB-005's own new concurrency logic is **additionally**
  qualified against a real, natively-installed PostgreSQL instance found in the sandbox — see §A.
  **Not** real-Keycloak-qualified, not runtime/browser-demonstrated, not production-deployed. Full
  ticket-by-ticket detail: `docs/MVP-1.0.md` §8, `PROJECT_STATE.md`.
- **DH-IDENTITY-001 — provider-scoped native Patient directory (ADR-051)**: `Patient`
  (`exchange_patients`) now exists as the provider-scoped human-workflow identity record, separated
  from `PatientReference`'s unchanged exact source-identity/provenance meaning. Native, idempotent
  registration (`POST .../organizations/{provider_id}/patients`), provider-scoped directory
  read/list/search, §19 registration recovery by idempotency key, and the legacy exact-identity
  bind endpoint's own compatibility behavior (new identity → new 1:1 Patient; existing identity →
  unchanged) are all implemented. Migration `0022` backfills every pre-existing `PatientReference`
  1:1, never merged. **No fuzzy/demographic matching anywhere** — proven by test that identical
  demographics under a different idempotency key or a different source identity always produce a
  second, distinct Patient. Database-level tenant safety: `PatientReference`'s composite foreign key
  to `Patient(id, provider_organization_id)` rejects a cross-provider link, proven against real
  PostgreSQL (`tests/integration/test_dhidentity001_postgres_qualification.py`, CI-wired) — SQLite
  does not enforce foreign keys by default, so this claim rests on the Postgres run, not the
  SQLite-based unit suite. Real-PostgreSQL-qualified: registration concurrency (PG1-PG8), the FK
  rejection above, and migration backfill/downgrade-guard qualification
  (`scripts/qualify_dh_identity_001_migration.py`, CI-wired). Adapter: **897 passed, 101 skipped**
  (855 pre-existing + 35 new patient-directory tests + 7 new legacy-binding-compatibility tests
  including a post-completion-review regression fix, 3 new Postgres-only tests correctly skipped
  locally; count corrected here from an earlier stale 896 — the fix landed after this line was first
  written). Native Clinic frontend (`ClinicPatientsPage.tsx`) migrated to the new directory API;
  `pendingPatientStorage.ts` rewritten so persistent browser recovery state carries an idempotency
  key only, never any demographic field. **Not** real-Keycloak/browser-runtime-qualified — same
  CI/vitest evidence level as the rest of DHealth Clinic, no new claim beyond that. See
  `DECISIONS.md` ADR-051 and `PROJECT_STATE.md`'s DH-IDENTITY-001 entry.
- **DH-CLINIC-005 — wrong-patient-safe human identity UX**: Native Clinic's Orders/Results screens
  no longer identify patients by truncated UUID fragments. Order/result read APIs
  (`LabOrderResponse`, `LabOrderSummaryResponse`, `ProviderOrderResultsResponse`,
  `ProviderReleaseVersionContentResponse`) now carry a server-resolved `patient` identity projection
  (name/DOB/contact only), resolved through `patient_reference_id -> PatientReference.patient_id ->
  Patient`, batched per list (proven not N+1 by counting actual SQL statements) — never a
  client-supplied id, never the legacy `PatientReference.display_name` snapshot. A reference whose
  linked Patient cannot be resolved fails closed as a server integrity error, never silently renders
  `patient=None`. No schema migration — additive read-side API only. Frontend: a shared
  `PatientIdentity` component (name prominent, truthful "Nom non renseigné"/"Identité limitée"
  fallbacks, timezone-safe DOB formatting, full technical id never truncated) now drives
  Orders/Results/Patients; `ClinicPatientsPage.tsx`'s native workflow operates on
  `PatientDirectoryEntry` directly (no more `ClinicPatientSummary` compatibility bridge); a real,
  previously-untested async race in `ClinicResultsPage`'s result-detail loading (a stale earlier
  selection's response could overwrite the current one) is fixed and covered by 7 mandatory
  concurrency tests (RR1-RR7). Adapter: **911 passed, 101 skipped** (897 baseline + 14 new). UI
  clinic suite: **171 passed** across 10 files. **Not** real-Keycloak/browser-runtime-qualified —
  same CI/vitest evidence level as the rest of DHealth Clinic. See `DECISIONS.md` ADR-051's
  2026-09-17 clarification and `PROJECT_STATE.md`'s DH-CLINIC-005 entry.
- **DH-CLINIC-006 — provider-assigned laboratory workflow (ADR-052)**: `ProviderLabRoute`
  (`exchange_provider_lab_routes`, migration `0023_provider_lab_routes`) is the missing
  routing-authority concept behind DH-EX-010's `assign_by_provider` — a platform-admin-configured,
  explicit "may this provider route to this lab" fact, independent of `LabDirectoryProfile.listed`
  and never joined against `LabPrice` anywhere in the new code path. New APIs: route management
  (`PUT .../lab-routes/{lab_id}`, platform-admin only, idempotent, audited), the provider's own
  route directory (`GET .../lab-routes`, CLINICIAN), and the order-specific whole-order eligibility
  read (`GET .../orders/{order_id}/eligible-labs`) that the routing UI actually consumes — zero
  eligible labs is a real, valid `[]` response, never an error or a fabricated fallback.
  `assign_by_provider` now requires an active route *inside its own locked mutation*
  (`ProviderLabRouteNotActive` → 409), checked before every other re-check, so a future caller
  bypassing HTTP cannot skip the routing boundary; deactivating a route never touches an
  already-made `LabSelection`. `LabOrderResponse`/`LabOrderSummaryResponse` gained a batched,
  N+1-safe `lab_assignment` projection resolved from the canonical `LabSelection`, present whenever
  one exists regardless of the order's current status (a SELECTED-then-REJECTED order still shows
  the lab that rejected it) and failing closed (`LabAssignmentIntegrityError`) only when a status
  that structurally requires one has none. Real-PostgreSQL-qualified (§20 PG1/PG2/PG5: concurrent
  same-lab assignment converges to one `LabSelection`; concurrent different-lab assignment has
  exactly one winner and one clean conflict; a route-deactivation race resolves to one of two
  truthful outcomes only, never a torn state). Frontend: a new shared `LabRoutingStep` component
  (patient + ordered tests + eligible labs + explicit confirmation, no price/ranking of any kind,
  structurally unable to call the marketplace/price/patient-selection endpoints) drives a new
  `'routing'` step in the order wizard (strictly after durable commit, strictly before
  confirmation) and a new "Choisir un laboratoire" action on both the Orders page and the Patient
  workspace's order history, for any order still `AWAITING_LAB_SELECTION`. Adapter: **960 passed**
  (adapter suite grew by ~49 tests; one unrelated pre-existing test-isolation flake confirmed by
  individual re-run, not a regression). UI: full suite passes (WSL2/vitest worker-pool flakiness
  non-deterministically drops a handful of unrelated files per run, confirmed individually green;
  clinic suite alone: **150 passed** across 10 files, up from 146). **Not**
  real-Keycloak/browser-runtime-qualified — same evidence level as the rest of DHealth Clinic.
  **Does not solve "no double entry"** (ADR-043 Law 2) — this proves DHealth-side routing to a
  DHealth lab *organization* only, not that any real lab staff system received the order; that is
  DH-PILOT-TOPOLOGY-001's own future choice. **DH-CLINIC-004B remains separately open, unchanged.**
  See `DECISIONS.md` ADR-052 and `PROJECT_STATE.md`'s DH-CLINIC-006 entry.
- **DH-CLINIC-003C — durable provider lab order intent (ADR-049)**: re-verified this ticket, fresh
  local run on this exact commit, not carried forward — **adapter: 854 passed, 98 skipped**;
  **UI: 26 test files, 370 tests, all passing**. Bidirectional `LabOrder.intent_id`/
  `LabOrderIntent.committed_lab_order_id` ownership, cross-provider/cross-clinician anti-enumeration
  (404, not 403), server-derived practitioner identity (request body cannot supply one), historical
  (pre-003C, `intent_id IS NULL`) order replay preserved through the legacy endpoint, and
  concurrency PG2-PG6 (real PostgreSQL) all re-confirmed present and green. **Scope-qualified, not
  closed**: qualified for same-browser-profile reload/recovery only — no cross-device or
  cross-browser discovery workflow exists or is claimed; no real-Keycloak or browser-runtime
  qualification exists for this ticket either. Formal closure (`DH-CLINIC-003C CLOSED: Y/N`) remains
  Liban's own decision, per his explicit instruction — this status re-verifies the technical claims,
  it does not self-declare closure. See `PROJECT_STATE.md`'s DH-CLINIC-003C entries and
  `DECISIONS.md`'s ADR-049.
- **OpenMRS/OpenELIS maternity/birth-centre stack**: real, running, unchanged by this ticket. See
  `CURRENT_STATE.md`'s "Assessed 2026-08-17" inventory for the detailed, still-accurate breakdown.
- **Native browser authentication + same-origin Exchange routing (ADR-050, DH-CLINIC-004A)**: a
  public, secretless `dhealth-native-ui` Keycloak client (Authorization Code + PKCE S256, standard
  flow only) now exists in the `dhealth-exchange` realm, carrying the same `dhealth-exchange-api`
  audience the backend already validates unchanged — proven by a new adapter unit test that mints a
  token under the new client's `azp` and confirms `TokenValidator` accepts it. `app.${BASE_DOMAIN}`'s
  Caddy vhost now proxies `/exchange/v1/*` to the adapter, same path scoping as the existing
  `exchange.${BASE_DOMAIN}` vhost. A shared, Clinic-independent auth layer
  (`ui/src/features/auth/exchange/`) drives `keycloak-js` with `check-sso` (never a redirect as an
  init side effect), holds tokens in memory only, single-flights and fails closed on refresh, and
  gates Clinic's data layer so `/me` cannot fire before real authentication exists.
  **CONFIG-QUALIFIED for the auth layer itself; the realm-reconciliation gap this ADR originally
  flagged as unbuilt is now closed and real-Keycloak-runtime-qualified (DH-CLINIC-004B,
  `keycloak/reconcile-exchange.sh`, run `35210377720`, commit `7752bf9` — real pinned Keycloak
  26.7.1, `Exited (0)`, reconciler's own success log observed).** That same real run also found and
  fixed a genuine defect this ADR's config-level review had missed twice: two realm-JSON description
  fields exceeded Keycloak's `VARCHAR(255)` column limit and crashed the entire realm import against
  a real database. ~~**Still not observed: a real browser completing the redirect/login/PKCE round
  trip.**~~ The qualification run's full-stack boot stalled on the pre-existing, unmodified
  `openmrs-backend`/`openelis-webapp` services never reaching healthy within CI's wait window, which
  blocks Caddy and therefore every `app.${BASE_DOMAIN}` vhost the browser suite needs — consistent
  with this repo's own documented ~50-minute-plus full-stack cold boot (`.github/workflows/ci.yml`
  header), not a defect in this ADR's own code. **[Superseded 2026-09-25, observed;
  re-stated here 2026-09-26, DHEALTH-STATE-001.]** The struck sentence was true until
  DHEALTH-NATIVE-MVP-1.0. A real Chromium now completes the redirect / real-Keycloak login /
  Authorization Code + PKCE round trip against the `dhealth-native-ui` client and arrives at an
  authenticated workspace — repeatedly, at the start of every journey, authorization and recovery
  phase. It happens on the **canonical Native topology**, which has no OpenMRS/OpenELIS service to
  stall on; the CI capacity problem described above was never solved, it was routed around by a
  deployment that does not need those services. **The auth layer's own status is therefore no longer
  "CONFIG-QUALIFIED" in present tense — it is runtime- and browser-qualified on that topology**
  (§C banner). See `DECISIONS.md` ADR-050's 2026-09-17 overlay, ADR-061, and `PROJECT_STATE.md`'s
  DH-CLINIC-004B and DHEALTH-NATIVE-MVP-1.0 entries.
- **Native demo reset-safety boundary and baseline/reset idempotency (DH-DEMO-001)**:
  `demo_seed.py`'s three-fact refusal (wrong database name; a process-own env var; an unreadable/
  missing marker table) and its `TRUNCATE ... CASCADE` reset (clears every workflow table,
  preserves every baseline row, idempotent on replay) are **real-PostgreSQL-qualified** — 2 tests,
  4 clean executions, including a genuine positive control (a differently-named scratch database
  was correctly refused) and a genuine permission bug found and fixed (the marker table's missing
  `GRANT SELECT`, which without the fix would have made a non-demo database raise a raw DB error
  instead of a clean refusal). See `PROJECT_STATE.md`'s DH-DEMO-001 entry.
- **Patient access metadata read and patient result-status projection (DH-PATIENT-001)**: a
  provider-authorized `GET .../orders/{order_id}/patient-access` behind the identical
  `CLINICIAN`-for-the-owning-provider boundary as issuance/revocation, returning `{active,
  expires_at, access_mode}` and never the raw token, its SHA-256 digest, or the grant UUID; an
  expired grant reads as inactive with a `null` expiry, and reading it never mutates `revoked_at`.
  Plus `PatientOrderResponse.result_status`, resolved from the order's *latest released version* and
  that version's own `DoctorReview`, so a version-2 amendment release correctly reverts the
  patient's view to `AWAITING_CLINICIAN_REVIEW` instead of presenting a disposition recorded about
  superseded content. A grandfathered `COMPLETED`-without-release order uses its one honest unbound
  review row; no release version is ever fabricated for it. **Unit-qualified** (18 adapter tests,
  incl. the mandated amendment regression and a no-result-value assertion against the serialized
  JSON string rather than only a field list) with **7 backend negative controls executed**; the
  remote CI run for this work is reported in the DH-PATIENT-001 session handoff, not asserted here. No
  migration; Alembic head unchanged at `0025_native_result_binding`. One real bug found and fixed:
  `GrantRecord` returned naive datetimes on SQLite read-back, which would have serialized an
  offset-less `expires_at` a browser parses as local time. See `PROJECT_STATE.md`'s DH-PATIENT-001
  entry.

## D. BUILT BUT NOT RUNTIME-PROVEN

**[Dated banner, 2026-09-26, DHEALTH-STATE-001 — this section's title is now only partly true of
its own contents, and that is stated rather than repaired by deletion.]** When these bullets were
written, "not runtime-proven" meant *nothing here has ever run outside jsdom*. That is no longer the
case: §C's dated banner lists, surface by surface, which of the screens described below a real
browser has driven against real Keycloak, a real adapter and real PostgreSQL. The bullets are kept
verbatim as each ticket's own record; **where one says no browser has rendered something, read §C's
table first** — it is authoritative for present state, and it also names the surfaces here that
genuinely remain jsdom-only (native patient registration, the correction/amendment lifecycle, the
DHealth Patient status screen, and the platform/facility-enrollment screens). Nothing in this
section is production-deployed, human-qualified, or evidence about real patients.

- **DHealth Clinic native frontend** (`ui/src/features/clinic/`) **[no longer a separate
  application — updated 2026-09-20, DH-MVP-POLISH-001: these pages are now sections of the one staff
  application (bullet below, ADR-054). `ClinicApp`/`ClinicShell` are deleted and `ClinicProvider` no
  longer fetches `/me`; everything this bullet claims about behaviour still holds, reached through
  `Patients`/`Ordres`/`Résultats` rather than through a product the user opens.]**: patient registration/search
  against the native Patient directory (DH-IDENTITY-001, §C), diagnostic order creation against the
  Exchange catalog using the directory entry's `primary_patient_reference_id` compatibility bridge,
  durable
  order-intent recovery across reload/restart, order status, results/release-version history, and
  (as of DH-CLINIC-004A, §C) a complete, config-qualified authentication path. Built and
  component/UI-tested. **Still cannot be called deployed.** DH-CLINIC-004B has since observed a real
  Keycloak import of the two-client file and real reconciliation against it (§C) — ~~but no browser
  has actually completed a redirect/login/PKCE round trip, and no live Caddy routing has been
  exercised end to end, because the full stack (Caddy included) never finished booting in the one
  qualification run attempted.~~ **[Superseded 2026-09-26, DHEALTH-STATE-001: the struck clause was
  true of the maternity-coupled stack and is no longer true of the product.]** On the canonical
  Native topology a real browser completes that round trip and every request the screens make
  reaches the adapter **same-origin through Caddy** (`app.{$BASE_DOMAIN}` → `/exchange/v1/*` →
  `adapter:8000`, `deploy/native/Caddyfile`). Patient **list/open** (the seeded patient is found in
  the directory list and opened — **no search string was ever typed**), the order wizard, routing, orders,
  the review queue and the patient-access handoff have all been driven that way; the native patient
  **registration** form has not (§C banner). **`docker-compose.native.yml` is still not a production
  deployment** — `BASE_DOMAIN=localhost`, Caddy's internal CA, one workstation, synthetic data.
  DH-CLINIC-004B's *purpose* is served by this; its own stated closure criteria are not, and it stays
  open (§I).
- **DHealth Lab native frontend** (`ui/src/features/lab/`) **[no longer a separate application —
  updated 2026-09-20, DH-MVP-POLISH-001: same as Clinic above. `LabApp`/`LabShell` are deleted and the
  `canWorkOrders`/`canReleaseResults` capability model described below is now derived once, from the
  active membership, by `WorkspaceProvider` — the release/entry authority separation it encodes is
  unchanged and still covered by a negative control.]** (DH-LAB-001, extended by DH-LAB-002,
  DH-LAB-003, DH-LAB-004, and DH-LAB-005): authentication (reusing the same shared
  `ExchangeAuthProvider`/`ExchangeAuthGate` Clinic uses — no second OAuth client), a capability
  model (`canWorkOrders`/`canReleaseResults`) that now drives real navigation — a dual-role user
  sees Accueil/Ordres/Libération, a releaser-only user sees Accueil/Libération with no route to the
  `LAB_TECH` queue at all — a lab selector for multi-lab users with immediate (render-phase) state
  clearing on switch, an assigned-order work queue, a safe order-detail panel driven by the
  lab-safe minimal patient projection (§C), accept/reject with explicit reject confirmation and
  three-way lost-response recovery, an explicit collection-milestone confirmation and a
  start-processing confirmation, each with its own lost-response recovery and
  generation/tenant-ref race protection. DH-LAB-004 turned DH-LAB-003's read-only
  result-structure preview into a controlled Native entry form (`NativeResultEntryForm`); a new
  "Libération" surface (`LabReleasePage`) gave releaser-only users a least-privilege release queue,
  full result inspection, and a fingerprint-gated release confirmation. **DH-LAB-005 closes the
  correction/amendment gap this surface deliberately left open**: an already-entered Native value
  now shows "Valeur actuelle" plus a Corriger control (browser submits only the new value; the
  exact confirmation wording is required), with correction history exposed via the existing
  correction-history read (current vs. superseded always labeled unambiguously, never as equally
  current); a COMPLETED order's panel now discovers, starts, or resumes a post-release amendment
  (server-driven, never dependent on browser memory — "Commencer un amendement"/"Reprendre
  l'amendement" are mutually exclusive, and no "Annuler l'amendement" control exists anywhere,
  since PREPARING/RELEASED are the only two amendment states that exist); a new
  `AmendmentWorkspace` component gives the durable-revision warning before an amendment's first
  correction; `LabReleasePage`'s "Libération" tab gained an "Amendements" section (queue,
  inspection, fingerprint-gated release, with the exact distinct stale-fingerprint wording an
  amendment race requires) rather than a disconnected second page; a new `ReleaseVersionHistory`
  component exposes every release version (status always textual, never color-only) and one
  version's exact, immutable content on demand. Built and component/UI-tested only — ~~**same "built
  but not runtime-proven" boundary as DHealth Clinic above**: no real-Keycloak or real-browser
  qualification exists for it either.~~ **[Corrected 2026-09-26, DHEALTH-STATE-001 — and the
  correction is deliberately partial.]** A real browser signed in as a real Keycloak lab technician
  and drove the queue, acceptance, the collection milestone, start-processing, template-driven entry
  and gated completion; a **separate** releaser identity drove the least-privilege release queue and
  the fingerprint-gated release, and the technician was confirmed to have **no route to the release
  surface at all**. **The DH-LAB-005 half of this bullet — correction, and the whole post-release
  amendment lifecycle — has never run in a browser** and keeps the original boundary exactly as
  written. LIS/Connect integration remains explicitly **not** part of
  this surface. See `DECISIONS.md` ADR-043's 2026-09-18 clarification, ADR-053 and its
  DH-LAB-004/DH-LAB-005 addenda, ADR-044's DH-LAB-004 addendum, ADR-045's DH-LAB-005 addendum,
  ADR-048's DH-LAB-005 addendum, and `PROJECT_STATE.md`'s DH-LAB-001/DH-LAB-002/DH-LAB-003/
  DH-LAB-004/DH-LAB-005 entries.
- **DHealth Patient native frontend** (`ui/src/features/patient/`, DH-PATIENT-001) — the first
  patient-facing surface in the product, and the only one with no authenticated user: a
  single-screen, mobile-first, French-first status view authorized solely by DH-EX-005's
  pre-existing order-scoped opaque grant, reached through a QR code or link whose credential lives
  in the URL **fragment** and is scrubbed from the address bar on ingestion. It renders order
  status, the assigned laboratory's public contact detail, the requested tests by human name, and
  one server-owned follow-up projection — **never** a result value, reference range,
  interpretation, price, internal identifier, or clinician identity. Mounted before any
  `ExchangeAuthProvider` exists, so a patient is structurally incapable of being redirected to
  Keycloak. Paired with a Clinic-side `PatientAccessHandoff` that issues/revokes/rotates access and
  produces a locally-generated QR plus a printable sheet for patients with no smartphone. Built and
  component/UI-tested only — **same "built but not runtime-proven" boundary as DHealth Clinic and
  DHealth Lab above**: **no real browser has rendered this screen**, no real phone has scanned one of
  these QR codes, and no printer has produced one of these sheets. **[Narrowed 2026-09-26,
  DHEALTH-STATE-001 — one half of this bullet moved and the other did not.]** The **clinic-side**
  `PatientAccessHandoff` *has* now been driven in a real browser by a real Keycloak clinician:
  issuing an order-scoped grant, enrolling a synthetic destination for notification, and the
  product's own on-screen confirmation that an active grant exists
  (`ui/e2e/native/03-recovery-source.spec.ts`). The **patient-facing screen itself remains
  unrendered** — the restored grant was replayed against `/exchange/v1/patient/order` at the API
  level (RECOVERY-5/RECOVERY-8), which proves the grant survived a destructive restore and stays
  bound to exactly its own order, and proves nothing whatever about the page. Responsive layout was written
  mobile-first and reasoned about at 320/375/430px but **not measured** — jsdom cannot measure
  layout. No SMS/WhatsApp/email/voice transport was added; `ResultDelivery` is untouched and
  viewing a status page deliberately does **not** mark it `DELIVERED`. See `docs/ARCHITECTURE.md`
  §19 and `PROJECT_STATE.md`'s DH-PATIENT-001 entry.
- **Facility enrollment and integration readiness** (`adapter/.../facility_enrollment.py`,
  `ui/src/features/platform/`, DH-MVP-ONBOARD-001) — the control plane for joining the network.
  A platform operator enrols an organization and its enrollment in one transaction, attaches a first
  administrator found through a bounded platform-only identity lookup — or attaches people later
  from the platform-scope facility **Équipe et accès** section, which grants access and never removes
  it (the *facility-owned* screen of the same name does remove it — DHEALTH-NATIVE-ACCESS-001) —
  records `NATIVE` or `CONNECT` explicitly, captures a Connect facility's software/vendor/version/hosting and the interfaces it
  reports, and reads a server-derived answer to whether production orders may be routed there.
  **Four facts stay separate**: enrolled, configured, operationally ready, production-routable.
  Native routing requires an active CLINICIAN (provider) or an active LAB_TECH *and*
  LAB_RESULT_RELEASER (laboratory), one person may hold both lab roles, and losing that staffing
  blocks new routing without rewriting the configured route. A **CONNECT facility cannot reach
  production readiness at all** while no connector exists — enforced in the repository, and
  unaffected by staffing, offerings, templates or a verified interface. Platform scope is distinct
  from organization scope and **no organization is fabricated** for a platform administrator with no
  membership. Built and component/UI-tested plus real-PostgreSQL migration qualification — **no CI
  conclusion exists for it** (see §A), no E2E has run, and no person has used it in a browser. See
  `DECISIONS.md` ADR-055, `docs/ARCHITECTURE.md` §21, `docs/FACILITY-ONBOARDING.md`, and
  `PROJECT_STATE.md`'s DH-MVP-ONBOARD-001 entry.
- **One authenticated DHealth staff application** (`ui/src/features/staff/`, DH-MVP-POLISH-001) —
  supersedes the separate `ClinicApp`/`LabApp` shells and the application-switch buttons that reached
  them. `StaffApp` loads `/me` **once** for the whole application; the active organization plus the
  user's roles in it decide which of `Aujourd'hui / Patients / Ordres / Résultats` is available and
  what each contains, so a person with memberships in a clinic and a laboratory switches
  *establishment*, not product. `ClinicProvider`/`LabProvider` survive as projections of that one
  session and fetch nothing, which is why the existing Clinic and Lab page tests remained meaningful
  rather than being rewritten to match new code. Frontend capability derivation is **usability, not
  security** — each of the five capabilities is documented against the backend gate that actually
  enforces it, and the server refuses regardless of what the browser rendered. An organization switch
  discards the org-scoped subtree by `key`, so a late response from one organization cannot render
  under another. There is deliberately **no `Réglages` item**: see §I. Built and component/UI-tested
  only — ~~**same "built but not runtime-proven" boundary as every native surface above**: no real
  browser has rendered this shell, no real person has used the establishment selector, no E2E has
  run, and no font has been observed loading.~~ **[Superseded 2026-09-26, DHEALTH-STATE-001, except
  for one clause.]** A real browser has rendered this shell repeatedly: every journey, authorization
  and recovery phase begins by signing in through real Keycloak and then navigating **by this
  shell's own navigation landmark**, and the `Se déconnecter` control is the assertion that an
  authenticated session exists. The claim the shell was built to make — *navigation derives from the
  membership the token actually carries* — is now observed rather than reasoned: a technician
  identity had no `Résultats` section at all while a releaser identity did. **Still true and
  deliberately kept:** no real *person* has used the establishment selector (each e2e identity holds
  one membership, so no live establishment switch has occurred), and no font has been observed
  loading. `no E2E has run` is struck. See `DECISIONS.md` ADR-054, `docs/ARCHITECTURE.md` §20,
  `docs/UI-002-DESIGN-LANGUAGE.md`, and `PROJECT_STATE.md`'s DH-MVP-POLISH-001 entry.
- **Native demo environment** (DH-DEMO-001, `docker-compose.native-demo.yml`,
  `scripts/demo/*.sh`, `adapter/src/daher_adapter/exchange/demo_seed.py`): a dedicated Compose
  project running the real Keycloak/adapter/DHealth-UI/Caddy code paths, deliberately never
  waiting on OpenMRS/OpenELIS (which stays completely untouched), with a P0 reset-safety boundary
  (three independent facts, real-PostgreSQL-qualified — §C) and deterministic synthetic-only
  baseline data. Real Keycloak-backed authentication throughout — grepped, zero demo-conditional
  branch exists in any authentication/request-handling code. **[Corrected 2026-09-20,
  DH-PATIENT-001 — this bullet previously said "the demo has never actually booted anywhere" and
  called the CI job "its first attempt, not a confirmation"; that attempt has since succeeded.]**
  **Real native Compose boot: qualified in CI** (run `35390037354` at `a5992bd`, job `native demo —
  real boot (DH-DEMO-001)` green with all 10 jobs — verified against the GitHub API, not carried
  forward from a report). ~~**Real Keycloak interactive login: not yet qualified. Real browser golden
  journey: not yet qualified.**~~ This sandbox's Docker still cannot pull real image blobs from the
  CDN, so that boot is CI's evidence, not a local observation. **[Superseded 2026-09-26,
  DHEALTH-STATE-001 — both struck lines are now false, and the file this bullet is named after has
  changed role.]** Real Keycloak interactive login and the real-browser golden journey are both
  qualified (DHEALTH-NATIVE-MVP-1.0, §C banner). Since ADR-061, **`docker-compose.native-demo.yml`
  is an overlay, not a topology**: the canonical Native deployment is `docker-compose.native.yml`
  and the demo file adds only demo users, the demo database name and the seed authority — it no
  longer renders on its own, which is the disclosed cause of CI's two native-demo jobs failing at
  `config` (§A, DHEALTH-NATIVE-DEPLOY-001). Images are now fetched out-of-band and loaded locally,
  because the blob CDN is still unreachable from this environment. See `PROJECT_STATE.md`'s
  DH-DEMO-001 entry for the full evidence matrix.
- Everything in §C's Exchange-backend list beyond CI/PostgreSQL qualification: ~~no real-Keycloak
  qualification, no manual live/browser demonstration, no production deployment, for any Exchange
  ticket.~~ **[Split 2026-09-26, DHEALTH-STATE-001 — this line bundled four claims that no longer
  share a verdict.]** Real-Keycloak qualification: **now exists** for the backend paths the journey
  exercised, via real JWKS token validation and authorization on the canonical Native topology
  (§C banner, §H gate 1). Automated real-browser demonstration: **now exists**, for those same
  paths. **Manual live/browser demonstration: still does not exist** (§H gate 3). **Production
  deployment: still does not exist**, for any Exchange ticket. Exchange surfaces the journey never
  touched — amendment release, platform administration, the patient screen — keep the original
  boundary in full.

## E. BLOCKED / OPEN

- **Human usability qualification of the native clinic and laboratory workflows**: **not done, and
  it is the only thing that would make the claim.** DH-USABILITY-001 (2026-09-24) reviewed both
  workflows in a real browser, fixed one P0 and five P1s, and is labelled `INTERNAL WORKFLOW
  REVIEW` exactly because no clinician, front-desk user or laboratory technician has used it. §40:
  automated browser tests are not a substitute. The instrument for the sessions is written out and
  empty in [`docs/USABILITY-QUALIFICATION-001.md`](docs/USABILITY-QUALIFICATION-001.md) Part B;
  what is needed is one clinician, one front-desk participant and one laboratory participant for a
  day — **defined 2026-09-24**: they must be people who genuinely do those jobs (not developers or
  executives), and they are **not** to be trained on DHealth before the session; hesitation, a wrong
  click and any moderator help are the evidence. Two P2s stay open with stated reasons: the clinic
  order actions are clipped at a tablet's right edge with one of them reachable only by scrolling
  the table (O-1b — **tablet is a supported surface**, so this is carried, not closed), and a result
  detail opens below the queue with no scroll or focus move. The handset version of the first is
  **closed by ADR-060**.
- **Staff workspaces are a desktop and tablet product — ADR-060, decided 2026-09-24.** DHealth
  Patient stays mobile-first; Front Desk, Clinician, Lab Staff and Admin must work well at 1280 and
  768; a **~390px handset is not a supported primary staff-work surface** for MVP 1.0. Staff pages
  must not catastrophically break there, but card-layout conversion is not required, and the dense
  workflows (documentation, result entry, release, billing, administration) need no 390px layout
  before pilot. **Tablet must not be weakened**: a workflow that works at 1280 and breaks at 768 is
  a defect. Later tickets may add handset-friendly staff workflows where *observed* usage justifies
  them. `docs/MVP-1.0.md` §3 carries the same boundary.
- **Staff identity stays minimal — decided 2026-09-24, and it is a constraint on future work rather
  than a change to present code.** Name, role(s) and login identity are required; **phone is
  optional**; email is optional unless authentication technically needs it. Staff onboarding is
  never blocked for want of a personal telephone number, and a facility's phone/WhatsApp stays
  separate from a staff member's personal contact details. MFA, password recovery and on-call
  alerting, if they ever need a number, are introduced deliberately as their own work. Nothing today
  asks a staff member for one: staff identity is the Keycloak identity plus memberships and roles,
  and `contact_value` belongs to **patient** references only.
- **WhatsApp patient notification against real credentials**: **still `BLOCKED`, and the pinned P0
  rule is unchanged** — a mock is never done. **2026-09-24 dated update (DH-NOTIFY-001, transport
  replaced same day by DH-NOTIFY-META-001/ADR-059):** the transport, the content policy, the durable
  queue, the dispatcher and the signed delivery-receipt webhook exist and are CI- and
  real-PostgreSQL-qualified (§C), and WhatsApp now goes **directly to Meta's Cloud API** rather than
  through Twilio, which is removed. A real-provider qualification *path* also now exists
  (`.github/workflows/qualify-meta-whatsapp.yml`, `docs/META-WHATSAPP-RUNBOOK.md`) and **has never
  been run**. **No real Meta endpoint has been contacted, no credential exists, and no message has
  ever reached a handset**, so this stays `BLOCKED` and pilot gate 5 is not cleared. What changed is
  that clearing it is now a matter of credentials and one manual run, rather than of code.
- **SMS patient notification**: **no provider selected.** The domain understands the channel and no
  deployment can send on it; availability is server-derived, so the staff UI offers nothing it
  cannot deliver. Choosing an SMS vendor was explicitly out of DH-NOTIFY-META-001's scope.
- **Meta's primary documentation was never read** from the implementing environment
  (`developers.facebook.com` unreachable). The wire shapes are corroborated from independent
  secondary sources, not verified against Meta's own text; every value that could drift is
  configuration with no default. Confirming them against the live API is the first qualification
  task.
- ~~**`ResultDelivery` semantics**~~: **RESOLVED 2026-09-27, ADR-064 — deprecated /
  compatibility-only, owns nothing, no migration.** Original text follows: created `PENDING` at order
  creation and no code path advances it
  past `PENDING`. Unchanged by DH-NOTIFY-001, deliberately: a patient's handset accepting an SMS is
  not the ordering provider organization receiving a result, and what `DELIVERED` should mean there
  remains an open product decision (`docs/MVP-1.0.md` §10 item 6).
- **Email and voice patient notification**: not built, and `§28` non-goals rather than gaps.
- ~~**Real Keycloak qualification of authorization boundaries**: still not run for token
  validation/audience/authorization decisions — every such boundary is proven only against a locally
  generated RSA keypair (`StaticKeySource`), never a live identity provider. (Narrower than before:
  DH-CLINIC-004B did runtime-qualify realm/client *reconciliation* against a real Keycloak — §C — but
  that is a deployment-provisioning proof, not a token-validation one.)~~
  **RESOLVED [2026-09-26, DHEALTH-STATE-001 — struck rather than deleted, because it was true for
  every ticket that cited it].** The canonical Native deployment points the adapter at a **real
  Keycloak JWKS endpoint** (`EXCHANGE_OIDC_ISSUER` / `EXCHANGE_OIDC_JWKS_URL` /
  `EXCHANGE_OIDC_AUDIENCE`, `docker-compose.native.yml`), so `JWKSKeySource` — the production path,
  not `StaticKeySource` — is what validated the tokens a real browser obtained from a real login.
  Authorization was then exercised **negatively** with those same tokens: an unauthenticated browser,
  an unauthenticated API call, a clinician reading the laboratory's queue, a technician acting as the
  provider, and a fabricated organization id were each refused. The proof that the suite can see the
  boundary break is NC-NATIVE-1 (a different expected audience fails the journey before any workflow
  runs) and NC-NATIVE-3 (weakening `require_clinician`'s tenant check made the cross-tenant read
  answer **200** and turned `NEG-3` red). `StaticKeySource` remains what the unit suite uses, which
  is appropriate and is no longer the *only* evidence. **Pilot gate 1 clears on this** (§H).
- ~~**Real-browser qualification of native Clinic authentication**: the config-level pieces exist
  (§C), and realm reconciliation is now real-Keycloak-qualified (§C), but a real OIDC round trip
  against a live browser/Caddy stack has never been observed — the one qualification run attempted
  stalled before Caddy ever started (§D). This is DH-CLINIC-004B's open item (§I).~~
  **RESOLVED [2026-09-26, DHEALTH-STATE-001].** Observed, on the canonical Native topology:
  `https://app.<BASE_DOMAIN>` → real Keycloak login form → Authorization Code + PKCE → callback →
  an authenticated workspace, with every subsequent Exchange call made **same-origin** and proxied
  by Caddy from `/exchange/v1/*` to the adapter. **Pilot gate 2 clears on this** (§H). It does not
  close DH-CLINIC-004B, whose own criteria were written against the maternity-coupled stack (§I).
- **Full-stack (Caddy-included) boot on a shared GitHub-hosted CI runner**: `openmrs-backend` and
  `openelis-webapp` did not reach Docker `healthy` within a 3600s wait window in the one attempt made
  (DH-CLINIC-004B, run `35210377720`). Consistent with this repo's own documented ~50-minute-plus
  cold boot being a manual/fresh-VM procedure, not a shared-runner one — see `.github/workflows/ci.yml`'s
  header. Not a code defect found in this attempt; next step is a runner with more capacity
  (self-hosted or a larger GitHub runner), not a different test design. **[Still true, and its blast
  radius narrowed 2026-09-26, DHEALTH-STATE-001: this no longer blocks Native.]** The canonical
  Native deployment has no OpenMRS/OpenELIS service to wait on, so Native browser qualification was
  reached by removing the dependency rather than by finding a bigger runner. This item remains open
  for the **maternity/reference-connector** stack, which still needs that capacity.
- **Cross-provider or national patient lookup/matching by demographic**: does not exist and is
  explicitly out of Launch-1 scope (ADR-051 Decision 11) — this is a boundary, not a gap. **2026-09-17
  dated update (DH-IDENTITY-001):** provider-scoped native patient *registration* by name (display
  name, optional exact DOB, optional contact) and provider-scoped substring search now exist
  (§C) — the narrower claim this bullet originally made ("what exists is only exact-identity
  binding, DH-EX-009") is superseded for the provider-scoped case; DH-EX-009's exact-identity bind
  endpoint itself is unchanged and still exists for connector/integration use.

## F. NOT BUILT

- **DHealth Lab LIS/Connect integration.** DH-LAB-001 through DH-LAB-004 built the full native
  loop (authentication/queue/accept/reject, collection-milestone recording and start-processing,
  the controlled result-structure template layer, controlled entry, required-gated completion, a
  least-privilege release queue, and fingerprint-gated release); §D's DH-LAB-005 entry closed the
  remaining correction/amendment gap DH-LAB-004 deliberately left closed rather than building
  unsafely. Demo seed data now exists (§D, DH-DEMO-001), but LIS/Connect integration still does
  not.
- ~~**DHealth Connect** (EMR/LIS integration layer, beyond the OpenMRS/OpenELIS reference track
  itself): no code of its own exists yet.~~ **[Corrected 2026-09-26, DHEALTH-STATE-001 — this
  sentence had been directly contradicted by §C for three days, and the contradiction is removed
  here rather than left for a reader to arbitrate.]** DHealth Connect's **contract, runtime and
  on-premises agent exist and are synthetically qualified**; **no real vendor adapter has been
  implemented or qualified.** Built: the external integration contract (`docs/CONNECT-CONTRACT.md`,
  ADR-056), connector identity and lifecycle, the durable qualification model with `DECLARED` vs
  `EXERCISED` evidence strength, a durable delivery runtime (`connector_deliveries`, claim leases,
  exponential retry, dead letters that are unresolved rather than deleted), the on-prem agent
  (`daher_adapter.connect_agent`), organization-and-source-scoped correlation with no fuzzy
  matching, durable provider-delivery obligations, and synthetic provider/lab qualification paths
  that reach `connector_qualified = true` (ADR-057, `docs/CONNECT-AGENT.md`, `docs/CONNECT-QUALIFICATION.md`).
  **Not built and not proven:** any named real vendor adapter, any real external EMR integration,
  any real external LIS integration, any production-routable CONNECT facility, any live partner
  facility. Nothing in this repository has ever contacted an external EMR or LIS. A CONNECT facility
  therefore still cannot reach `READY_FOR_ROUTING`, pinned by
  `test_RQ8_qualified_is_still_not_production_routable`. That is DH-CONNECT-ADAPTER-001's subject
  (§I). **[Updated 2026-09-20, DH-PATIENT-001 — this bullet
  previously also named "DHealth Patient (assisted-digital patient layer)" as having no code of its
  own; a first DHealth Patient surface now exists, see §D.]** What remains not built on the patient
  side specifically **[updated 2026-09-24, DH-NOTIFY-001 — this bullet previously named push
  notification of any kind as having no code; SMS and WhatsApp now have a full implementation that
  has never been run against a real provider, see §C and §E]**: **email and voice** notification
  (`§28` non-goals rather than gaps), detailed result delivery to patients (excluded by policy, not
  pending — patients see status only), patient accounts, family/proxy authorization, and
  offline/PWA capability.
- **Any patient-facing marketplace or pricing surface.** Not built, not scheduled for Launch 1 —
  preserved as a product-law boundary (§B), not merely an unstarted feature.
- **Any decision-support, alerting, or clinical-interpretation logic anywhere in the repository.**
  Excluded by decision, not by omission — the partograph in particular stays data-entry only, no
  alert lines, no action lines, no prolonged-labour flagging (pinned P0).

## G. Launch-1 scope

Per `docs/MVP-1.0.md` §9's explicit out-of-scope list: no full inpatient HIS, no complete
longitudinal national EHR, no pharmacy/radiology/PACS, no insurance claims or patient payments, no
consumer price marketplace, no AI diagnosis/interpretation/decision support, no nationwide rollout.
Launch 1 is the Exchange backend plus whichever native frontends are actually wired end-to-end and
deployed. ~~Today that is **none**: the Exchange backend is unqualified against a live identity
provider, and the one native frontend that exists (DHealth Clinic), while now config-complete for
authentication (§C), has never been runtime/browser-qualified (§D).~~

**[Current state, 2026-09-26, DHEALTH-STATE-001 — the struck sentence was true until 2026-09-25 and
is replaced rather than edited, because both of its clauses are now false.]**

> The native Clinic and Lab diagnostic workflow is **wired end to end and runtime/browser-qualified
> with synthetic data on the canonical Native topology**; **no production deployment and no
> real-patient pilot exists.**

Four distinct facts, and collapsing any two of them would be a false claim:

| | State |
|---|---|
| **Runtime/browser-qualified** | **yes**, for the surfaces §C's banner lists, on `docker-compose.native.yml` with real Keycloak, real PostgreSQL and a real Chromium — `BASE_DOMAIN=localhost`, Caddy's internal CA, synthetic data, one workstation |
| **Production deployed** | **no.** Nothing runs on a real domain, with a public certificate, for any real facility |
| **Human-qualified** | **no.** `INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED`; no clinician, front-desk or laboratory participant has used it (§E) |
| **Real-patient pilot** | **no**, on either track, and three of six pilot gates remain uncleared (§H) |

"The frontends are wired" is therefore an accurate statement about the **diagnostic workflow only**.
There is still no `Réglages`/facility-setup surface (§I) — DHEALTH-NATIVE-ACCESS-001 added one
narrow facility-owned administration screen (`Équipe et accès`, staff access lifecycle) and nothing
else; the patient status screen has not been rendered in a browser, and the correction/amendment
lifecycle has not been browser-exercised (§D).

## H. Pilot gates

Gates that must all clear before any real-patient pilot on the Exchange track. **Three of six are
cleared** — gate 4 on 2026-09-25, gates 1 and 2 re-evaluated from their own wording and cleared on
2026-09-26 (DHEALTH-STATE-001). **Gates 3, 5 and 6 are not cleared, and none of them was
reinterpreted to make it so.** Three cleared gates is not pilot-readiness and is not authorization
for anything; the same caveats travel with all three cleared gates — `BASE_DOMAIN=localhost`,
Caddy's internal CA, one workstation, synthetic data, no production deployment.

1. Real Keycloak qualification of token validation/authorization. ~~(currently: not run, §E). Realm/
   client reconciliation is now real-Keycloak-qualified (§C) — narrower than this gate.~~
   **CLEARED for the Native/Exchange track, 2026-09-26, DHEALTH-STATE-001**, on evidence produced by
   DHEALTH-NATIVE-MVP-1.0/1.0R and re-exercised by DHEALTH-NATIVE-DEPLOY-001 and
   DHEALTH-NATIVE-RECOVERY-001. **The load-bearing question was not "was Keycloak running" but
   "did Exchange validate and authorize real Keycloak-issued credentials on its normal runtime
   path", and it did**: `docker-compose.native.yml` configures `EXCHANGE_OIDC_ISSUER`,
   `EXCHANGE_OIDC_JWKS_URL` and `EXCHANGE_OIDC_AUDIENCE` against the real `dhealth-exchange` realm,
   so `JWKSKeySource` — not the unit suite's `StaticKeySource` — fetched real signing keys and
   validated issuer, audience and signature on tokens a real browser obtained from a real login.
   Authorization decisions were then made on those identities and **refused correctly in five
   hostile cases**. There is no test bypass: no demo-conditional branch exists in any
   authentication or request-handling code — **re-grepped 2026-09-26 rather than carried forward
   from DH-DEMO-001's own claim**, since this is the clause the gate turns on. `identity_auth.py`
   and `api.py` contain no `demo` branch at all (the only case-insensitive matches are the words
   *demographic* and *demonstrably* in prose), and `ADAPTER_DEMO_ENVIRONMENT` appears in exactly one
   file, `demo_seed.py`, where it guards seeding — never authentication. Negative controls prove the suite
   can see this break — NC-NATIVE-1 (wrong expected audience → the journey fails before any workflow
   runs) and NC-NATIVE-3 (weakened tenant check → cross-tenant read answers 200, `NEG-3` red).
   **Not cleared by this:** anything about a public certificate authority, a real domain, or a
   Keycloak deployment other than this one.
2. DHealth Clinic native browser authentication + same-origin Exchange routing. ~~— config-qualified
   as of DH-CLINIC-004A, reconciliation real-Keycloak-qualified as of DH-CLINIC-004B (§C), but a real
   browser round trip is still not observed (§D/§E — DH-CLINIC-004B, blocked on full-stack boot
   capacity, §I).~~ **CLEARED for the Native/Exchange track, 2026-09-26, DHEALTH-STATE-001.**
   Observed, repeatedly and with both halves of the gate satisfied: a real Chromium at
   `https://app.<BASE_DOMAIN>` is redirected to real Keycloak, completes **Authorization Code +
   PKCE** against the public, secretless `dhealth-native-ui` client, returns through the callback to
   an authenticated workspace, and then makes every Exchange call **same-origin** — `app.` serves
   the UI and proxies `/exchange/v1/*` to `adapter:8000` in the same vhost
   (`deploy/native/Caddyfile`), so no cross-origin path is involved at all. **Wording nuance,
   recorded rather than resolved:** the gate names "DHealth Clinic", which ADR-054 has since made a
   set of sections inside one staff application. The evidence is that application, reached at
   `app.<BASE_DOMAIN>`, carrying the Clinic sections; if the gate was ever meant to require a
   separately navigable DHealth Clinic product, that product no longer exists by decision. See §I's
   `HUMAN DECISION REQUIRED` list.
3. Manual live/browser demonstration of the golden path. **NOT CLEARED** — and it is worth being
   exact about why, because the neighbouring evidence invites over-reading. **[2026-09-26,
   DHEALTH-STATE-001: this gate was re-read from its own wording and deliberately left uncleared.]**

   > **automated real-browser golden journey: QUALIFIED** (DHEALTH-NATIVE-MVP-1.0 —
   > 12 Chromium tests against real Keycloak/adapter/PostgreSQL, `docs/NATIVE-GOLDEN-JOURNEY.md`)
   > **manual live golden-path demonstration: STILL OWED**

   This gate says *manual*. Playwright driving Chromium is not a human driving the product, and
   automated execution is not evidence about a person — the same distinction DH-USABILITY-001 §40
   already draws. What has changed since this gate was written is only that the sentence "the
   product has never run in a real browser at all" is no longer true; the gate's own requirement is
   untouched. Clearing it needs a human to walk the golden path on a live stack and that to be
   recorded. It remains **separate from** human usability qualification (§E), which additionally
   requires people who genuinely do these jobs and who have not been trained on DHealth.
4. A performed backup **restore drill**, not merely a script that exists (pinned P0; see
   `PROJECT_STATE.md` for current backup status). **CLEARED for the Native/Exchange track,
   2026-09-25, DHEALTH-NATIVE-RECOVERY-001** — and cleared in the strong sense the gate was written
   to demand: a populated synthetic deployment was backed up, **every durable volume was deleted**,
   a clean deployment was rebuilt from source control, the encrypted archive was restored, tenant
   isolation was re-proved over TCP, the pre-backup staff identity signed in through real Keycloak
   in a real browser, the same order, release version and clinician review were found through the
   real product, and a new clinical workflow was completed afterwards. Seven negative controls were
   executed and reverted. Runbook: `docs/NATIVE-RECOVERY.md`; evidence:
   `PROJECT_STATE.md`'s DHEALTH-NATIVE-RECOVERY-001 entry.
   **Two things this does NOT clear, stated so the gate is not over-read.** (a) **Off-site
   durability is unexercised** — no archive has left the host, the Native image carries no uploader,
   and the production contract still *requires* off-site by default, which means an unconfigured
   production backup run ends `FAILED` with its local archive retained. If this gate is read as also
   requiring off-site recovery, it is **not** cleared. (b) The **maternity/reference-connector
   track keeps its own separate status** — its 2026-08-13 drill (`backup/restore-drill.md`) remains
   partially verified and still owes a re-run; nothing here changes that, and this entry must never
   be read as retiring it.
5. WhatsApp patient notification running against real credentials, not a mock (pinned P0, §E).
   **Not cleared.** DH-NOTIFY-001 built the transport, the dispatcher and the receipt webhook, and
   DH-NOTIFY-META-001 pointed WhatsApp straight at Meta's Cloud API and wrote the qualification
   path (§C). DH-NOTIFY-META-001R then made the callback path publicly routable, which was a
   prerequisite nobody had noticed was missing — the proxy answered 404 to the exact URL the
   runbook told operators to configure. **No real Meta endpoint has been contacted, no credential
   exists, no message has reached a handset, and the qualification workflow has never been run.**
   SMS has no provider at all and is not part of this gate.
6. Clinical lead sign-off on the order set and partograph fields (`docs/CLINICAL-SIGNOFF.md`) —
   unrelated to the Exchange track's own gates above, but binding wherever the maternity track is
   touched. Pilot-ready is never clinically live without it.

No real-patient pilot exists on either track as of this status.

## I. Next ticket

**[Rewritten head, 2026-09-26, DHEALTH-STATE-001. The paragraphs below this block are preserved as
each ticket's own brief; several of them are superseded and say so. Read this block for the current
queue.]**

### The current queue — three genuinely parallel tracks

The repository no longer has one "next thing". It has three, and which runs first is Liban's call,
not an agent's. **Nothing below is authorization to start implementing a feature** — the same rule
DH-USABILITY-001 §43 and the 2026-09-24 product-definition brief already set stands unchanged.

| Track | Next work | State |
|---|---|---|
| **Native product** | ~~**Clinic + Lab switch-ready operating-product specification**~~ — **DONE 2026-09-27 as `DHEALTH-NATIVE-OPERATING-PRODUCT-001`**. ~~Its first implementation ticket, `DHEALTH-CLINIC-VISIT-001`~~ — **DONE 2026-09-27** (ADR-065, migration `0034_clinic_visits`, `Visites`). The next Native product work is ticket 2 of that specification's §35 sequence, and **is not yet authorized to start** | **specification done; visit lifecycle built and qualified; human usability not claimed** |
| **Native critical path** | **Native security qualification** (`DH-SEC-001` in the DH-DEMO-001 forward sequence — a name in a sequence list, **not a scoped ticket**; nothing in this repository defines its content). The valid next engineering step now that deployment and recovery are qualified | **not scoped** |
| **Connect** | **`DH-CONNECT-ADAPTER-001` — first named real partner/vendor adapter.** Defined, unstarted, and **blocked on having an actual external EMR/LIS target to integrate against** — the blocker is a counterparty, not code. **One design prerequisite was cleared 2026-09-27: ADR-064 fixes what "delivered" means and gives the adapter an explicit rule** (its responsibility ends at the Connect delivery contract's acknowledgement; it never writes `ResultDelivery` or `DoctorReview`) | **blocked on a partner system; no longer blocked on delivery semantics** |

~~**The Native product ticket has no free name, and that is a real problem rather than a formatting
one.**~~ **[Resolved 2026-09-27.]** `DHEALTH-NATIVE-MVP-1.0` was queued on 2026-09-24 as the
product-definition ticket described immediately below. On 2026-09-25 a **different** piece of work —
the real-browser golden-journey qualification — shipped under that same identifier (commit
`eccb543`), and neither entry acknowledged the collision. **The completed qualification keeps that
name; the specification is `DHEALTH-NATIVE-OPERATING-PRODUCT-001` and is now done.**

**What that specification found, in one line each** (`docs/NATIVE-OPERATING-PRODUCT.md`):

- **Switch-ready** = every routine act of a working day has a supported path in DHealth, so the
  facility can stop depending on its previous software. Four necessity tests; explicitly not
  "deployed", "clinically approved", "usability qualified" or "certified".
- **Six P0 switch blockers**, each tied to a concrete operating failure: Visits/encounters; arrival +
  waiting list; invoice/payment/receipt; an honest `BLOCKED` state on an accepted lab order;
  patient-directory import; facility-owned services and prices.
- **The hardest constraint is not a feature.** `require_clinician` guards the patient directory
  itself, so **Front Desk, Lab Reception and Cashier have no backend authority and cannot be UI
  personas** — one `BACKEND AUTHORITY DECISION REQUIRED` (D-1). The first implementation ticket is
  deliberately clinician-only so it is not blocked by it.
- **`ResultDelivery` is evaluated:** two different things in this repository are called "delivery";
  the Exchange one has **no production caller** and stays `PENDING` forever for Native orders. Two
  concrete options, `HUMAN DECISION REQUIRED` (D-7), to be settled **before** a real Connect adapter
  builds against it.
- **Seven follow-up tickets**, of which the first (`DHEALTH-CLINIC-VISIT-001`) is **DONE 2026-09-27**; six remain, none authorized to start.

**What the Native product specification must not lose.** The future clinic/lab operating product
stays **organization-scoped, role-scoped and least-privilege**, exactly as the backend already
enforces it: organization boundaries are enforced server-side; roles and capabilities determine
actions; **UI visibility follows authority but is never the security boundary**; a platform
administrator does not thereby get clinical-record access and no organization is fabricated for
one; `ORG_ADMIN` does not imply clinician; and `LAB_RESULT_RELEASER` stays a distinct capability
from `LAB_TECH` (ADR-044) — the golden journey observed that separation in a real browser, and a
specification must not flatten it. **No staff user sees every module**: navigation derives from the
active membership and the roles held in it (ADR-054). None of this is a new role taxonomy, and this
ticket did not invent one. **The specification preserved every one of these** — §7, §26 and §37 of
`docs/NATIVE-OPERATING-PRODUCT.md`.

**What the Native product specification must not lose.** The future clinic/lab operating product
stays **organization-scoped, role-scoped and least-privilege**, exactly as the backend already
enforces it: organization boundaries are enforced server-side; roles and capabilities determine
actions; **UI visibility follows authority but is never the security boundary**; a platform
administrator does not thereby get clinical-record access and no organization is fabricated for
one; `ORG_ADMIN` does not imply clinician; and `LAB_RESULT_RELEASER` stays a distinct capability
from `LAB_TECH` (ADR-044) — the golden journey observed that separation in a real browser, and a
specification must not flatten it. **No staff user sees every module**: navigation derives from the
active membership and the roles held in it (ADR-054). None of this is a new role taxonomy, and this
ticket did not invent one.

### `HUMAN DECISION REQUIRED` — raised 2026-09-26 by DHEALTH-STATE-001, not decided here

1. ~~**The `DHEALTH-NATIVE-MVP-1.0` name collision.** One identifier now denotes two different pieces
   of work: a product-definition ticket queued 2026-09-24 (never done) and the golden-journey
   qualification shipped 2026-09-25 (done, `eccb543`). Which keeps the name, and what the other is
   called, is a decision for Liban. Until then the specification work is referred to by
   description.~~
   **RESOLVED 2026-09-27 by DHEALTH-NATIVE-OPERATING-PRODUCT-001.** The completed golden-journey
   qualification **keeps** `DHEALTH-NATIVE-MVP-1.0`; the product-definition work is
   **`DHEALTH-NATIVE-OPERATING-PRODUCT-001`** and is now done — `docs/NATIVE-OPERATING-PRODUCT.md`.
   No history was rewritten: the queued brief in `PROJECT_STATE.md` keeps its original heading and
   carries a dated note assigning the new identifier.
2. **Pilot gate 2's wording.** It names "DHealth Clinic native browser authentication". ADR-054 made
   Clinic a set of sections inside one staff application, so a separately navigable DHealth Clinic
   no longer exists. The gate was cleared against the staff application at `app.<BASE_DOMAIN>`
   (§H gate 2). If the gate was meant to require something else, it needs re-wording — **and a
   docs-reconciliation ticket is the wrong place to re-word a pilot gate**, which is why it is
   raised rather than rewritten.

A third item, narrower, is raised in `docs/MVP-1.0.md` §8: whether that table's per-row
qualification cells should now be upgraded to `RUNTIME-QUALIFIED` under the section's own
definition. It is flagged there, not decided.

### `HUMAN DECISION REQUIRED` — raised 2026-09-26 by DHEALTH-NATIVE-SEC-001, not decided here

4. **Should successful sensitive *reads* be audited?** Mutations are audited transactionally
   (`exchange/security_audit.py`) and authorization refusals best-effort
   (`exchange/audit.py`), but a clinician opening a patient record, viewing a released result, or
   an admin reading a facility configuration is recorded **nowhere** — `SecurityEventType` has no
   read event at all, and no ADR says whether that is policy or omission. Implementing it would
   change retention, storage volume and privacy posture, and the line that matters — a meaningful
   record access versus a queue polling every thirty seconds — is not drawn anywhere in this
   repository. Raised deliberately rather than smuggled into a qualification ticket.
5. **What should the clinic's session and idle timeouts be?** The realm currently carries a 300s
   access token, a 1800s idle timeout and a 14400s maximum session. Those are Keycloak-shaped
   defaults inherited from DH-EX-008, not a decision about how long a shared workstation in a busy
   clinic should stay signed in. DHEALTH-NATIVE-SEC-001 qualified the *behaviour* (expiry, refresh
   failure, logout) and deliberately did not choose the *policy*. Related and also undecided: the
   realm has **no password policy** configured and no MFA; brute-force protection is on and is the
   current compensating control.

Two P2 findings from the same ticket are **not** decisions — they are recorded gaps with tests
pinning current behaviour, and either could be closed by an ordinary follow-up ticket: staff
clinical API responses carry no cache directive (G2), and `app.<domain>`'s SPA catch-all answers
200 HTML to API-shaped paths (G1). One P1 — **authority can be granted and never withdrawn through
the product** — is a missing capability rather than a broken guard, and is described in §A and
`docs/NATIVE-SECURITY-QUALIFICATION.md` §5.

---

**NEXT: `DHEALTH-NATIVE-MVP-1.0` — Clinic + Lab Operating Product Specification.** Queued
2026-09-24 after DH-USABILITY-001. **[Still owed — see the queue above; this identifier was
subsequently reused by a different, completed ticket.]** It is a **product-definition** ticket: *"Do NOT implement the
full native MVP yet unless explicitly instructed."* The hypothesis it carries in is four roles
(Front Desk, Clinician, Lab Staff, Admin — with backend safety capabilities such as result release
kept separate where needed, but never surfaced as a large role taxonomy), a clinic of
`Today / Calendar / Patients / Visits / Orders / Results / Billing`, and a laboratory of
`Today / Reception / Orders / Samples / Results / Release / Billing`. Those section names are
**things to test with real participants, not a backlog**: Calendar, Billing, Visits, Samples and
Reception must not be started inside it, and DH-USABILITY-001's product-gap list stays empty until
participants fill it. The law it is judged against is *ask only for information DHealth actually
needs, derive everything else*, with four named failure modes: large required forms, duplicate
entry, unnecessary role fragmentation, and internal architecture exposed to users. Full shape:
`PROJECT_STATE.md`'s ▶ NEXT block.

**`DH-CONNECT-ADAPTER-001` is not cancelled** — it is the next ticket on the *Connect* track, and
the paragraph below still describes it accurately. Which of the two runs first is a human decision
and is not made here: Connect is about reaching an external EMR/LIS, Native is about what DHealth
itself is to the people using it.

**DH-NOTIFY-001 and DH-NOTIFY-META-001 both landed 2026-09-24 (§A) and neither changed what
comes next.** The first closed the push-notification transport gap on the Exchange track; the
second replaced its provider with a direct Meta integration. Both are orthogonal to Connect.

**DH-NOTIFY-001 landed 2026-09-24 (§A) and did not change what comes next.** It closed the
push-notification transport gap on the Exchange track, which is orthogonal to Connect; the
next ticket is unchanged.

**NEXT: DH-CONNECT-ADAPTER-001 — First Real Partner / Vendor Adapter.**
DH-CONNECT-AGENT-001 built the runtime and deliberately no adapter. Everything vendor-specific sits
behind one three-method protocol — `handle_order`, `poll_events`, `probe` — and the only
implementation that exists is synthetic and contacts nothing. That is why a Connect facility can now
hold a genuinely qualified connector with a healthy, reporting agent and still be refused production
routing: there is nothing real at the far end.

The next ticket writes the first one against a named partner system, and it is the first work in this
repository that would actually contact an external EMR or LIS. Everything it needs already exists and
must not be rebuilt: the canonical message set, the durable queue, the identity boundary, the
correlation model, the retry and dead-letter semantics, and the qualification suite that will judge
it. What it adds is a translation layer and the operational questions that come with a real
counterparty — credentials, mapping revisions, the partner's own release signal, and whether their
identifiers round-trip at all. The `READY_FOR_ROUTING` refusal is what keeps an unqualified adapter
out of production in the meantime, and
`test_RQ8_qualified_is_still_not_production_routable` pins it.

**[Superseded 2026-09-23, DH-CONNECT-AGENT-001 — the paragraph below was the next ticket before the
runtime existed.]**

**NEXT: DH-CONNECT-CONTRACT-001 — External EMR/LIS Integration Contract + Qualification Model.**
DH-MVP-ONBOARD-001 made it possible to enrol a facility, record that it keeps its own EMR or LIS,
and capture what that software is and which interfaces it reports. What it deliberately could not
do is connect any of them: no DHealth connector exists for any external system, so a Connect
facility can be fully enrolled, fully discovered, hold a verified interface, and still be unable to
receive one production order. The onboarding UI says exactly that and refuses to say more.

The next ticket defines the one canonical contract every external connector must satisfy before it
may carry production work: connector identity and facility binding, authentication, patient and
practitioner correlation, order delivery and acknowledgement, collection and processing events,
result observations, source timestamps kept distinct from DHealth received and recorded times,
professional release, rectificatifs, external report identifiers, idempotency, duplicate
suppression, retry, outage recovery, dead-letter behaviour, connector health, credential rotation,
mapping versioning, suspension, and qualification. Only once that contract is accepted should
`DH-CONNECT-AGENT-001` and specific adapters be built. The readiness gate this ticket installed is
what will keep an unqualified connector out of production in the meantime.

**[Superseded 2026-09-21, DH-MVP-ONBOARD-001 — the paragraph below proposed a facility-setup ticket
and is preserved as the brief it was written as; that work is now done, under a different name.]**

~~**NEXT: DH-MVP-ADMIN-001 — Facility Setup + Administration.**~~ DH-MVP-POLISH-001 made the native
surfaces one authenticated staff application whose visible workflow derives from the active
organization and the user's roles in it (ADR-054, `docs/ARCHITECTURE.md` §20). That exposes the next
gap plainly: **nothing in the product can create or administer any of the facts it now depends on.**
An organization, a membership, a role grant, a provider-lab route, a lab offering, a result template
— every one is created today by a platform administrator through the API or a seeding script, and the
staff application has no `Réglages` navigation item at all, deliberately, because there is no screen
to put behind it. The next ticket should build that: organization profile, member and role
administration within a facility, provider-lab route visibility, and whatever offering/template
administration the laboratory side needs, each gated on the authority the backend already enforces
(`ORG_ADMIN` is a real role in the enum and currently has **no** frontend at all). It should not
re-open the workflow architecture, and it should not invent a role, a permission, or an
administrative concept the backend does not already model — if a screen needs one, that is a backend
decision to raise, not a frontend assumption to make.

**What the MVP still must not be claimed to do**, regardless of the above: no SMS/WhatsApp
notification, no detailed patient result delivery, no national MPI, no full EMR, no full LIS, no
Connect integration, no clinical decision support. And "functionally complete" is not
"runtime-proven": see §A and §D for what has and has not been observed running. DH-MVP-POLISH-001
adds a further honest limit of its own — **no part of the new design has been seen in a real
browser**, so every claim about it is about intent and about behaviour under jsdom.

**[Superseded 2026-09-20, DH-MVP-POLISH-001 — the paragraph that stood here proposed
DH-MVP-POLISH-001 itself and is preserved below as the brief it was written as, since the ticket it
describes is now done and its scope is worth comparing against what actually landed.]**

~~**NEXT: DH-MVP-POLISH-001 — Unified Product Polish + Operational UX.**~~ With DH-PATIENT-001 the
native MVP's functional layer is complete end to end: a clinic identifies/registers a patient,
orders diagnostics, assigns a configured laboratory; the lab receives, accepts, records collection
and processing, enters controlled results, and releases them under separate authorization;
corrections and amendments are versioned immutably; the clinician reviews an exact version and
records a follow-up disposition; and the patient can now reach a simple status/follow-up view
through an order-scoped QR/link or a staff-assisted printed handoff. The next ticket should stop
adding major clinical workflow architecture and instead make DHealth Clinic, DHealth Lab, and
DHealth Patient feel like one coherent product — navigation consistency, visual hierarchy, home
dashboards, empty/loading/error states, responsive layout, French wording consistency, status
presentation, forms, confirmation patterns, branding, accessibility, workflow speed, first-use
clarity, and support/error recovery. Administrative onboarding UX, security hardening,
operations/monitoring, backup/restore qualification, DHealth Connect, and any marketing site are
each separate later evaluations, explicitly **not** part of that ticket. **[Delivered, with two
deliberate deviations recorded in ADR-054: "home dashboards" became one `Aujourd'hui` section rather
than a dashboard per product, and "DHealth Clinic / DHealth Lab / DHealth Patient feel like one
product" was achieved by making Clinic and Lab stop being separate applications at all rather than by
styling two applications alike.]**

**DH-CLINIC-004B remains open — incomplete, not closed.** Realm/client reconciliation is now
real-Keycloak-runtime-qualified (§C), and a genuine deployment-breaking defect (VARCHAR(255) realm
import crash) was found and fixed along the way. The browser E2E suite (12 scenarios + 4 negative
controls) is written, typechecked, and reviewed, but has never executed against a live browser: the
one qualification run attempted stalled because `openmrs-backend`/`openelis-webapp` never reached
healthy within CI's wait window, blocking Caddy. Needed to close this ticket: a CI runner with more
capacity (self-hosted or a larger GitHub-hosted runner) — not a different test design — then a
successful Case A run through the full Playwright suite, then Case B (upgrade path), then the
idempotent-repeat check. See `PROJECT_STATE.md`'s DH-CLINIC-004B entry for full evidence.

**[Dated note, 2026-09-26, DHEALTH-STATE-001 — its purpose is served; its criteria are not; it is
not closed here.]** The *question* DH-CLINIC-004B existed to answer — does a real browser complete
a real OIDC round trip against this product, and does same-origin Exchange routing work — is now
answered affirmatively by the Native qualification, and pilot gates 1 and 2 clear on that evidence
(§H). But that evidence comes from a **different suite on a different topology**: `ui/e2e/native/`
against `docker-compose.native.yml`, not this ticket's own `ui/e2e/` suite against the
maternity-coupled stack. DH-CLINIC-004B's own stated closure criteria — a successful Case A run
through *its* Playwright suite, then Case B's upgrade path, then the idempotent-repeat check, all
on a runner with enough capacity to boot OpenMRS/OpenELIS — remain exactly as written and
**unmet**. Whether those criteria are still worth meeting now that Native does not need that stack
is a scope decision, and therefore Liban's; this ticket does not make it. **Not closed.**

**[Added 2026-09-20, DH-MVP-POLISH-001 — part of that suite is now written against controls that no
longer exist, and this is stated rather than quietly left to fail.]** E2E-11 navigated directly to a
per-product native app and clicked between `DHealth Clinique (Natif)` and `DHealth Laboratoire
(Natif)`. Those two buttons are gone: there is one staff application and one `Ouvrir l'application
DHealth` control (ADR-054). The spec was rewritten to `page.goto('/?app=maternity')` followed by that
one click, which still exercises the invariant it was written for — the connector's `check-sso` must
stay silent — by a simpler path. **That rewrite is written, not verified**: `npm run typecheck:e2e`
passes locally (and is not part of CI), and the click target is reachable with no OpenMRS session
because the connector shell has no auth gate, but no Playwright run has executed it. Any other step
in that suite assuming two separately navigable native applications should be re-derived against
ADR-054's URL table before it is trusted. **None of this closes DH-CLINIC-004B**, which still needs
exactly what the paragraph above says it needs.

**DH-IDENTITY-001 — native patient identity/directory contract — is implemented** (§C), and did not
touch, did not runtime-qualify, and does not change DH-CLINIC-004B's own open item above in any way.
See `DECISIONS.md` ADR-051 and `PROJECT_STATE.md`'s DH-IDENTITY-001 entry for full evidence.

**DH-CLINIC-005 — wrong-patient-safe human identity UX — is now implemented** (§C), consuming that
directory to eliminate UUID fragments as the primary way humans identify patients in Orders/Results.
Same evidence-level boundary as above: identity-UX work does not touch, does not runtime-qualify,
and does not change DH-CLINIC-004B's own open item. See `DECISIONS.md` ADR-051's 2026-09-17
clarification and `PROJECT_STATE.md`'s DH-CLINIC-005 entry for full evidence.

**DH-CLINIC-006 — provider-assigned laboratory workflow — is now implemented** (§C): explicit,
platform-admin-configured provider-lab routing, closing the "which labs is a provider actually
configured to work with" gap behind DH-EX-010's own `assign_by_provider`. Same evidence-level
boundary as above: routing work does not touch, does not runtime-qualify, and does not change
DH-CLINIC-004B's own open item; it also does not claim ADR-043 Law 2 ("no double entry") is solved
— that remains DH-PILOT-TOPOLOGY-001's own future decision. See `DECISIONS.md` ADR-052 and
`PROJECT_STATE.md`'s DH-CLINIC-006 entry for full evidence.

**DH-CLINIC-007 — released-result review + follow-up UX — is now implemented** (§C): a
provider-scoped review queue (one entry per outstanding `DoctorReview` obligation, never one per
order — an order with two independently-pending amendment versions surfaces as two entries),
exact-version content loaded and validated before any review disposition, version-specific review
mutations preferred over the legacy order-only route whenever a version exists, and a truthful
follow-up-scope disclosure (`FOLLOW_UP_REQUIRED` records only that a clinician judged follow-up
necessary — no scheduling, contact, or task engine). No clinical interpretation, urgency ranking, or
new review status was added; the three existing `DoctorReview` statuses remain authoritative. No
migration was needed — the version-specific review contract already existed from DH-EX-014A/014B;
the only new backend surface is the read-only queue endpoint. Real-PostgreSQL-qualified for the one
concurrency scenario not already covered by DH-EX-014A/014B's own PG suites (identical-disposition
race provenance, PG-R2) plus reconfirmation of PG-R1/PG-R3 on the current schema. Same evidence-level
boundary as above: this work does not touch, does not runtime-qualify, and does not change
DH-CLINIC-004B's own open item; it also does not resolve `ResultDelivery`'s own separate, unrelated
state machine, which remains untouched. See `DECISIONS.md` ADR-048's 2026-09-18 clarification and
`PROJECT_STATE.md`'s DH-CLINIC-007 entry for full evidence.

**DH-LAB-001 — native DHealth Lab shell, assigned-order queue, accept/reject — is now implemented**
(§C/§D): the first frontend DHealth Lab has ever had (`ui/src/features/lab/`), built against the
existing Exchange lab-workflow backend rather than any new backend. Same evidence-level boundary as
above: this work does not touch, does not runtime-qualify, and does not change DH-CLINIC-004B's own
open item. **Corrects a documentation drift, not a product decision:** this ticket's own text
caught forward-looking sections of this file and `PROJECT_STATE.md` framing native Lab and DHealth
Connect as a fork to choose between, which ADR-043 never actually intended — see `DECISIONS.md`
ADR-043's 2026-09-18 dated clarification. Result entry, result release, LIS/Connect integration,
and demo seed data remain not built (§F). See `PROJECT_STATE.md`'s DH-LAB-001 entry for full
evidence.

**DH-LAB-002 — collection + processing workflow for native DHealth Lab — is now implemented** (§C/
§D): the second Native Lab increment, built entirely against the existing DH-EX-011 backend
(`record_collection`/`start_processing` on `LabWorkflowRepository`, both already CI/real-PostgreSQL
qualified before this ticket — no new backend workflow endpoint, table, or `LabOrderStatus` value
was added). What this ticket actually added: `ExchangeClient.recordLabOrderCollection`/
`startProcessingLabOrder`, the `LabCollectionResponse` type, the panel UI (explicit confirmation for
both actions, truthful null-collection wording, no specimen/barcode/free-text/manual-timestamp
field), each action's own distinct lost-response recovery (collection: 3-way; processing: 4-way,
including a "prerequisite not met" outcome collection/accept-reject never has), the matching
race/stale-response/lab-switch/unmount test suites (mirroring, not sharing, LAB-001's own), the
`LabOrder.items` queue N+1 fix (§C) with its own query-count proof, `LabHomePage`'s hardening to the
same tenant/generation discipline with a dedicated stale-count test, and two backend role-negative
tests (`LAB_RESULT_RELEASER`-alone denied on both actions) that DH-EX-011 could not have written
because that role did not exist until DH-EX-012. **Docker was reachable this session (via `sudo
docker` in WSL2) but pulling `postgres:16-alpine` was blocked by this sandbox's network egress to
Docker Hub's CDN** (two attempts, both timing out mid-transfer on different blob layers), so the
concurrency/race backend guarantees this ticket depends on were not re-run against real PostgreSQL
here — they are cited as DH-EX-011's own prior, still-valid evidence (the backend transition/lock
logic is unchanged; only a read-only `selectinload` was added to the queue query).
Same evidence-level boundary as every other DHealth Lab entry above: not real-Keycloak-qualified,
not real-browser-demonstrated. Result entry, release, LIS/Connect integration, and demo seed data
remain not built (§F). See `PROJECT_STATE.md`'s DH-LAB-002 entry for full evidence.

**DH-LAB-003 — controlled, LabOffering-scoped result templates + read-only entry plan — is now
implemented** (§C/§D, ADR-053): the third Native Lab increment, and the first to add new schema
since DH-LAB-001 (migration `0024_result_templates`, purely additive — new
`exchange_lab_result_templates`/`exchange_lab_result_template_observations` tables plus the usual
security-audit CHECK-constraint widening). Templates are scoped to `LabOffering`, not
`TestCatalogEntry` — two labs offering the same catalog test may report a different structure
without that being a second ordered-service identity (ADR-053 Decision 1). Immutable per version,
at most one active per offering via a `FOR UPDATE`-locked compare-and-swap mirroring
`CatalogRepository.set_price`'s own discover-and-swap discipline, org-admin-authorized like
offerings/prices, no reference-range or clinical-interpretation field anywhere in the model. The
existing low-level `LabObservation`/`record_observation` API is completely untouched — templates
are additive Native Lab configuration, never a new requirement Connect's own source-flexible
observations must satisfy (ADR-053 Decision 8). A new batched, read-only
`GET .../lab-orders/{order_id}/result-entry-plan` resolves each `IN_PROGRESS` item's active
template for the frontend's own read-only preview; a currently-assigned order's item with no
`LabOffering` row at all is treated as a structural-integrity failure, never silently rendered
(`assign_by_provider`/`select` both require one at assignment time, and offering rows are never
deleted). **The migration itself applied cleanly against a real, disposable PostgreSQL instance in
push-triggered CI** (`alembic upgrade head` succeeded 0022→0023→0024) — genuine real-Postgres
evidence for the schema. The two new PG-A1/PG-A2 concurrent-activation tests were **not executed
this session**, for the identical Docker-reachable-but-CDN-blocked reason DH-LAB-002 already
disclosed. No result-entry, no result value, no LIS/Connect code anywhere in this ticket. Same
evidence-level boundary as every other DHealth Lab entry above: not real-Keycloak-qualified, not
real-browser-demonstrated. Result entry and release remain not built (§F). See
`PROJECT_STATE.md`'s DH-LAB-003 entry for full evidence.

**DH-LAB-004 — Native Result Entry + Completion + Release — is now implemented** (§C/§D): the
fourth Native Lab increment, closing the native loop end to end with first-write exact
template-version pinning, a Native controlled batch value endpoint, required-definition-gated
completion, a least-privilege release queue, and a release-inspection fingerprint. Same
evidence-level boundary as above: does not touch, does not runtime-qualify, and does not change
DH-CLINIC-004B's own open item. See `PROJECT_STATE.md`'s DH-LAB-004 entry for full evidence.

**DH-LAB-005 — Correction + Amendment UX — is now implemented** (§C/§D): the fifth Native Lab
increment, closing the two gaps DH-LAB-004 deliberately left closed rather than build unsafely — a
Native controlled correction endpoint (pre-release and inside an amendment, browser submits only
the new value) and a complete post-release amendment lifecycle (discovery, controlled correction,
fingerprint-gated release, a least-privilege amendment release queue, lab-facing exact
per-version content). This ticket is explicitly **not** an "Edit Result" feature — every
historical observation, release version, and review remains independently reconstructable; no
value is ever mutated in place, no release version is ever collapsed, and no value set is ever
released without the releaser having actually inspected the exact fingerprint of what they are
releasing. No new migration, no new `LabOrderStatus`/amendment status. Same evidence-level
boundary as above: does not touch, does not runtime-qualify, and does not change DH-CLINIC-004B's
own open item; DH-CLINIC-004B remains separately open, unchanged. See `DECISIONS.md` ADR-045's and
ADR-048's 2026-09-18 addenda and `PROJECT_STATE.md`'s DH-LAB-005 entry for full evidence.

**DH-DEMO-001 — Resettable Visible Native DHealth Demo Environment — is implemented and its native
Compose boot is now CI-qualified** (§C/§D): a dedicated Compose project running the real Keycloak/
adapter/DHealth-UI/Caddy code paths without ever waiting on OpenMRS/OpenELIS (untouched,
preserved), a P0 reset-safety boundary independently real-PostgreSQL-qualified (three facts:
database name, a process-own env var, a marker table only the demo's own db-init script creates),
and deterministic synthetic-only baseline data — real Keycloak-backed authentication throughout, no
bypass anywhere (grepped).

**[Corrected 2026-09-20, DH-PATIENT-001 — the paragraph this replaces said "the demo has never
actually booted anywhere" and described the `native-demo-boot` CI job as "its first real attempt".
That attempt has since run and succeeded, so that text is now stale and is corrected here rather
than left standing. This is a documentation correction only; DH-PATIENT-001 changed nothing about
the demo environment.]** Precisely what is and is not qualified:

- **Real native Compose boot: qualified in CI.** Run `35390037354` at
  `a5992bd37c40f1296955f15692a84560f7c7b3f0`, job `native demo — real boot (DH-DEMO-001)`, green
  along with all 10 jobs in that run — verified directly against the GitHub API during
  DH-PATIENT-001, not carried forward from a report.
- ~~**Real Keycloak interactive login: not yet qualified.** The stack boots with real Keycloak; no
  human has completed an interactive browser login against it.~~ **[Superseded 2026-09-26,
  DHEALTH-STATE-001.]** A real browser now completes a real Keycloak Authorization Code + PKCE login
  against this stack. The **"no human"** half of the struck sentence stays true and is the whole of
  pilot gate 3: the logins are automated, and no human has driven the golden path end to end.
- ~~**Real browser golden journey: not yet qualified.** No manual or Playwright walkthrough
  exists.~~ **[Superseded 2026-09-26, DHEALTH-STATE-001.]** The **Playwright** walkthrough exists
  and is green (DHEALTH-NATIVE-MVP-1.0, 12 tests; `docs/NATIVE-GOLDEN-JOURNEY.md`). The **manual**
  walkthrough still does not — gate 3, uncleared.

This sandbox's Docker still cannot pull real image blobs from the CDN, so the boot above is CI's
evidence, not a local observation. This does not touch, does not runtime-qualify, and does not
change DH-CLINIC-004B's own open item. See `PROJECT_STATE.md`'s DH-DEMO-001 entry for the full
evidence matrix.

**Forward context only, not started:** DH-DEMO-001's own forward sequence begins with native
runtime/browser golden-journey qualification (the immediate next step — see above), then
DH-SEC-001, DH-OPS-001, DH-RECOVERY-001, DH-DEMO-E2E-001, DH-CONNECT-CONTRACT-001, and finally
DH-PILOT-TOPOLOGY-001 — last, not first, since it answers only "which deployment model fits a
specific real facility" once one exists, never "which product path DHealth permanently supports"
(ADR-043's 2026-09-18 clarification). Full order: `PROJECT_STATE.md`'s DH-LAB-003 entry.

**[Dated note, 2026-09-26, DHEALTH-STATE-001 — three items in that sequence are done under other
names, so reading it as a to-do list would repeat finished work.]** Of the sequence above:
**native runtime/browser golden-journey qualification** is **done** (DHEALTH-NATIVE-MVP-1.0/1.0R);
**`DH-RECOVERY-001`** is **done** in substance, as DHEALTH-NATIVE-RECOVERY-001 (pilot gate 4,
§H); **`DH-DEMO-E2E-001`** is **done** in substance, as the `ui/e2e/native/` suites — and
DHEALTH-NATIVE-DEPLOY-001 additionally delivered the canonical deployment this sequence never
anticipated. **`DH-CONNECT-CONTRACT-001`** is **done** (ADR-056, `docs/CONNECT-CONTRACT.md`) and was
followed by DH-CONNECT-AGENT-001. What genuinely remains from that sequence is **`DH-SEC-001`** and
**`DH-OPS-001`** — both of which are names in this list and nothing more; **neither is a scoped
ticket anywhere in this repository** — and **`DH-PILOT-TOPOLOGY-001`**, still correctly last,
because no real facility exists to fit a deployment model to. The current queue is the table at the
top of this section.

## J. Documentation precedence

This file is the **summary of record for current status** — read it first. It does not replace or
duplicate the documents below, and it is not itself an ADR:

- **`DECISIONS.md`** — the record of accepted/rejected decisions (ADRs). Append-only; corrections
  are dated inline overlays, never rewrites of history. Authoritative for *why* a decision was made.
- **`docs/MVP-1.0.md`** — the full current-vs-MVP gap table (§8) and product-family scope (§3).
  Authoritative for granular per-capability evidence level.
- **`PROJECT_STATE.md`** — the detailed, ticket-by-ticket implementation and evidence log.
  Authoritative for exactly what changed, when, and under what evidence, per ticket.
- **`CURRENT_STATE.md`** — the historical maternity/reference-connector-track inventory
  ("Assessed 2026-08-17"), plus a running Exchange-era summary note. Authoritative for the
  connector-track's own detailed history.
- **`docs/ARCHITECTURE.md`** — authoritative for *how* the system is built, not for current status.
- **`AGENTS.md`** — governs how work in this repository is done; read before any ticket.

If any of the above appears to contradict this file, treat it as a signal this file is stale and
needs its own dated correction — do not silently trust either side without checking the current
code.
