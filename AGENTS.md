# AGENTS.md — rules for any agent working in this repository

## DHealth today

DHealth is an interoperable diagnostic network connecting doctors/clinics, independent
laboratories, and patients — reachable through DHealth's own native applications (DHealth Clinic,
DHealth Lab, DHealth Patient — ~~not yet built~~ **[corrected 2026-09-26, DHEALTH-STATE-001: built,
and since ADR-054 they are sections of one staff application rather than three products. The Clinic
and Lab diagnostic workflow is runtime/browser-qualified with synthetic data on the canonical Native
topology; the DHealth Patient screen is not. Nothing is production-deployed, human-qualified, or
running on real patients]**) or through an existing EMR/LIS via DHealth Connect.
The authoritative, current product definition is `DECISIONS.md`'s **ADR-043** and
**`docs/MVP-1.0.md`**, including four non-negotiable product laws (no forced marketplace, no
double entry, faster than what it replaces, no digital-literacy requirement for care). Read one of
those before any ticket that touches the DHealth Exchange backend (`adapter/src/daher_adapter/
exchange/`).

## Existing maternity/OpenMRS/OpenELIS stack

This repository's oldest and largest body of work is real clinical software for a birth delivery
centre and its laboratory in Djibouti, used by midwives, doctors, and lab technicians on real
patients. **It is not discarded by the direction above.** It is the **first reference connector
track** under DHealth Connect's model (ADR-042, extended by ADR-043) — a preserved clinical-stack
implementation and reusable deployment/security/backup/interoperability evidence. Its rules below
remain fully binding wherever that track is touched, regardless of whether maternity work sits on
the Exchange's current critical path. "Off the Exchange MVP critical path" does not mean invalid,
obsolete, or safe to weaken — only an ADR can supersede one of these rules.

## Exchange safety

The Exchange handles transaction-scoped clinical data — patient/practitioner references, ordered
tests, lab results, review dispositions — and gets the identical seriousness the maternity stack
already requires around correctness, tenant isolation, privacy, auditability, and never inventing
clinical semantics. Every rule below is marked **GLOBAL** (binds both tracks) or **MATERNITY /
REFERENCE-CONNECTOR TRACK** (binds that track specifically); none of it is weakened by the product
pivot.

Correctness, data safety, and auditability are requirements, not aspirations, on either track.

**Read before doing anything:** `DECISIONS.md` (an Exchange-track ticket starts with ADR-042 and
ADR-043 specifically), then `PROJECT_STATE.md`, then `docs/ARCHITECTURE.md`. `CURRENT_STATE.md`
for an honest read of what actually works. `docs/MVP-1.0.md` for the current product contract if
your ticket touches the Exchange.

---

## The rule that matters most — GLOBAL

**Never assume-and-continue on anything with clinical or scope surface.**

The usual agent default — "make a safe assumption, document it, keep going" — is **overridden in
this repository**. The test is not "is this decision hard?" It is "does this decision have
clinical or scope surface?" This principle binds both tracks equally — an Exchange-track ticket
that touches result content, patient identity matching, or what leaves the Exchange to a patient
gets the identical stop-and-ask treatment a maternity-track ticket already gets.

**You may decide alone and continue:** log format, folder structure, code style, language
versions, retry/backoff constants, variable naming — inert technical choices.

**Always `Human Decision Required: Yes`, no exceptions:**

- Anything touching the prenatal lab panel contents or the partograph form fields — **maternity /
  reference-connector track**
