# Daher Health

Real clinical software: a health information system for a birth delivery centre and its
laboratory in Djibouti. Used by midwives, doctors, and lab technicians on real patients.

**→ Read [AGENTS.md](AGENTS.md) before doing anything.** Then `DECISIONS.md` (recorded decisions —
follow them, don't re-litigate) and `PROJECT_STATE.md` (tickets, blockers, where we are).
`CURRENT_STATE.md` is the honest read of what actually works versus what is merely written.

This file exists so the rules below are in context automatically. It is a summary, not the
source — `AGENTS.md` governs.

---

## The rule that matters most

**Never assume-and-continue on anything with clinical or scope surface.** The usual "make a safe
assumption, document it, keep going" default is **overridden in this repo**.

Decide alone: log format, folder structure, code style, language versions, retry constants.

**Always stop and ask** (`Human Decision Required: Yes`, recorded in `PROJECT_STATE.md`, then move
to a different unblocked ticket):

- The prenatal lab panel contents or the partograph form fields
- Anything adding decision support, alerting, or clinical interpretation
- Anything changing what patient data leaves the system
- The bed-seeding delete behaviour
- Cutting any pinned P0 item below
- **Any gap that "obviously should" be filled a particular way** — that instinct is the failure
  mode being guarded against, not a green light

## Pinned P0 — immune to deadline pressure

1. Backup **plus a performed restore drill**. "The script exists" is not done.
2. WhatsApp stays `BLOCKED`, not `DONE`, until real credentials run the flow. A mock is never done.
3. Bed seeding stays **additive-only** — never deletes a bed that may have history.
4. Partograph stays **data-entry only** — no alert lines, no action lines, no prolonged-labour
   flagging. Excluded by decision. A near deadline is not permission to add it.

## Non-negotiables

- Patient data never goes to a third-party AI/ML service.
- No clinical diagnosis or interpretation. This system records and routes.
- French is first-class, not a localisation afterthought.
- Never invent clinical content — unmodified WHO/OpenMRS/OpenELIS reference defaults only, marked
  *needs sign-off from clinical lead*.
- Never commit secrets. Pin every image; never fabricate a digest you have not pulled.
- Distinguish **written** from **verified** from **demonstrated**. Report faithfully.

## Pilot-ready ≠ clinically live

Target is **pilot-ready** (1 Sept 2026). Clinically live additionally requires the clinical lead's
sign-off on the order set and partograph fields. Nobody treats pilot-ready as authorization to use
this on real patients. Say "pilot-ready", never "launched" or "in production".
