# Field diagnostic workflow — generalized evidence from real Djibouti artifacts

**Status:** Evidence record, not a decision document. **Date:** 2026-09-13 (DH-FIELD-001).
**Source:** Liban reviewed two real-world Djibouti diagnostic workflow artifacts — a doctor/clinic-
issued lab order, and a laboratory-generated result packet — and reported generalized findings from
them to this session.

> **This document contains no real patient data of any kind** — no name, DOB, identifier, dossier
> number, practitioner name, result value, timestamp, address, phone number, or reproduced image
> from either artifact. Every example below is either a generalized description of a *shape* (what
> kind of field existed, not what it said) or an explicitly synthetic illustration. Where a finding
> could only be stated by revealing real content, it is instead named as an open question with no
> answer given. This document does not decide anything by itself — see `DECISIONS.md`'s **ADR-046**
> for the one narrowly-worded architectural requirement accepted from this evidence, and
> `PROJECT_STATE.md`'s **DH-FIELD-001** entry for the full reconciliation this evidence triggered.

This document does not invent clinical content. It records what *shape* of software/data problem
the artifacts revealed — never lab panel composition, analyte lists, reference ranges, units,
clinical thresholds, or interpretation rules, all of which remain reserved to a clinical lead and a
real laboratory's own approved catalog, per `AGENTS.md`.

---

## Finding 1 — order entry is operationally simple

The observed clinic order's shape was simple: a patient, the requested diagnostic service(s), a
date, and ordering-clinician provenance. Nothing about the artifact suggested a doctor works through
a complex form to place a routine order.

**Development implication.** DHealth Clinic ordering should stay low-friction, consistent with
`docs/MVP-1.0.md` Law 3 (faster than what it replaces). Conceptual target flow:

```text
Find patient
        ↓
Select diagnostic service(s)
        ↓
Select/confirm lab if required
        ↓
Send / Print
```

Provenance the system itself should supply automatically, never re-asked of the clinician: identity,
provider organization, timestamp, order id, and audit trail. This does not change routine ordering
into a large EMR-style workflow.

---

## Finding 2 — an orderable service is not the same thing as a returned observation (primary finding)

**The strongest architecture-relevant finding, stated as plainly as the evidence supports:**

> Clinicians order diagnostic services/panels. A laboratory may return multiple observations for one
> ordered service.

Conceptually:

```text
LabOrderItem
"Panel / diagnostic service"
        ↓
Result grouping
        ↓
Observation
Observation
Observation
...
```

The doctor is not, and should not be, required to order every underlying observation individually —
one clinical intent (order this diagnostic service) can legitimately produce a variable number of
discrete result values. **This is the one finding this evidence session treats as strong enough to
accept as a binding architectural requirement** — see ADR-046. No specific panel's real composition
is recorded here or anywhere in this repository as a result of this finding; see Finding 3.

---

## Finding 3 — panel composition is source/facility configuration, not invented here

One artifact from one facility is not sufficient evidence to define any panel's universal
composition, and this document does not attempt to. Future panel/observation-set configuration may
need to vary by laboratory, by DHealth's own catalog, or by source system — which of these (or some
combination) is not decided here. The exact clinical composition of any named panel requires
approved laboratory-workflow/catalog evidence and clinical-lead sign-off, not an agent's inference
from one artifact. **No panel composition, analyte list, or fixed observation set is recorded
anywhere in this document or committed to this repository as a result of reviewing these
artifacts.**

---

## Finding 4 — participating organizations already carry their own reference identifiers

The lab workflow's artifacts demonstrate that participating organizations already generate and rely
on their own transaction identifiers, independent of any identifier DHealth might assign. Future
architecture — specifically DHealth Connect, for an organization running an existing LIS — must be
capable of representing source-owned references alongside DHealth's own, conceptually:

```text
DHealth order ID
provider local order ID
lab dossier/reference
LIS order ID
LIS report ID
```

No specific identifier value, format, or numbering scheme from either artifact is recorded here.
This is recorded as a future architectural requirement, not designed or scheduled by this document.