- Anything adding decision support, alerting, or clinical interpretation of any kind — **GLOBAL**;
  binds the Exchange identically (ADR-043 §13/`docs/MVP-1.0.md` §13)
  **[Added 2026-09-27, DHEALTH-CLINIC-VISIT-001: a clinician-written clinical note now exists
  (`VisitNote`), and it creates three new temptations this bullet already forbids but which are worth
  naming because they will look helpful. (1) *Reading* a note for any purpose other than showing it to
  the clinician looking at that visit — summarising it, extracting a problem list, flagging a word —
  is clinical interpretation. (2) A note body must never reach an audit row, a log line, an error
  message, an analytics event, or any third-party AI/ML service; nothing in this repository may send
  one anywhere. (3) Adding a *structured* clinical field beside it — vitals with units or ranges, a
  SOAP section, an examination template, a problem or medication list, a diagnosis code (**D-5**) — is
  clinical content requiring the clinical lead's sign-off, not a schema decision. A free-text field a
  clinician chose to fill is inside the boundary; anything that shapes, reads or transmits it is not.
  Also forbidden without a decision: deriving a waiting time, priority, or triage order from visit
  timestamps — a number a clinic would act on is a clinical signal even when arithmetic produced it.]**
- Anything changing what patient data leaves the system — WhatsApp message content, a new
  integration, or logging that might capture clinical values — **GLOBAL**; on the Exchange this
  currently includes ~~`ResultDelivery`'s undecided semantics and~~ any per-channel (SMS/WhatsApp/
  voice) sensitive-content policy, ~~both~~ explicitly left open by ADR-043.
  **[Corrected 2026-09-27: `ResultDelivery`'s semantics are no longer open — ADR-064 settled them
  (deprecated / compatibility-only, owns nothing). Do not stop and ask about it; the rule that now
  applies is the opposite one — no new code may write or read it, and `docs/ARCHITECTURE.md` §29
  names the authoritative owner of each delivery-shaped fact. The per-channel sensitive-content
  policy remains genuinely open.]**
- Any change to the bed-seeding delete behaviour (ADR-007) — **maternity / reference-connector
  track**
- Any scope decision that would cut a pinned P0 item (below, maternity-track) or that would violate
  one of ADR-043's four product laws (Exchange-track) — **GLOBAL** principle, track-specific lists
- **Anything where you notice a gap that "obviously should" be filled a particular way.** That
  instinct is the failure mode being guarded against here — it is not a green light. If you catch
  yourself reasoning "they clearly meant X" about anything clinical, stop. — **GLOBAL**

The Exchange track's own catalog of open, not-yet-decided questions — the DH-EX-005 price-consent-
vs-marketplace conflict, result validation/release workflow design, proxy/family authorization,
patient-matching policy — lives in ADR-043 and `docs/MVP-1.0.md` §10 ("Known gaps") and §4
("Existing-contract conflict recorded, not resolved"); it is a live list, not exhaustively repeated
here.

When blocked by one of these: record it in `PROJECT_STATE.md` under a blocker entry (ticket,
problem, options if genuinely relevant, `Human Decision Required: Yes`) and **move to a different
unblocked ticket** rather than waiting idle or guessing.

---

## Pinned P0 items — maternity / reference-connector track — immune to deadline pressure

Cutting these is a §5 human decision, never an agent's call to make under time pressure. **These
four are specific to the maternity/reference-connector track** — off the Exchange MVP's current
critical path does not mean invalid or optional; only an ADR can cut one. The Exchange track's own
non-negotiables are the **four product laws** (ADR-043 / `docs/MVP-1.0.md` §6: no forced
marketplace, no double entry, faster than what it replaces, no digital-literacy requirement for
care) — a parallel list for the other track, not a replacement for this one.

1. **Backup + a *performed* restore drill.** Not "the script exists". A restore has to have
   actually been run and documented. Untested backups on clinical data are not done.
2. **The WhatsApp real-credential gate.** A passing mock is never reported as `DONE`. If
   1 September arrives without credentials, the status is `BLOCKED`. (ADR-003)
3. **Bed seeding stays additive-only.** Never deletes a bed that may have history attached.
   Do not "simplify" this away. (ADR-007)
4. **The partograph form stays data-entry only.** No alert lines, no action lines, no
   prolonged-labour flagging. Excluded *by decision*, not by oversight. **A near deadline is not
   implicit permission to add it "helpfully."** (ADR-006)

