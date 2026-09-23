# Facility onboarding — how a clinic or laboratory joins DHealth

**Added 2026-09-21, DH-MVP-ONBOARD-001.** The operator's guide to enrolling a facility, and to
reading what DHealth is telling you about one. Decisions and their reasoning: `DECISIONS.md`
ADR-055. Architecture: `docs/ARCHITECTURE.md` §21.

> **DHealth Connect has a runtime and no qualified vendor adapter.** DH-CONNECT-AGENT-001 built the
> delivery engine and the on-premises agent; the only adapter behind them is synthetic and contacts
> nothing. No connector to any real external EMR or LIS exists, none has been qualified, and a
> facility in Connect mode can be fully enrolled, fully discovered, hold a genuinely qualified
> connector with a healthy agent, and still be unable to receive a single production order — that is
> the truthful state of the product today, and the screens say so.

---

## The process

```
partner agrees to participate
        ↓
facility identity verified
        ↓
organization enrolled          ← creates the organization and its enrollment together
        ↓
first administrator attached   ← optional here; attachable later from Équipe et accès
        ↓
NATIVE or CONNECT chosen       ← explicit, never inferred
        ↓
configuration (Native) or technical discovery (Connect)
        ↓
readiness evaluated            ← server-derived, from live rows
        ↓
network relationship configured
        ↓
connector qualification        ← Connect only; real, and reachable
        ↓
production routing activated   ← platform-level, readiness-gated
```

Each arrow is a separate fact. A facility that has reached step three is enrolled and is not
configured; one that has reached step seven is configured and may still not be a place orders can
be sent. Conflating them is the failure this whole design exists to prevent.

---

## The two participation modes

| | **DHealth** (`NATIVE`) | **Connect** (`CONNECT`) |
|---|---|---|
| Who runs the workflow | DHealth itself | The facility's existing EMR/LIS |
| What must be configured | Staff, roles, lab profile, offerings, result templates | Technical discovery of the existing system |
| Can it receive orders today | Yes, once operationally ready | **No.** No connector exists |
| What is outstanding | Whatever readiness reports | A DHealth connector, then its qualification |

Neither is the better choice. A facility that keeps its own software is not a lesser participant,
and one that adopts DHealth is not taking a shortcut.

---

## Native readiness

Production routing to or from a Native facility requires real operational authority, held through
an **active** membership:

- **Établissement de soins** — at least one `CLINICIAN`. Nobody else may order.
- **Laboratoire d'analyses** — at least one `LAB_TECH` **and** at least one `LAB_RESULT_RELEASER`.

The releaser requirement is the one worth explaining. Entry and release are separate authorities
(ADR-044). A laboratory with technicians and no releaser can accept an order, enter every value
against it, and then be structurally incapable of releasing the result — work that arrives
somewhere it can never leave. That is why the absence blocks routing rather than merely being
reported.

**One person may hold both laboratory roles.** The requirement is that both authorities exist at
the organization, never that two humans do; a small laboratory where the same person enters and
releases is a real and permitted configuration.

**`Administration` implies nothing operational.** An `ORG_ADMIN` administers the facility. They are
not thereby a clinician, a technician, or a releaser.

### A Native laboratory must be able to finish what it offers

DHealth's own human result entry runs against a **controlled active result template**. Without one,
the Native entry screen says so and offers nothing — that is DHealth Lab's deliberate design, not a
gap. So an active offering with no active template is a service the laboratory appears to sell and
cannot deliver inside DHealth: an order routed against it would arrive, and no value could ever be
entered against it, let alone released.

The rule is therefore **per service, not per facility**:

| Situation | What happens |
|---|---|
| Every active offering has an active template | Nothing is restricted. |
| Some do, some do not | Orders for the templated services route normally. An order requiring an untemplated one is **not eligible** for this laboratory and direct assignment is refused, naming the test. |
| No active offering has one | The laboratory is **blocked** for production routing entirely — it can complete nothing it advertises, which is the same fact as having no offerings at all. |

Partial coverage deliberately does not block the whole facility. A laboratory that can complete
nine of its ten services is a real destination for those nine, and blocking it outright would both
refuse work it can genuinely do and push an operator toward deactivating the tenth offering to
clear a flag. Nothing here ever deactivates an offering, and no template is ever fabricated.

Publishing and activating the missing template makes the same configured route usable again. **The
route is never recreated** — exactly as with staffing.

**This is a Native-workflow rule and reaches no further.** A CONNECT laboratory is entirely
unaffected: its results are produced in the partner's own system, whose structure DHealth neither
owns nor requires, and the Exchange observation model stays as source-flexible as it was for the
connector work to come.

---

## Connect technical discovery

What DHealth records about a facility's existing software:

- software name, vendor, version
- hosting: *Serveur dans l'établissement* · *Cloud* · *Hybride* · *Je ne sais pas*
- technical contact and vendor contact
- which interfaces the partner reports, each with how much DHealth actually knows about it
- whether the source system exposes separate event timestamps and a professional release event
- what identifier concepts exist (patient, order, report, specimen)

