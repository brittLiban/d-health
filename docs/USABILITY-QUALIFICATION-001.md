# Native clinic + laboratory workflow — INTERNAL WORKFLOW REVIEW

**DH-USABILITY-001. This document is not a usability qualification.**

§40 of the ticket decides what this may be called, and it is unambiguous: *"If no real humans
participate: the ticket is NOT usability-qualified."* No clinician, no front-desk user and no
laboratory technician took part in anything recorded here. The work was done by an agent in a
sandbox with no way to recruit or moderate a participant, so the label is the one §40 assigns to
developer-only work:

```text
INTERNAL WORKFLOW REVIEW          ← what this is
USER-QUALIFIED                    ← what this is not
```

The distinction §40 draws is the whole point of the document:

```text
component tested  ≠  browser tested  ≠  human tested
        done              done              NOT DONE
```

Everything in **Part A** was run in a real Chromium at three viewport widths and is evidence about
the software. Nothing in it is evidence about a person. **Part B** is the instrument for the human
sessions, written out in full and deliberately empty — it is what somebody with access to a clinic
runs next. **Part C** is what this environment could not do at all.

Read [`MVP-1.0.md`](MVP-1.0.md) §6 for the four product laws the ticket tests against, and
[`UI-001-CLINICAL-UX.md`](UI-001-CLINICAL-UX.md) / [`UI-002-DESIGN-LANGUAGE.md`](UI-002-DESIGN-LANGUAGE.md)
for the design rules the fixes had to stay inside.

---

## Part A — what was actually done

### A.1 Method

| | |
| --- | --- |
| Reviewer | One agent. No clinical, front-desk or laboratory participant. |
| Data | Synthetic only (§5). Invented names, one invented clinic, one invented laboratory, clinically meaningless values. No real record was opened, and none exists in this environment. |
| Surface | The real components, the real stylesheets, the real self-hosted fonts, in headless Chromium via Playwright, against stub data (`ui/harness/`). |
| Widths | 1280×860 (desktop), 768×1024 (tablet), 390×844 (phone), `deviceScaleFactor: 2`, locale `fr-FR`. |
| Captures | 44 scenes × 3 widths = **132 screenshots**, of which **42 are new** (14 new clinic/laboratory scenes). |
| Mechanical checks per scene | page-level horizontal overflow; controls outside the viewport; controls reachable only by scrolling their container; navigation clipping and height; unlabelled inputs; console errors; page errors; failed requests. |
| Looked at | 6 captures read as images, named in A.3. The rest are evidence on disk; the harness's own checks cover all 132. |

**Why a harness rather than the full stack.** §42 prefers native demo + Keycloak + Caddy + a real
browser. The Docker daemon is permission-denied in this environment, so Keycloak and Caddy cannot
start (Part C). The harness is the honest substitute: it is a real browser rendering the real
components, and it is *not* an authenticated end-to-end run. It cannot detect anything about
login, session expiry, routing, or latency.

### A.2 What the screens are

Fourteen scenes were added for the two workflows this product exists for. Before this ticket the
harness covered platform administration, the shell and the patient surface — the clinician's and
the technician's own screens had never been photographed at any width, so the mechanical checks
above had never run against them.

```text
clinic-today              clinic-patients          clinic-patient-new
clinic-orders             clinic-order-handoff     clinic-results-queue
clinic-results-detail     clinic-results-superseded
lab-today                 lab-orders               lab-entry
lab-release-queue         lab-release-inspect      lab-version-history
```

### A.3 Findings

Severity follows §24. Every P0 and P1 below was fixed inside this ticket (§33).

#### P0-1 — a superseded result did not say it was superseded *(fixed)*

`ClinicResultsPage`. The version tabs read `Version 1 — Revu — aucun suivi requis` and
`Rectificatif (version 2) — À examiner`. They stated which release was *later*. Nothing on the
screen stated which one is **current**. A clinician opening version 1 after a correction saw a
complete, plausible result with nothing contradicting it — §13's named P0 exactly: *"If the user
cannot confidently identify which result is current: P0 usability defect."*

Fixed by putting the standing in the tab's own text and by leading the content with it:

