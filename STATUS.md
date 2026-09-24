# STATUS — Daher Health

**This is the canonical, present-tense status document for this repository.** It is short by
design and does not replace the detailed historical records it summarizes. Where this file and any
other document disagree, see §J (Documentation precedence) before assuming either is wrong.

## A. As-of

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
and there is no control whose label implies a removal the server would refuse. Revocation,
invitations and Keycloak provisioning remain out of scope and unbuilt.

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
  a real database. **Still not observed: a real browser completing the redirect/login/PKCE round
  trip.** The qualification run's full-stack boot stalled on the pre-existing, unmodified
  `openmrs-backend`/`openelis-webapp` services never reaching healthy within CI's wait window, which
  blocks Caddy and therefore every `app.${BASE_DOMAIN}` vhost the browser suite needs — consistent
  with this repo's own documented ~50-minute-plus full-stack cold boot (`.github/workflows/ci.yml`
  header), not a defect in this ADR's own code. See `DECISIONS.md` ADR-050's 2026-09-17 overlay and
  `PROJECT_STATE.md`'s DH-CLINIC-004B entry.
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
  Keycloak import of the two-client file and real reconciliation against it (§C) — but no browser has
  actually completed a redirect/login/PKCE round trip, and no live Caddy routing has been exercised
  end to end, because the full stack (Caddy included) never finished booting in the one qualification
  run attempted. That gap is DH-CLINIC-004B's own open item (§I), not closed by this entry.
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
  version's exact, immutable content on demand. Built and component/UI-tested only — **same "built
  but not runtime-proven" boundary as DHealth Clinic above**: no real-Keycloak or real-browser
  qualification exists for it either. LIS/Connect integration remains explicitly **not** part of
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
  DHealth Lab above**: no real browser has rendered this screen, no real phone has scanned one of
  these QR codes, and no printer has produced one of these sheets. Responsive layout was written
  mobile-first and reasoned about at 320/375/430px but **not measured** — jsdom cannot measure
  layout. No SMS/WhatsApp/email/voice transport was added; `ResultDelivery` is untouched and
  viewing a status page deliberately does **not** mark it `DELIVERED`. See `docs/ARCHITECTURE.md`
  §19 and `PROJECT_STATE.md`'s DH-PATIENT-001 entry.
- **Facility enrollment and integration readiness** (`adapter/.../facility_enrollment.py`,
  `ui/src/features/platform/`, DH-MVP-ONBOARD-001) — the control plane for joining the network.
  A platform operator enrols an organization and its enrollment in one transaction, attaches a first
  administrator found through a bounded platform-only identity lookup — or attaches people later
  from the facility's **Équipe et accès** section, which grants access and never removes it —
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
  only — **same "built but not runtime-proven" boundary as every native surface above**: no real
  browser has rendered this shell, no real person has used the establishment selector, no E2E has run,
  and no font has been observed loading. See `DECISIONS.md` ADR-054, `docs/ARCHITECTURE.md` §20,
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
  forward from a report). **Real Keycloak interactive login: not yet qualified. Real browser golden
  journey: not yet qualified.** This sandbox's Docker still cannot pull real image blobs from the
  CDN, so that boot is CI's evidence, not a local observation. See `PROJECT_STATE.md`'s
  DH-DEMO-001 entry for the full evidence matrix.
- Everything in §C's Exchange-backend list beyond CI/PostgreSQL qualification: no real-Keycloak
  qualification, no manual live/browser demonstration, no production deployment, for any Exchange
  ticket.

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
- **`ResultDelivery` semantics**: created `PENDING` at order creation and no code path advances it
  past `PENDING`. Unchanged by DH-NOTIFY-001, deliberately: a patient's handset accepting an SMS is
  not the ordering provider organization receiving a result, and what `DELIVERED` should mean there
  remains an open product decision (`docs/MVP-1.0.md` §10 item 6).
- **Email and voice patient notification**: not built, and `§28` non-goals rather than gaps.
- **Real Keycloak qualification of authorization boundaries**: still not run for token
  validation/audience/authorization decisions — every such boundary is proven only against a locally
  generated RSA keypair (`StaticKeySource`), never a live identity provider. (Narrower than before:
  DH-CLINIC-004B did runtime-qualify realm/client *reconciliation* against a real Keycloak — §C — but
  that is a deployment-provisioning proof, not a token-validation one.)
- **Real-browser qualification of native Clinic authentication**: the config-level pieces exist
  (§C), and realm reconciliation is now real-Keycloak-qualified (§C), but a real OIDC round trip
  against a live browser/Caddy stack has never been observed — the one qualification run attempted
  stalled before Caddy ever started (§D). This is DH-CLINIC-004B's open item (§I).
- **Full-stack (Caddy-included) boot on a shared GitHub-hosted CI runner**: `openmrs-backend` and
  `openelis-webapp` did not reach Docker `healthy` within a 3600s wait window in the one attempt made
  (DH-CLINIC-004B, run `35210377720`). Consistent with this repo's own documented ~50-minute-plus
  cold boot being a manual/fresh-VM procedure, not a shared-runner one — see `.github/workflows/ci.yml`'s
  header. Not a code defect found in this attempt; next step is a runner with more capacity
  (self-hosted or a larger GitHub runner), not a different test design.
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
- **DHealth Connect** (EMR/LIS integration layer, beyond the OpenMRS/OpenELIS reference track
  itself): no code of its own exists yet. **[Updated 2026-09-20, DH-PATIENT-001 — this bullet
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
deployed — today that is **none**: the Exchange backend is unqualified against a live identity
provider, and the one native frontend that exists (DHealth Clinic), while now config-complete for
authentication (§C), has never been runtime/browser-qualified (§D).

## H. Pilot gates

Gates that must all clear before any real-patient pilot on the Exchange track, none of which have
cleared yet:

1. Real Keycloak qualification of token validation/authorization (currently: not run, §E). Realm/
   client reconciliation is now real-Keycloak-qualified (§C) — narrower than this gate.
2. DHealth Clinic native browser authentication + same-origin Exchange routing — config-qualified
   as of DH-CLINIC-004A, reconciliation real-Keycloak-qualified as of DH-CLINIC-004B (§C), but a real
   browser round trip is still not observed (§D/§E — DH-CLINIC-004B, blocked on full-stack boot
   capacity, §I).
3. Manual live/browser demonstration of the golden path (currently: not run).
4. A performed backup **restore drill**, not merely a script that exists (pinned P0; see
   `PROJECT_STATE.md` for current backup status).
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

**NEXT: `DHEALTH-NATIVE-MVP-1.0` — Clinic + Lab Operating Product Specification.** Queued
2026-09-24 after DH-USABILITY-001. It is a **product-definition** ticket: *"Do NOT implement the
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
- **Real Keycloak interactive login: not yet qualified.** The stack boots with real Keycloak; no
  human has completed an interactive browser login against it.
- **Real browser golden journey: not yet qualified.** No manual or Playwright walkthrough exists.

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