**"Je ne sais pas" is a real answer.** A clinic manager may genuinely not know their LIS vendor, and
a form that refuses to record that pushes an operator into guessing. An unknown creates a discovery
obligation for DHealth; it never blocks enrollment.

**No credential is ever stored.** Not an API key, not a password, not a connection string. The
discovery fields describe *what exists*; how DHealth would authenticate to it belongs to a secrets
architecture that does not yet exist. A request carrying an unrecognised field is refused rather
than absorbed into a notes column.

### The three truth levels

```
Signalé par le partenaire   a claim somebody made
Documentation reçue          paper in hand
Interface vérifiée           DHealth checked it exists as documented
```

None of the three means a connector exists. The word "connecté" appears nowhere in this surface,
because nothing has connected. Only platform authority may record a verification — a facility
cannot certify its own interface.

### Where Connect stops today

**Updated 2026-09-22, DH-CONNECT-CONTRACT-001.** The last three lines used to be hardcoded and
permanently unreachable. They are now real state, and they live in their own **Connecteur DHealth**
section below discovery rather than at the end of the discovery list — a fixed *"Connecteur — à
construire"* sitting above a registered connector would be a screen contradicting itself.

```
── Système existant ──────────────────────────
Système identifié            ✓ possible
Interface signalée           ✓ possible
Documentation obtenue        ✓ possible
Interface vérifiée           ✓ possible

── Connecteur DHealth ────────────────────────
Connecteur enregistré        ✓ possible          ← real state
Qualification                ✓ possible          ← real state, derived from evidence
Exécution du connecteur      ✓ possible          ← real state, derived from an agent  (2026-09-23)
Routage de production        ✗ désactivé         ← still unreachable, for a different reason
```

**Updated 2026-09-23, DH-CONNECT-AGENT-001.** The third line became reachable: an agent running at
the facility reports in, and health is derived from what it reports. The fourth did not, and its
reason changed rather than disappearing — routing requires a qualified **vendor** adapter, and the
only adapter that exists is synthetic. A Connect facility can now be registered, genuinely qualified,
and observed healthy, and still not take a single production order.

The paragraph below describes the state between DH-CONNECT-CONTRACT-001 and that ticket, and is kept
because the distinction it draws is still the one that matters — only its last clause has moved.

A connector can now be registered and can genuinely qualify. **It still cannot be put into
service.** `connector_operational` derives from a health state that is unconditionally *unknown*
because nothing reports in, and it blocks routing. So the best state any Connect facility can reach
today is *qualified and unroutable* — which is the honest state, not a bug to be tidied away. The
runtime that would change it is DH-CONNECT-AGENT-001's work.

What an operator can do on that section: register a connector, record that its configuration or
test-code mapping changed (which makes a passing qualification *périmée*), suspend, restore, and
retire. What nobody can do, anywhere, by any route: mark a connector qualified. Qualification is
what running the suite produced.

Full detail: `docs/CONNECT-CONTRACT.md` and `docs/CONNECT-QUALIFICATION.md`.

---

## Integration currently impossible

`NOT_SAFELY_INTEGRABLE` is a truthful conclusion, not a deletion. The facility stays enrolled, the
reason stays visible, and the next step is named: engage the vendor, or reconsider the participation
mode. A partner DHealth cannot serve today is not a record to erase.

---

## Attaching people

Two routes, one endpoint's worth of meaning: a membership with roles. Which route you take depends
only on whether the facility exists yet.

| When | Where | What happens |
|---|---|---|
| While enrolling | The wizard's **Responsable** step | The membership and its roles are created **inside the same transaction** as the organization and its enrollment |
| Any time after | The facility's **Équipe et accès** section | `POST /organizations/{id}/memberships` — the ordinary organization-admin path |

Neither is a shortcut around the other. `first_administrator` is optional at enrollment on purpose:
a partner is often signed up before the person meant to run the facility has logged in for the first
time, and refusing to enrol them until somebody had would invent a prerequisite the business does
not have. The consequence is that a facility can exist with nobody able to administer it — which is
why the second route has to exist, and why its absence was a real hole rather than a convenience gap.

### The bootstrap, unchanged

There is **no invitation system** in this release, and no account provisioning:

1. The intended person signs into DHealth once.
2. That materialises their `ExchangeIdentity` — which grants zero access on its own.
3. An administrator searches for them by name or email.
4. They select the person and grant explicit roles.
5. The membership and role assignments are created.

At enrollment, step 5 is atomic with the facility's own creation. If any part fails — an illegal
role for this facility type, a person who has never signed in — **nothing is created at all**, and
the screen says so. There is no state in which the facility exists and the person meant to run it
was never granted anything, and so no partial outcome for an operator to untangle.

If somebody cannot be found, the reason is almost always that they have never signed in. Both
screens say so rather than leaving an operator wondering.

The lookup is platform-admin-only, needs at least three characters, caps its results, matches by
prefix rather than fuzzily, and returns only identity id, display name and email. **Finding somebody
grants nothing**, and an email address is never authorization.

### Valid roles