```text
Version 1 — Remplacé — Revu — aucun suivi requis
Rectificatif (version 2) — Résultat actuel — À examiner

    ┌──────────────────────────────────────────────────────────────┐
    │ Ce résultat a été remplacé.                                  │
    │ Le résultat actuel est le rectificatif (version 2). Ce que   │
    │ vous consultez ici n’est plus le résultat de référence.      │
    │ [ Voir le résultat actuel ]                                  │
    └──────────────────────────────────────────────────────────────┘
```

Three decisions worth recording:

* **In the text, not in a colour.** A badge that a stylesheet can hide, a screen reader can skip or
  a greyscale print can flatten is not a safety control (§30 — status never by colour alone).
* **Derived from the highest `release_version_number`**, never from array order, so a
  differently-sorted response cannot invert the meaning.
* **Reviewing an older version is still allowed.** ADR-048 Model 3 gives every released version its
  own immutable review obligation, and a clinician who genuinely read version 1 may record a
  disposition against it. What changed is which action leads.

Seen at 1280 and 390 (`clinic-results-superseded-desktop.png`, `-phone.png`): both tabs stay
legible at phone width and the banner with its action is fully visible.

#### P1-1 — the laboratory's own history had the same gap *(fixed)*

`ReleaseVersionHistory`. Rows read `Version 1 — Version initiale — 23/09/2026 14:00:00` — which came
first, and when, but not which one counts. A technician answering *"what did we actually report?"*
(§21) could read superseded values with nothing contradicting them. Now:

```text
Version 1 — Version initiale — Remplacé — 23 sept. 2026, 14:00
    Version remplacée — ce n’est plus le résultat de référence.
    · Glycémie à jeun — Glycémie : 5.4000 mmol/L
Version 2 — Amendement — Résultat actuel — 23 sept. 2026, 15:10
```

Same words as the clinician's screen, deliberately: "résultat actuel" has to mean the same thing in
both rooms. The exact content is still shown — this labels the values, it does not withhold them.

#### P1-2 — on a tablet, the laboratory's primary actions were off the screen *(fixed)*

Both laboratory screens held a fixed `1fr 1fr` queue-beside-detail layout at every width. At 768px
that squeezed the queue to roughly 200px and pushed the detail panel past the right edge.

Measured, before → after:

| Scene | tablet 768 | phone 390 |
| --- | --- | --- |
| `lab-entry` | 101px overflow, `Enregistrer les valeurs` + `Terminer la saisie` off-screen → **0** | 251px, + `Fermer le détail` off-screen → **0** |
| `lab-release-inspect` | 4px → **0** | 154px, `Libérer les résultats` off-screen → **0** |
| `lab-version-history` | 24px → **0** | 174px, navigation itself off-screen → **0** |

Fixed with one rule rather than a breakpoint
(`ui/src/features/lab/panelLayout.ts`): `repeat(auto-fit, minmax(min(100%, 22rem), 1fr))` — the
second column appears when there is room for it and the panel stacks under the queue when there is
not.

#### P1-3 — patient names broke mid-word *(fixed)*

`PatientIdentity` and `LabPatientIdentity` carried `overflow-wrap: anywhere`. Wherever a column was
narrow it broke identity apart:

```text
before                       after
------                       -----
Fatou      Ismail            Fatouma Robleh
ma         Warsa             Ismail Warsama
Robleh     ma                Nasra Guelleh
Téléph                       Téléphone : +25377100001
one :
+2537
71000
01
```

`anywhere` also *caused* the narrowness: it lets a word count as one character wide when the
browser measures min-content, so the cell agreed to be unreadably narrow and then filled that width
by breaking the name. `break-word` breaks only when a word genuinely cannot fit and leaves
min-content alone. A technical identifier keeps `anywhere` — a UUID has no break opportunity and
loses nothing.

#### P1-4 — the orders table dragged the whole page sideways *(fixed)*

`ClinicOrdersPage`'s six-column table already sat in an `overflow-x: auto` box, and the box did
nothing: a grid item defaults to `min-width: auto` and refuses to shrink below its content, and the
scroll container needed its own containing block. The page scrolled 235px at tablet and 385px at
phone, carrying `Suivi patient`, `Choisir un laboratoire` and, at phone width, the navigation
itself outside the viewport. `minWidth: 0` + `position: relative` → **0 at both widths**; the table
now scrolls inside its own card.

#### P1-5 — three error messages written from the inside, in unaccented French *(fixed)*

