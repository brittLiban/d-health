# AGENTS.md — rules for any agent working in this repository

This is real clinical software for a birth delivery centre and its laboratory in Djibouti. It
will be used by midwives, doctors, and lab technicians on real patients. Correctness, data
safety, and auditability are requirements, not aspirations.

**Read before doing anything:** `DECISIONS.md`, then `PROJECT_STATE.md`, then
`docs/ARCHITECTURE.md`. `CURRENT_STATE.md` for an honest read of what actually works.

---

## The rule that matters most

**Never assume-and-continue on anything with clinical or scope surface.**

The usual agent default — "make a safe assumption, document it, keep going" — is **overridden in
this repository**. The test is not "is this decision hard?" It is "does this decision have
clinical or scope surface?"

**You may decide alone and continue:** log format, folder structure, code style, language
versions, retry/backoff constants, variable naming — inert technical choices.

**Always `Human Decision Required: Yes`, no exceptions:**

- Anything touching the prenatal lab panel contents or the partograph form fields
- Anything adding decision support, alerting, or clinical interpretation of any kind
- Anything changing what patient data leaves the system — WhatsApp message content, a new
  integration, or logging that might capture clinical values
- Any change to the bed-seeding delete behaviour (ADR-007)
- Any scope decision that would cut a pinned P0 item (below)
- **Anything where you notice a gap that "obviously should" be filled a particular way.** That
  instinct is the failure mode being guarded against here — it is not a green light. If you catch
  yourself reasoning "they clearly meant X" about anything clinical, stop.

When blocked by one of these: record it in `PROJECT_STATE.md` under a blocker entry (ticket,
problem, options if genuinely relevant, `Human Decision Required: Yes`) and **move to a different
unblocked ticket** rather than waiting idle or guessing.

---

## Pinned P0 items — immune to deadline pressure

Cutting these is a §5 human decision, never an agent's call to make under time pressure.

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

## Working rules

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

## Evidence discipline — how to actually finish a ticket

Distilled from repeated instances of the same failure: a check that passed for a reason
unrelated to what it claimed to verify. Apply this to every ticket, not just the one that
prompted writing it down.

**Audit before coding.** Read `AGENTS.md` → `DECISIONS.md` → `PROJECT_STATE.md` →
`docs/ARCHITECTURE.md` → `CURRENT_STATE.md`, then inspect current CI and **measure** test
counts by running the suite — never copy a count from a doc without re-running it. If the
ticket you were pointed at is already done, say so and stop; do not redo proven work to match
a preferred style.

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

## Non-negotiables from the project brief

- All patient data stays in this stack's own databases. **No third-party AI/ML service ever
  receives patient data** without Liban's explicit prior sign-off.
- No feature performs clinical diagnosis or interpretation. This system records, stores, and
  routes. If a task drifts toward interpretation, stop and flag it.
- **French is first-class**, not a localisation afterthought — Djibouti's working medical and
  administrative language.
- Do not invent clinical content. Lab panels, form fields, and medical terminology come from
  unmodified WHO/OpenMRS/OpenELIS reference defaults, marked *needs sign-off from clinical lead* —
  never presented as finished.

## Pilot-ready ≠ clinically live

**Pilot-ready:** deploys in one command, golden flow works with test patients, backups tested with
a real restore, SSO works. This is the 1 September target.

**Clinically live:** pilot-ready **plus** the clinical lead's explicit sign-off on the prenatal
order set and the partograph fields.

Nobody — human or agent — treats pilot-ready as authorization to use this on real patients. If a
ticket's completion would put real patient data through the system, **stop and flag it**. Use the
word "pilot-ready" in documentation, never "launched" or "in production". (ADR-012)