If time runs short, cut elsewhere.

---

## Working rules — GLOBAL

- **One ticket at a time.** No speculative work on unrelated tickets "while you're in there".
  This repo has real cross-service sequencing; parallel edits to `docker-compose.yml` and env
  files produce silent conflicts in exactly the pieces hardest to debug.
- **Follow recorded decisions.** If `DECISIONS.md` covers your ticket, apply it — do not
  re-litigate. If you believe an ADR is wrong, write a superseding ADR with reasoning and flag
  it; never quietly diverge.
- **Don't redesign the architecture silently.** If your ticket causes `docs/ARCHITECTURE.md` to
  drift from reality, update it as part of that ticket. Do not create a second architecture doc.
- **Small, reviewable changes.** Commit per logical unit.
- **Never commit secrets.** `.env` is gitignored from the first commit; `.env.example` carries
  placeholders and a comment for every variable, added the moment the variable is introduced.
- **Pin every image.** No `:latest`, ever. Where upstream publishes only a moving branch tag,
  pin the digest too. Never fabricate a digest you have not pulled — leave a `TODO` instead.
- **Preserve backward compatibility** on anything already deployed or already holding data.
- **Report faithfully.** If a step was skipped, say so. If a healthcheck is defined but never
  observed passing, it is unverified, not working. Distinguish "written" from "verified" from
  "demonstrated" — this repo has been burned by tidy-looking work that had never been run.
- **Update `PROJECT_STATE.md` before ending a session.** Continuity across sessions depends on it.

## Evidence discipline — how to actually finish a ticket — GLOBAL

Distilled from repeated instances of the same failure: a check that passed for a reason
unrelated to what it claimed to verify. Apply this to every ticket, not just the one that
prompted writing it down.

**Audit before coding.** Read `AGENTS.md` → `DECISIONS.md` → `PROJECT_STATE.md` →
`docs/ARCHITECTURE.md` → `CURRENT_STATE.md`, then inspect current CI and **measure** test
counts by running the suite — never copy a count from a doc without re-running it. If the
ticket you were pointed at is already done, say so and stop; do not redo proven work to match
a preferred style. **Current code and a fresh `git fetch`/CI check always win over an older
document's own snapshot when the two disagree** — a doc's own "audited against SHA X" line, a
named line number, or a status table entry is a claim about a moment in time, not a promise it
still holds. Re-verify a prompt-carried starting SHA against `origin/master` yourself before
trusting it (this repeated so often across tickets, informally, that it is written down here
once rather than re-derived every time — added 2026-09-13, DH-FIELD-001).

**Test matrix, not just the happy path.** For any check whose claim matters (a health
verdict, a security refusal, a persistence guarantee), also write the adjacent cases that
would silently make the claim false: the input that looks successful but isn't (exit 0 with
a failed component inside), the missing/malformed/stale case, the boundary a nearby rule
carves out (a one-shot job that's supposed to exit and stop, an optional profile that's
supposed to be absent).

**Negative controls are mandatory, not optional polish.** After the tests pass, deliberately
break the property each one claims to guard — comment out the check, invert the condition,
restore the old behavior on a throwaway copy — and confirm the *specific* test goes red for
the *specific* reason. Record what you broke and which tests failed, with real counts. Never
write "control-tested" without having actually run one. A test that would pass regardless of
whether the code is right proves nothing and should be strengthened or deleted.

**Precision in what you claim.** Say what was actually observed, not the generalization a
reader would make from it: "the latest check at 10:42:16 found every required service in the
expected state," not "the facility is healthy." "Runs on demand," not "continuous
monitoring." "Compensation on failure," not "transactional." If a claim needs a qualifier to
stay true, keep the qualifier.

**Before committing:** run the relevant tests, lint, and format check locally; review the
diff for secrets, clinical data, unrelated files, and scope creep; confirm the commit is one
logical unit. Full CI cannot run before a push-triggered commit — say so rather than implying
it did, and verify against the real CI run afterward.