`ClinicPatientsPage`. The shipped text:

```text
Impossible de verifier la recuperation sure d'une creation precedente.
Impossible de preparer la recuperation sure de cette creation. Aucun envoi n'a ete effectue.
Stockage local corrompu : Le stockage local de réconciliation d’ordre est corrompu…
```

Two defects at once. Five missing accents in a product where French is first class (CLAUDE.md), and
vocabulary belonging to the idempotency mechanism rather than to the person at the desk. §32 asks
every error to answer *what happened / did my action succeed / what should I do now*; none answered
the third. Rewritten to state only what is actually known — the first cannot claim the patient does
or does not exist, because that is precisely what could not be checked, so it says how to find out;
the second can claim it, because nothing was sent, and saying so is what stops a duplicate.

#### P2-1 — result entry needed the mouse *(fixed)*

§18: *"A lab technician doing 100 samples per day cannot tolerate a mouse-heavy workflow."* Typing a
value and then having to find and click a button is that workflow. `Enter` in an entry field now
does exactly what `Enregistrer les valeurs` does, behind the same guard; `Enter` in a correction
field confirms the correction. `Enter` never completes entry — ending entry keeps its own two
deliberate gestures. The panel was deliberately not made a single `<form>`: it holds entry fields,
an independent correction editor and a two-step completion, and one implicit submit across all
three would mean `Enter` doing something different depending on where the cursor happens to be.

#### P2-2 — two date formats on one card *(fixed)*

`23/09/2026 14:00:00` sat directly above `23 sept. 2026, 14:00` in the same laboratory card, and
the entry form used the seconds-bearing form too. A reader comparing two timestamps should be
comparing the times, not first working out that the formats differ (§27). Both now use the app's
format.

#### Fixed, found by a test rather than by looking

`--radius-2` (pre-existing, from DH-NOTIFY-001) and `--color-warning-400` / `--color-warning-50`
(introduced by the P0 fix above) were referenced and never defined — silent fallbacks to a radius
of 0 and to a hue belonging to no palette here. The design-token test named all three; the banner
now uses the design system's own warning pair.

### A.4 Open findings — not fixed, with the reason

| | Severity | Finding | Why it is still open |
| --- | --- | --- | --- |
| O-1 | **P2** | At 768 and 390 the clinic orders table's action controls (`Suivi patient`, `Choisir un laboratoire`, `Parcours`) are reachable only by scrolling the table sideways. The clipped status badge is the only affordance. | The honest fix is a card layout below a breakpoint, which is a redesign of the screen rather than a defect fix. §29 names desktop, tablet and *the mobile patient view* — a clinician's phone is not a stated target, which is what keeps this at P2 rather than P1. Somebody should decide whether staff phones are in scope before the redesign is built. |
| O-2 | **P2** | Pressing `Examiner` on the results queue renders the detail *below* the queue and the history cards, with no scroll-into-view and no focus move. With a long queue the panel opens off-screen. | Focus management is worth doing deliberately across all four detail panels at once, not one screen at a time, and it interacts with the accessibility work §30 says not to damage. |
| O-3 | **P3** | 20px of page overflow at 390px on the result detail, from the four-column observations table. | `table-layout: fixed` was tested and removes it, at the cost of changing how every clinical values table lays out at every width. Not worth that trade for 20px; recorded with the tested fix so the next person does not have to find it again. |
| O-4 | **P3** | On the laboratory history rows, `Voir le contenu exact` wraps to three lines beside its row. | Cosmetic. §24: do not let P3 consume the ticket. |
| O-5 | **P3** | Every queue card and table row shows the internal order id (`order-entry`, `order-completed`). | §26 (automatic-data audit) says the system should not make people read what it already knows. Whether staff actually use that string to talk to a laboratory on the telephone is a question for a human session — removing it on a guess would be the wrong direction. |

### A.5 Required-field audit (§25)