---

## Finding 5 — source event time is distinct from DHealth's own recorded time

An existing LIS may know the true source-system timestamp for an event (e.g., when a sample was
collected, or when a result was created), which is not necessarily the same instant DHealth first
learns about it. Future DHealth Connect architecture must be able to distinguish:

```text
source event time
```

from:

```text
DHealth received/recorded time
```

Candidate distinguishable events include collection, result creation, professional validation/
release, and report issuance — named here as *candidates*, not a schema. **No new column is added
by this document, and DH-EX-011's `LabSampleCollection.recorded_at` is not renamed.** If anything,
this field evidence reinforces why `recorded_at` was named precisely as DHealth's own recording time
in the first place (`models.py`'s own docstring already makes exactly this distinction) — a future
connector may separately supply an authoritative source-event time alongside it, without either
value overwriting the other.

---

## Finding 6 — the real laboratory workflow already distinguishes entry from professional release

The laboratory report artifact contains professional validation/signature structure — i.e., the real
paper workflow already treats "a result exists" and "a result has been professionally authorized to
leave the lab" as two distinguishable moments. This is independent field support for the separation
ADR-044/DH-EX-012 already built into DHealth:

```text
result entered
≠
result released
```

**No specific job title, credential, or facility release policy is inferred from one artifact.**
ADR-044 remains the sole authoritative source for DHealth-native MVP release authorization
(`LAB_RESULT_RELEASER`, additive to `LAB_TECH`) — this finding supports that ADR's existing shape,
it does not modify or extend it.

---

## Finding 7 — the observed laboratory already runs software (existing-LIS → DHealth Connect)

The result artifact is a structured, computer-generated packet — evidence that at least this
laboratory already operates some software system, not a purely paper-based workflow. Product
implication, consistent with `docs/MVP-1.0.md` Law 2 (no double entry):

```text
existing LIS
        +
DHealth Connect
```

is the intended steady-state architecture for an organization in this shape — not:

```text
existing LIS
        +
manual duplicate DHealth result entry
```

**No specific LIS product is named, and no interface protocol (API/FHIR/HL7/CSV or otherwise) is
claimed to exist** — nothing in either artifact demonstrates an interface, only that structured
software output exists on the laboratory side. Whether and how that software could someday connect
to DHealth Connect is future discovery work, not decided or scoped here.

---

## Finding 8 — reference ranges may not always be a clean low/high pair

Future observation/result modeling must not assume every reference range can be represented only as:

```text
low
high
```

A real source system may provide numeric bounds, inequality-style values (e.g., a bound expressed as
"greater than" or "less than" rather than a closed range), or purely textual reference information.
**None of these are interpreted clinically by this finding** — no automatic derivation of "high,"
"low," "abnormal," "critical," or "dangerous" is proposed, implied, or built. DHealth continues to
record and route reference-range information exactly as a source provides it, never compute a
clinical judgment from it. The exact schema capable of preserving this range of source
representations is explicitly deferred to DH-EX-012B, not designed here.

---

## Finding 9 — ordering-practitioner provenance is needed on the lab side too

The lab workflow may need an appropriate ordering-practitioner reference/display value on its own
side of the transaction. This should derive from DHealth's own existing practitioner/provider
provenance mechanism (the same source-system + source-identity binding DH-EX-004/DH-EX-009 already
established), not a new identity concept. Unrelated authentication/account metadata (e.g., login
credentials, internal identity ids) must never be exposed to the lab side as if it were clinical
provenance.

---

## Finding 10 — patient identity presentation varies across organizations

Names and identity details may be displayed or formatted differently by different organizations
participating in the same transaction. This reaffirms, rather than changes, DHealth's existing
patient-matching stance (ADR-042/`docs/MVP-1.0.md` §7): no exact-display-name identity matching, no
fuzzy automatic merge, source references remain authoritative, and staff/human confirmation of
identity remains necessary. No demographic field is added to any model as a result of this finding.

---

## Finding 11 — the minimum patient-verification dataset is an open question, not answered here

The laboratory side of a real workflow may use demographic information (for example, date of birth)
to verify that the person presenting for a test is genuinely the patient named on the order. This
evidence does **not** justify a conclusion that DHealth's Exchange should automatically transmit or
store date of birth, a national identifier, an address, or any fuller demographic profile — doing so
would be a real privacy/scope expansion this document does not have standing to make.

**Recorded as an explicit open question, not a decision:**

> What minimum patient attributes does a receiving laboratory need to safely verify the person
> presenting for the order?

This requires a human decision and further field validation — it is not resolved by this document,
and no code, schema, or API change results from raising it here.

---

## Finding 12 — paper, print, and QR remain operationally valid

The observed workflow shows paper is still a real, working part of diagnostic transactions in this
setting. Target transitional pattern, consistent with `docs/MVP-1.0.md` §7's "one order, many doors"
principle:

```text
Digital DHealth order
        ↓
optional printed slip
        ↓
patient carries it
        ↓
lab scans QR / enters reference
        ↓
same canonical DHealth order opens
```

**Not built by this document** — recorded as a reaffirmed target for a future access-channel
implementation, exactly as `docs/MVP-1.0.md` §7 already frames print/QR as a design constraint, not
a current feature.

---

## Finding 13 — identifying an order by QR/reference must never itself be authorization

A printed reference or QR code may identify *which* order someone is asking about. It must never, by
itself, grant unrestricted clinical access to that order's content. Authenticated
organization/user authorization (the Exchange's existing OIDC/role model) remains structurally
independent of order identification, and a patient-bearer grant (DH-EX-005's own opaque,
order-scoped mechanism) remains the separate, correct mechanism for patient-side access — a QR/
reference is a lookup key, never a credential.

---

## Finding 14 — a real report may span multiple pages

A real laboratory episode's report artifact was multi-page, not a single scalar value. The only
architectural consequence recorded here: the result domain must not equate "a result" with a single
number or line of text — it must accommodate a genuinely variable-shaped, possibly multi-part
output. **This document does not decide to store PDFs, images, or any specific document format** —
document/report storage is separate product and privacy work, not started here.

---

## Finding 15 — analyzer integration is not demonstrated by this evidence

A computer-generated, structured result packet demonstrates that software produced the report — it
does **not** demonstrate that an analyzer instrument transmitted results automatically into that
software. No automatic analyzer-to-LIS transfer, middleware, or interface protocol is inferred from
either artifact. Real analyzer-integration workflow, if any exists at this facility, would need to be
separately observed before any claim is made about it.

---

## Finding 16 — what this evidence does, and does not, block right now

These findings do not require or justify opportunistic refactoring of unrelated, already-shipped
Exchange code. **They do become blocking**, however, before any of the following can be responsibly
built:

- a real DHealth Lab result-entry UI
- a real LIS-to-Exchange result import path
- production released-result rendering for a provider
- result correction/revision implementation (ADR-045)
- post-release amendment implementation (ADR-045)
- production report/document modeling

**Consequence recorded plainly:** ADR-045's correction/amendment implementation is paused, not
because ADR-045 itself is unresolved on its own terms, but because building a correction/revision
mechanism on top of today's one-`LabResult`-per-`LabOrderItem` representation — which this evidence
shows is not the final production shape — would very likely need to be redone once the underlying
result-domain cardinality question (Finding 2 / ADR-046) is resolved. **ADR-045 is not marked
resolved by this document.** DH-EX-012B is the ticket responsible for resolving the result-domain
shape question; only after that can ADR-045's own decisions be implemented against a stable schema.

---

*This document follows the same evidentiary discipline as `docs/MVP-1.0.md`: findings are recorded
as evaluated, not adopted, unless a corresponding ADR explicitly accepts one. See `DECISIONS.md`'s
ADR-046 for the single requirement accepted from this evidence, and `PROJECT_STATE.md`'s
DH-FIELD-001 entry for the full reconciliation record.*