**When a fix exposes a second bug**, close it in the same change if it's a direct, mechanical
consequence of the fix (not a pre-existing, unrelated gap) — but if it touches a different
ticket's already-shipped, tested surface (a lifecycle table, a security boundary), say so and
get confirmation before touching it rather than deciding alone.

**On handoff, state plainly what was NOT proven** — not exercised against a real boot, not
verified through two real hosts, not measured against production data volume — rather than
letting a passing test suite imply more than it showed.

## Non-negotiables from the project brief — GLOBAL

- All patient data stays in this stack's own databases. **No third-party AI/ML service ever
  receives patient data** without Liban's explicit prior sign-off.
- **Real-world artifacts shown to an agent for analysis (photos, scans, printouts of a real order,
  result, chart, or similar) may never have their real content — names, DOB, identifiers, dossier/
  report numbers, practitioner names, result values, timestamps, addresses, phone numbers, or a
  reproduced image of the artifact itself — copied into anything version-controlled: docs, tests,
  fixtures, examples, or commit messages. Extract only generalized, synthetic, or architectural
  findings.** Distinct from the rule above (which governs the *running system's* data flow): this
  governs an agent's own conduct when handed real evidence during a ticket. (Added 2026-09-13,
  DH-FIELD-001 — see `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` for the pattern applied.)
- No feature performs clinical diagnosis or interpretation. This system records, stores, and
  routes. If a task drifts toward interpretation, stop and flag it. (Restated for the Exchange
  specifically in ADR-043 / `docs/MVP-1.0.md` §13.)
- **French is first-class**, not a localisation afterthought — Djibouti's working medical and
  administrative language. (`docs/MVP-1.0.md` §7 extends this to the Exchange's patient-facing
  language direction: French-first professional UX now, with future capability — not current
  implementation — for Somali/Afar/Arabic on the patient side.)
- Do not invent clinical content. On the maternity/reference-connector track: lab panels, form
  fields, and medical terminology come from unmodified WHO/OpenMRS/OpenELIS reference defaults,
  marked *needs sign-off from clinical lead* — never presented as finished. On the Exchange track:
  the pilot-phase test catalog is synthetic-only by construction (DH-EX-003/DH-EX-004 reject any
  non-synthetic catalog entry) — the equivalent guard against inventing real clinical content,
  enforced in code rather than only in review.

## Pilot-ready ≠ clinically live — maternity / reference-connector track

**Pilot-ready:** deploys in one command, golden flow works with test patients, backups tested with
a real restore, SSO works. **This was the 1 September 2026 target — now a past, historical date**,
recorded as a decision-tracking milestone for the maternity/reference-connector track; see
`PROJECT_STATE.md`/`CURRENT_STATE.md` for whether it was met. No new date is set by this file.

**Clinically live:** pilot-ready **plus** the clinical lead's explicit sign-off on the prenatal
order set and the partograph fields.

Nobody — human or agent — treats pilot-ready as authorization to use this on real patients. If a
ticket's completion would put real patient data through the system, **stop and flag it**. Use the
word "pilot-ready" in documentation, never "launched" or "in production". (ADR-012)

**The Exchange track has no equivalent "pilot-ready" declaration yet.** `docs/MVP-1.0.md` §11
records a *proposed*, not secured, pilot shape; ~~§8 states plainly that no manual/browser
demonstration, real Keycloak qualification, production deployment, or real-patient pilot exists for
the Exchange.~~ **[Corrected 2026-09-26, DHEALTH-STATE-001 — two of those four moved and two did
not; the sentence bundled them, so it is replaced rather than trimmed.]** As of 2026-09-26:
**real Keycloak qualification exists** and an **automated real-browser golden journey exists** on
the canonical Native topology (pilot gates 1 and 2, `STATUS.md` §H); a **manual** live/browser
demonstration does **not** (gate 3, still owed), and **no production deployment and no real-patient
pilot exist** on either track. The Exchange track still has no "pilot-ready" declaration — three of
six pilot gates are uncleared. Treat "CI-qualified", "runtime/browser-qualified", "human-qualified"
and "pilot-ready" as four different claims — a green CI run, and equally a green Playwright run, is
not a pilot-readiness declaration on either track.