| Form | Required today | Verdict |
| --- | --- | --- |
| Create patient (`ClinicPatientsPage`) | Name only. Date of birth and telephone are labelled `(facultatif)`. | **Correct.** §8's target minimum is exactly `Name`. Nothing was removed; the label `Nom affiché (obligatoire)` was renamed `Nom du patient (obligatoire)` — "nom affiché" is the `display_name` column, not a thing a front-desk user is asked for. |
| Result entry (`NativeResultEntryForm`) | Whatever the pinned template marks required, shown as `(Requis)` / `(Facultatif)`. Units and ranges come from the template. | **Correct.** Nothing is invented by the form; §18's "are units/ranges already provided" is answered by the template. |
| Accept / reject order | Nothing beyond the decision. | **Correct.** §15 says not to invent a rejection-reason policy, and none is invented. |
| Register patient (`RegisterPatientForm`) requires four fields | — | **Out of scope.** That form belongs to the maternity/OpenMRS reference-connector track and is not reachable from the staff workspace. Recorded so the next reader does not "fix" a screen this ticket did not test. |

### A.6 Terminology audit (§27)

| Was | Is | Why |
| --- | --- | --- |
| `Nom affiché (obligatoire)` | `Nom du patient (obligatoire)` | A column name, not a question. |
| `Stockage local corrompu` | `Création d’ordres bloquée sur cet appareil` | Names the situation and the consequence instead of the mechanism. |
| `verifier`, `recuperation`, `creation precedente`, `ete effectue` | accented French | CLAUDE.md. |
| `23/09/2026 14:00:00` | `23 sept. 2026, 14:00` | One date format per screen. |
| `Version 1` alone | `Version 1 — Remplacé` / `— Résultat actuel` | The P0 above. |

Left alone deliberately: `Rectificatif`, `Libération`, `Saisie terminée`, `Amendement`. They are the
words the two ADRs and the existing screens already use, and renaming clinical vocabulary without a
human in the room is exactly the kind of guess this ticket is meant to replace with evidence.

### A.7 Regression tests added (§41)

14 new tests, each with a negative control that was actually run.

| Test | Pins | Negative control | Went red |
| --- | --- | --- | --- |
| `UI-CUR1`…`UI-CUR5` (`ClinicResultsPage.test.tsx`) | Exactly one tab named current and it is the latest; the replaced version says so above the values; the banner navigates and then withdraws; a single version claims no replacement; a legacy unversioned result claims neither standing. | Reverse the `currentVersionIdOf` comparison | `UI-CUR1`, `UI-CUR2`, `UI-CUR3` — and nothing else |
| " | " | Revert the tab label and suppress the banner | `UI-CUR1`…`UI-CUR4` **and** `UI-R5`, which asserts the tab's own text |
| `VH-UI2`…`VH-UI4` (`LabReleasePage.test.tsx`) | The laboratory rows, the replaced-content line, and the single-version case | Reverse the lab `currentVersionIdOf` comparison | `VH-UI2`, `VH-UI3` — and nothing else |
| `UI-KB1`…`UI-KB3` (`LabOrdersPage.test.tsx`) | `Enter` records values; `Enter` on an empty field records nothing; `Enter` never completes entry | Remove the `handleSave` call from the key handler | `UI-KB1` only |
| `FR1`, `FR1-NC`, `FR2` (`frenchCopy.test.ts`) | No always-accented word appears unaccented in any string or JSX text on eight clinic/lab files; the four rewritten phrases cannot return | Reintroduce the original message | `FR1` and `FR2` |

`frenchCopy.test.ts` deserves its own note, because its first version was worthless and passing.
It matched quoted spans with a regular expression; that desynchronises on every short literal and
every apostrophe in JSX text (`l'état`), then tests the garbage between stray quotes, and it never
saw JSX text at all — which is where most of what a user reads lives. It passed while a live match
sat in `ClinicResultsPage.tsx`. It now reads the copy through the TypeScript parser, and its word
boundaries are Unicode-aware because JavaScript's `\b` is ASCII-only and would report the correctly
accented `enregistrée` as the error. `FR1-NC` asserts both directions in the test file itself.

### A.8 Numbers

```text
UI test suite         882 tests across 58 files, run in 10 batches
                      881 pass
                        1 fails: src/features/auth/logout.test.ts — pre-existing,
                          environment-bound, untouched by this ticket
new tests               14
tsc -b --noEmit       clean
oxlint                48 → 49 warnings, 0 errors. The new one is
                      react(only-export-components) on the harness scene function,
                      the same rule and the same shape as three that already exist
                      in that same file
screenshots          132 (42 new), no console error, page error or failed request
                      in any scene at any width
fonts actually loaded IBM Plex Sans 400/600, Source Serif 4 600
```

