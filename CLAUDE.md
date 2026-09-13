# Daher Health

**DHealth is an interoperable diagnostic network** (providers, laboratories, patients — native
apps or an existing EMR/LIS connector). Current product contract: `DECISIONS.md`'s **ADR-043** and
**`docs/MVP-1.0.md`**. This repo's oldest, largest body of work is real clinical software for a
birth delivery centre and its laboratory in Djibouti (OpenMRS/OpenELIS), used by midwives, doctors,
and lab technicians on real patients — preserved as the first reference connector track, not
discarded, still fully bound by every rule below wherever it's touched.

**→ Read [AGENTS.md](AGENTS.md) before doing anything.** Then `docs/MVP-1.0.md`/ADR-043 if your
ticket touches the Exchange, `DECISIONS.md` (recorded decisions — follow them, don't re-litigate),
and `PROJECT_STATE.md` (tickets, blockers, where we are). `CURRENT_STATE.md` is the honest read of
what actually works versus what is merely written.

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

## Four DHealth product laws — Exchange track (ADR-043)

1. **No forced marketplace** — day-one DHealth is a connected workflow, not price-shopping.
2. **No double entry** — integrate the existing system or use DHealth natively, never both by hand.
3. **Faster than what it replaces** — routine actions must be low-friction, validated targets not
   current claims.
4. **No digital-literacy requirement for care** — assisted-digital, non-smartphone paths always
   exist. Full detail: `docs/MVP-1.0.md` §6.

## Pinned P0 — maternity/reference-connector track — immune to deadline pressure

1. Backup **plus a performed restore drill**. "The script exists" is not done.
2. WhatsApp stays `BLOCKED`, not `DONE`, until real credentials run the flow. A mock is never done.
3. Bed seeding stays **additive-only** — never deletes a bed that may have history.
4. Partograph stays **data-entry only** — no alert lines, no action lines, no prolonged-labour
   flagging. Excluded by decision. A near deadline is not permission to add it.

## Non-negotiables

- Patient data never goes to a third-party AI/ML service.
- Real-world artifacts shown to an agent for analysis (photos, scans, printouts of a real order,
  result, chart, or similar) may never have their real content — names, DOB, identifiers,
  dossier/report numbers, practitioner names, result values, timestamps, addresses, phone numbers,
  or a reproduced image of the artifact itself — copied into anything version-controlled. Extract
  only generalized, synthetic, or architectural findings. (See `AGENTS.md`, added 2026-09-13,
  DH-FIELD-001.)
- No clinical diagnosis or interpretation. This system records and routes.
- French is first-class, not a localisation afterthought.
- Never invent clinical content — unmodified WHO/OpenMRS/OpenELIS reference defaults only, marked
  *needs sign-off from clinical lead*.
- Never commit secrets. Pin every image; never fabricate a digest you have not pulled.
- Distinguish **written** from **verified** from **demonstrated**. Report faithfully.

## Pilot-ready ≠ clinically live — maternity/reference-connector track

Target was **pilot-ready** (1 Sept 2026, now a past date — see `PROJECT_STATE.md`/`CURRENT_STATE.md`
for whether it was met). Clinically live additionally requires the clinical lead's sign-off on the
order set and partograph fields. Nobody treats pilot-ready as authorization to use this on real
patients. Say "pilot-ready", never "launched" or "in production". The Exchange track has no
equivalent pilot-ready declaration yet — no real-patient pilot exists on either track.