`ORG_ADMIN` and `CLINICIAN` for a care facility; `ORG_ADMIN`, `LAB_TECH` and `LAB_RESULT_RELEASER`
for a laboratory. One laboratory person may hold all three. That rule is enforced in the backend for
every caller, not by which checkboxes a screen happens to show — the checkboxes are presentation,
and a role the server will refuse is refused whether or not a screen offered it.

### Équipe et accès

Every facility's detail screen lists who belongs to it, what each of them may do, and whether that
access is live. It is the answer to the three ordinary situations onboarding otherwise had no screen
for: *we hired another technician*, *our old administrator left and somebody else is taking over*,
and *the person we enrolled this facility for has now signed in*.

Attaching somebody re-reads the facility's readiness, so `administrator_present` and the staffing
findings above update as a consequence of the change rather than on the next visit.

**This screen only grants.** `POST .../memberships` is ensure/add on the server: a role left out of
a request body is never a removal request, and no endpoint in this API revokes a role or a
membership. So:

- There is no **Retirer**, no **Désactiver**, and no reactivation control. A button that quietly did
  nothing would be worse than its absence.
- A role the selected person already holds shows **ticked and disabled**. A control that visibly
  cannot be switched off says "not from here"; a hidden one says nothing.
- An `INACTIVE` membership is still **listed**, with its status. Somebody looking for a person who
  used to have access must be able to find them and see why they no longer do. Attempting to add
  roles to such a membership is refused, on screen before the request and by the server's 409 if the
  list was stale.

Revocation is a real requirement and deliberately not part of this release: whoever takes it has to
decide what happens to that person's in-flight orders and unreleased results first, which is a
clinical question rather than a UI one.

### Who may use it

`require_org_admin`, which a **platform administrator satisfies without holding any membership** —
`require_membership` returns early for them with `via_platform_admin`. A caller who is neither gets
**404**, not 403: the API does not confirm that an organization exists to somebody outside it.

---

## Reading a facility's availability

Two questions with different answers:

- **Ce qui empêche la mise en service** — conditions that block production routing right now.
- **Configuration à compléter** — things still to set up that do not stop the facility being a
  destination. A partially templated laboratory appears here: it can take orders for the services
  it can complete, and orders for the others will simply never be routed to it.

Both come from the server, evaluated from live rows on every read. The interface displays them and
never computes its own version: a copy of the rule in the browser would drift from the backend, and
the drift would only be noticed when somebody activated a route that should have been refused.

---

## Route configuration, readiness and eligibility

Three facts, deliberately never merged:

| | Meaning | Changes when |
|---|---|---|
| **Relation configurée** | A platform admin said this pair may exchange work | Somebody decides it |
| **Disponibilité opérationnelle** | The facility can actually do that work today | Staffing, offerings or standing change |
| **Destination éligible** | Both of the above, for this order, right now | Either of the above changes |

A laboratory whose last releaser leaves **keeps its configured route**. Nothing rewrites that row:
the relationship was a deliberate human decision and it remains one. What changes is that the lab
stops being an eligible destination and new assignments are refused. When the staffing returns, the
same route works again — there is nothing to recreate.

Production activation is platform-level and readiness-gated. If readiness changed between the screen
being drawn and the button being pressed, the server refuses and the blocking reasons are shown; the
interface cannot override that.

### Switching a route on

On a care facility's detail screen, **Relations de routage** lists every laboratory on the network
and what is true about each one:

| What you see | What it means |
|---|---|
| *Non connecté* | No relationship is configured. **Activer** is offered if the laboratory can take work today. |
| *Relation configurée · Prêt* | Configured, and orders can be routed there now. |
| *Relation configurée · Routage indisponible — …* | Configured, and the laboratory cannot currently take work. Nothing was deleted; the reason is named. |
| *Non connecté — Aucun connecteur DHealth (mode Connect)* | A Connect laboratory. No staffing or configuration changes this until a connector exists. |

Activation takes two gestures. The second one is preceded by a restatement naming the care facility,
the laboratory, and what activation permits — *"autorisera les nouveaux ordres éligibles de cet
établissement à être adressés à ce laboratoire. Les ordres déjà en cours ne sont pas modifiés."*

**Deactivation is never gated.** Switching routing off stays available precisely when a facility is
in no condition to receive work — that is when an operator most needs it.

If the server refuses, its reasons are shown against that laboratory and nothing is reported as
having succeeded. That is not an error to retry past: readiness is evaluated from live rows inside
the writing transaction, so the refusal is newer than what the screen was showing.

---

## What this does not do

No FHIR or HL7 transport. No SFTP runtime, webhook listener, Connect agent or VPN. No partner
credential storage. No OpenELIS or OpenMRS binding. No automatic vendor detection, code mapping or
patient matching. No connector health. No email or WhatsApp invitations. No billing, contracts or
CRM.

**Amended 2026-09-22, DH-CONNECT-CONTRACT-001.** "No connector qualification" was struck from that
list: a qualification *model* now exists, and a connector can be registered and qualified against
it. Everything else on the list is unchanged, and no connector exists to qualify — the harness runs
against a synthetic subject that contacts nothing.