---

## Part B — the human sessions, not run

This is the instrument. It is empty because nobody has sat at the screen. Filling it in is what
turns `INTERNAL WORKFLOW REVIEW` into a qualification, and nothing else does — §40 is explicit that
automated browser tests are not a substitute.

### B.1 Participants required (§4, §44)

| Code | Role | Status |
| --- | --- | --- |
| `Participant C1` | Clinician | **Not recruited** |
| `Participant F1` | Front desk / reception | **Not recruited** |
| `Participant L1` | Laboratory staff (with release authority for L7/L8) | **Not recruited** |

### B.2 Moderator rule (§6)

The moderator explains the **scenario**, never the interface. "Order a CBC and glucose for this
patient" is a scenario. "Click Ordres, then Nouveau" is coaching, and it destroys the measurement.
Hesitations are the data.

### B.3 Scenarios and what to record

Synthetic data only. Do not open a real record (§5).

| | Scenario | Say to the participant | Record |
| --- | --- | --- | --- |
| C1 | Find an existing patient | "Amina Hassan is already a patient. Find her." | first place they look; whether search is obvious; spelling tolerance; time to patient open |
| C2 | Register a new patient | "This patient has never visited before. Register them." | every field they ask "why do I need this?" about |
| C3 | Create diagnostic order | "Order a CBC and glucose for this patient." | how they find ordering; clicks; whether routing makes sense; anything DHealth asks that it already knows |
| C4 | Patient access handoff | *(per §10)* | whether they can hand the patient something without help |
| C5 | Find outstanding order | *(per §11)* | whether "where is it" is answerable |
| C6 | Review result | "The laboratory has released Amina's result. Find and review it." | whether the new result is obvious; whether release history confuses; whether the follow-up decision is understandable |
| C7 | Result amendment | "The laboratory corrected this result after it was originally released. Find what changed." | **the P0 above is the fix for this scenario. Whether it works is what a human session decides.** |
| L1 | Incoming order | "A clinic has just sent you a CBC order. Find it." | whether incoming work is obvious |
| L2 | Accept order | "Accept this order." | whether rejection feels deliberate |
| L3 | Collection | "The sample was collected." | whether `Collect` is immediately understandable |
| L4 | Start processing | "The specimen is now being analyzed." | whether terminology matches actual lab language |
| L5 | Enter results | "Enter these synthetic CBC results." | time; clicks; **keyboard use**; tab navigation; how often they move between mouse and keyboard |
| L6 | Correct value before release | "You entered the wrong value. Correct it." | whether current/previous/correction is understood without the supersession model |
| L7 | Release result | "Review this completed result and release it." | whether they ever think *"I don't know what I'm approving"* |
| L8 | Amendment | "The released result contains an error. Correct it and publish an amended version." | whether they understand they are not editing history |
| E2E | One uninterrupted journey | §22 | total time; every hesitation |

### B.4 Empty measurement table (§23, §45.5, §45.6)

| Scenario | Participant | Completed unaided? | Time | Hesitations | Severity of what was seen |
| --- | --- | --- | --- | --- | --- |
| C1–C7, L1–L8, E2E | — | — | — | — | — |

### B.5 MVP product gaps (§34)

```text
MVP PRODUCT GAPS
(empty — this list may only be filled from what participants actually ask for)
```

For each request, §34 wants: who asked, what workflow triggered it, how often it appeared, whether
the workflow is impossible without it, and whether a manual workaround exists. Nothing may be
entered here from a developer's intuition, and §33/§43 forbid building any of it inside this ticket.

### B.6 Role-model findings (§36)

The hypothesis to test is the existing small role set (front desk, clinician, laboratory staff,
admin). It is **untested**. §35 is the guard: a participant saying "our receptionist also takes
payments" is evidence about a *task*, not a licence to create a role.

---

## Part C — what this environment could not do