<!-- BEGIN CODEX DHEALTH ORCHESTRATION -->
## Codex DHealth orchestration

These instructions govern the PRIMARY Codex agent. For every non-trivial DHealth implementation
ticket, the primary agent must automatically use the project-scoped `dhealth_advisor` custom agent
as an independent reviewer twice: once before implementation and once after implementation and
testing. A non-trivial ticket includes any ticket that changes runtime behavior, domain or database
models, schema or migrations, authorization or tenant boundaries, patient-data handling, clinical
or laboratory workflow, API contracts, interoperability, or materially changes the tests or
documentation that assert those behaviors. Both advisor reviews remain part of the current ticket
and do not authorize work on an adjacent ticket.

### PHASE 1 — AUDIT

- Inspect current git HEAD and the working tree before making changes.
- Read the relevant repository instructions, ADRs, `PROJECT_STATE.md` entries, architecture docs,
  implementation, migrations, and tests.
- Verify that the ticket still matches current repository reality; current code and fresh evidence
  take precedence over stale summaries.
- Reproduce the issue first when applicable.

### PHASE 2 — PLAN

- Create a concrete implementation plan scoped only to the current ticket.
- Identify assumptions and unresolved decisions explicitly. Do not silently resolve anything with
  clinical or scope surface.

### PHASE 3 — INDEPENDENT ADVISOR REVIEW

- Automatically spawn the `dhealth_advisor` custom agent.
- Give it the ticket and enough context to independently inspect the repository, including the
  proposed plan and the relevant scope, but do not merely give it the primary agent's conclusions.
- Ask it to attack the proposed plan and find real architecture, database, security, workflow,
  migration, compatibility, evidence, and test risks.
- Wait for the advisor to finish before proceeding.

### PHASE 4 — RECONCILE

- Evaluate every advisor finding against repository evidence; the advisor is independent, not
  automatically correct.
- Fix or revise the plan for every valid finding and record the disposition of every finding.
- If any finding is `HUMAN DECISION REQUIRED`, STOP and ask Liban. The primary agent must not make
  that decision itself.
- If no human decision is required, continue automatically.

### PHASE 5 — IMPLEMENT

- Implement only the current ticket. Do not start adjacent tickets.
- Preserve all repository invariants and existing ADR decisions.
- Do not invent clinical semantics or fabricate historical data or provenance.
- Perform appropriate migrations, tests, real-PostgreSQL qualification, concurrency checks, and
  negative controls wherever relevant to the ticket and its claims.

### PHASE 6 — FINAL INDEPENDENT REVIEW

- After implementation and tests, automatically spawn `dhealth_advisor` again.
- Require it to independently inspect the actual diff, migrations, implementation, tests,
  documentation, and execution evidence.
- Do not let it rely only on the primary agent's final report or summary.

### PHASE 7 — FIX AND VERIFY

- Address every valid `BLOCKER` and `SHOULD FIX` finding within the current ticket.
- Rerun all affected tests, qualification, concurrency checks, and negative controls.
- If addressing a finding materially changes the architecture or plan, have `dhealth_advisor`
  re-review the changed design or implementation.
- A `HUMAN DECISION REQUIRED` finding still stops work for Liban; the primary agent must not infer
  the answer.

### PHASE 8 — FINAL REPORT

- Report the exact HEAD and commit where applicable.
- Report every file changed and every decision made.
- Report the tests and qualification actually executed, including exact negative controls actually
  executed.
- Report known remaining gaps and all advisor findings with their dispositions.
- Clearly distinguish VERIFIED facts from assumptions and state what was not proven.
<!-- END CODEX DHEALTH ORCHESTRATION -->
