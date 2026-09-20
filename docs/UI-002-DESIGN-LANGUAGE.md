# UI-002 — The DHealth design language

**Added 2026-09-20, DH-MVP-POLISH-001.** Companion to [UI-001-CLINICAL-UX.md](UI-001-CLINICAL-UX.md),
which covers clinical interaction rules (touch targets, glove use, confirmation patterns). This
document records *what was approved and why*, so a later ticket can tell a deliberate choice from an
accident. It does not restate the token values — `ui/src/index.css` is the single source of those, and
a scan in `ui/src/designTokens.test.ts` keeps it that way.

The design was explored as three directions and one was chosen with four corrections. Where this
document and a mockup disagree, this document and the repository's safety invariants win: the ticket
that commissioned the design said so explicitly.

## The one-sentence version

One product whose visible workflow is decided by permissions, not by which application the user
opened.

## Approved principles

1. **One product, permissions decide.** No application-switching tabs, no "DHealth Clinic" and
   "DHealth Lab" as things to choose between. The active organization plus the user's roles in it
   determine what is on screen. See ADR-054 and `docs/ARCHITECTURE.md` §20.
2. **Organization is context, not navigation.** The active establishment lives in the masthead, beside
   the person's identity, because it qualifies everything below it. It is not a destination.
3. **Render only what works.** No navigation item exists for a screen that is not built. "Bientôt"
   placeholders and non-functional buttons are worse than an absence: they teach users that this
   product's controls cannot be trusted. A real example this ticket refused: a
   `Envoyer par WhatsApp` button with no transport behind it.
4. **Colour carries workflow responsibility, never clinical meaning.** petrol = a step is complete or
   under way; brass = something is still owed; clay = stopped, refused or superseded; ink = a recorded
   fact. A haemoglobin value is never clay because it is low. A patient-facing follow-up marker is
   never clay either — beside a sentence about someone's own analysis, clay reads as alarm about the
   result, and no DHealth surface may imply an interpretation it does not have.
5. **State is never carried by colour alone.** Every state that matters is also a word, a shape, or a
   weight: the relay's stopped node is squared where the others are round, the active nav item carries
   weight and an inset edge as well as a tint, and a corrected result says "Rectificatif".
6. **Three densities, one language.** *Registre* for queues (56px rows, tabular numerals), *Focus* for
   a consequential single-subject decision (a measure, generous whitespace), *Assisté* for the patient
   surface (18px base, 56px touch targets, one column at every width). Same palette, same type, same
   spacing base.
7. **The relay shows what was recorded, not a guess.** It never names a person, because release and
   review authority are modelled as organization roles and naming an individual would be invention. A
   fact the API does not date is shown undated rather than borrowing a nearby timestamp. Six pairs are
   never collapsed: selected/accepted, entry-complete/released, released/reviewed, release/amendment,
   refused/cancelled, analysis-under-way/results-available.
8. **Self-hosted type.** No Google Fonts, no third-party font CDN at runtime: a clinic on a degraded
   link must not wait on a host it cannot reach, and the request would disclose which application is
   open and from where.

## The four corrections to the approved direction

Recorded because each overrode something the design proposed, and a later reader would otherwise see
only the outcome.

1. **The app switch dies, the tracks do not.** The unified staff shell replaces the Clinic/Lab switch,
   but the maternity/OpenMRS reference connector is preserved and stays *outside* the staff product's
   navigation (ADR-042/043). One header button leads from the connector into the staff application;
   there is deliberately no control back.
2. **"Rectificatif", with the version identity demoted rather than erased.** The word belongs in queue
   rows, where a record is scanned. The ordinal belongs in the version history, the relay's technical
   reference, and the sentence a clinician confirms — where the record is being studied, and where it
   is matched to the backend and discussed with a laboratory by telephone.
3. **Typed values may survive session expiry in memory, never in storage.** The design wanted a
   technician's typed result values to outlive a session expiry. Implementation may hold them in
   component memory while the page stays mounted — which is what a *successful* silent token refresh
   allows — but must not reach `localStorage`, `sessionStorage`, `IndexedDB`, a cookie or the URL to
   achieve it. A *failed* refresh unmounts the tree and the drafts with it; the UI states that cost
   per field instead of implying the work is safe.
4. **Two gestures for a durable disposition, and no modal.** A follow-up disposition is immutable once
   recorded, so it must not be one click — but a confirmation dialog on every review is worse: someone
   working a queue dismisses the twentieth without reading it. The chosen shape: the confirm control
   is always visible and inert until a disposition is chosen, and the sentence above it restates the
   disposition, the patient, and the version on screen, bound to the button by `aria-describedby`.

## What was explored and not chosen

Three directions were produced; the one recorded above ("one product, permissions decide") was
approved. The unchosen two are not preserved in this repository — they were static explorations, and
keeping them would invite a later reader to treat a rejected direction as an alternative still on the
table. The prototype that demonstrated the approved direction (one transaction carried end to end, an
amendment branch, and permission changes shown on one application rather than three mock ones) lives
outside this repository as a published artifact:
<https://claude.ai/artifact/QBbb4dej5mTVqFDxPvLeJw>. It is a design record, not a specification: it
was produced before implementation, and where it contradicts a repository invariant the invariant
wins.

## Looking at it

`ui/harness/` exists because this document's claims are about appearance and the test suite cannot
see appearance. It mounts the real components against stub data — no auth, no backend, no network —
so the shell, the relay and the patient screen can be looked at in a real browser with the real
stylesheets and the real self-hosted fonts.

```
npx vite                         # then open /harness/index.html?scene=shell-lab-dual
node harness/shoot.mjs           # or capture every scene at 1280px and 390px into harness/shots/
```

`shoot.mjs` is not only a camera. It fails loudly on a console error, a failed request, horizontal
overflow, a navigation item sitting outside its container, and a navigation row taller than a quarter
of a phone screen — the last two because both of those shipped. Screenshots are gitignored; the
scenes are the durable part.

Use it when changing anything in this document. Three of this ticket's fourteen bugs were invisible
to a full test suite and obvious in one screenshot.

## Not verified

The harness covers the shell, the relay and the patient screen at two widths. It does **not** cover
the full Clinic and Lab pages, any real device, any real session, contrast ratios, or focus-ring
appearance — and no user has ever used any of it. The MVP has no real-patient pilot on either track.