| | Blocked by | Consequence |
| --- | --- | --- |
| Real human sessions (§4) | No access to participants | The ticket is not usability-qualified. Everything in B is unrun. |
| §42's preferred stack — native demo + Keycloak + Caddy | Docker daemon permission-denied | No authenticated end-to-end run. Nothing here is evidence about login, session expiry, real routing or real latency, so §31 (performance perception) is **entirely unassessed**: stub promises resolve instantly and can show neither a slow search nor a loading flash. |
| Error states that need a live server (§32) | Same | `network temporarily unavailable`, `session expires`, `stale release state` and `notification unavailable` were read in the source and, where already covered, exercised under jsdom — not seen in a browser. Only the duplicate-patient/order family was reviewed as text and rewritten. |
| Manual exploratory use (§42) | Agent environment, no interactive session | The captures are scripted. Nobody moved a pointer around the screen looking for something. |

---

## The handoff (§45)

1. **Starting SHA** — `2395da221f44d643535a6a5f78adcf120430b623`
2. **Ending SHA** — the commit carrying this document
3. **Participants by role** — none. No clinician, no front-desk user, no laboratory staff
4. **Scenarios completed** — none with a participant. Fourteen screens photographed and reviewed at
   three widths; C7 and L8 are the two the fixed defects belong to
5. **Task completion rates** — not measurable without participants
6. **Task timings** — none. There is no honest way to time a task nobody performed
7. **Top friction points** — the ten things a reviewer would meet, in the order a reviewer would
   meet them: ① a superseded result that did not say so (P0-1); ② the same gap in the laboratory's
   history (P1-1); ③ the laboratory's save/complete controls off a tablet screen (P1-2); ④ patient
   names broken mid-word (P1-3); ⑤ the orders table dragging the page sideways (P1-4); ⑥ error
   messages written from inside the implementation, unaccented (P1-5); ⑦ entry requiring the mouse
   (P2-1); ⑧ order actions reachable only by scrolling a table (O-1); ⑨ a detail panel that opens
   below the fold (O-2); ⑩ two date formats on one card (P2-2)
8. **P0 findings** — one (P0-1). Fixed
9. **P1 findings** — five (P1-1…P1-5). All fixed
10. **P2 findings** — P2-1 and P2-2 fixed; O-1 and O-2 open with stated reasons
11. **Fixes made** — §A.3; eight code fixes and one pre-existing token defect
12. **Required fields removed / retained** — none removed, none added. §A.5 records why each one
    that remains is justified
13. **Redundant inputs eliminated** — none found in the tested forms. The internal order id shown on
    every row (O-5) is the one redundancy recorded, deliberately unfixed
14. **Terminology changes** — §A.6
15. **Role-model findings** — none. The role hypothesis is untested (§B.6)
16. **Missing-product requests** — none. The list may only be filled from what participants ask
    (§B.5)
17. **Browser/runtime evidence** — headless Chromium via Playwright, 132 captures at 1280/768/390,
    no console error, page error or failed request. Not the §42 stack: no Keycloak, no Caddy, no
    authenticated session
18. **Automated test counts** — 882 UI tests, 881 passing, 14 new, 5 negative controls run
19. **Unresolved issues** — §A.4, plus the whole of Part B and Part C
20. **Recommendation for `DHEALTH-NATIVE-MVP-1.0`** — do not treat this document as usability
    evidence. Fund one clinician, one front-desk user and one laboratory technician for a day, run
    Part B unchanged, and let §34's gap list be written by them. The structural defects that would
    have wasted that day are now fixed, which is the most this environment could contribute

```text
WOULD A CLINICIAN USE THIS TOMORROW?
PARTIALLY

WOULD A LAB STAFF MEMBER USE THIS TOMORROW?
PARTIALLY

WHAT STILL STOPS THEM?
Nobody has watched either of them try. That is not a formality: the one defect
that could have changed a clinical decision -- a corrected result that did not
say it had been corrected -- was invisible to 844 passing tests and to every
prior review, and was found by looking at the screen. What else is invisible is
unknown, and the only instrument that finds it is a person doing their own job
in front of it.

Concretely, and separately from that: on a tablet a clinician's order actions
are reachable only by scrolling a table sideways with no affordance saying so
(O-1); opening a result renders the detail below the queue with no scroll or
focus move, so on a busy day it opens off-screen (O-2); and nothing here has
been through a login, a session expiry or a real network, so no claim about how
this behaves when something is slow or has just failed is supported by anything
in this document (Part C).
```

---

*DH-USABILITY-001. `INTERNAL WORKFLOW REVIEW`, not `USER-QUALIFIED` — and no honest reading of §40
allows the second label until Part B has real names in it.*
