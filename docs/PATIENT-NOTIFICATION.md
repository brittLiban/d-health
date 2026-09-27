# Patient notification — a nudge, and what it deliberately is not

**DH-NOTIFY-001 (ADR-058), transport changed by DH-NOTIFY-META-001 (ADR-059).** DHealth can now
send a patient a WhatsApp message telling them something about their laboratory order has
changed. This document is about what that message
contains, when it is sent, and the several things it must never be mistaken for.

**Provider, stated once and plainly.** WhatsApp goes **directly to Meta's WhatsApp Cloud API** —
`DHealth → Meta → WhatsApp → patient`, with no intermediary. Twilio was the assumed provider in
DH-NOTIFY-001 and was removed entirely by ADR-059. **SMS has no selected provider**: the domain
understands the channel, no deployment can currently send on it, and the staff UI is told so by
the server rather than guessing.

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) §19 first — the patient access path this rides on —
then ADR-058 and ADR-059 for why the design is shaped the way it is, and
[`META-WHATSAPP-RUNBOOK.md`](META-WHATSAPP-RUNBOOK.md) for what an operator must configure.

---

## The one-paragraph summary

A clinician issuing a patient's secure link may also send it, by WhatsApp, to a number the
patient agreed to. When a laboratory later releases a result, or corrects one, the patient gets a
second message that says an update exists and nothing else — **no link, no value, no test name, no
disposition**. They open the link they already hold, or contact their clinic. Every message is
durably owed before anything is sent, a provider outage never affects a clinical release, and
`DELIVERED` is written only after an authenticated receipt.

**No message has ever been sent.** No real Meta endpoint has been contacted, no credential
exists, and ADR-003's gate stays **BLOCKED**. Everything below is qualified against a
deterministic fake and real PostgreSQL 18.6. A real-provider qualification *path* now exists
([`META-WHATSAPP-RUNBOOK.md`](META-WHATSAPP-RUNBOOK.md)) and **has never been run**.

**Meta's primary documentation could not be read** from the environment this was implemented in,
so the wire shapes are corroborated from independent secondary sources rather than verified
against Meta's own current text. Every value that could drift — the Graph API version, the
template names, the template language — is configuration with no default, and confirming the
shapes against the live API is the first qualification task.

---

## The distinctions that must not collapse

```
patient notification  ≠  ResultDelivery  ≠  ConnectorDelivery  ≠  DoctorReview
```

| | means |
|---|---|
| `PatientNotification` | a **patient** was sent a content-free message telling them to go and look |
| ~~`ResultDelivery`~~ | ~~the **ordering provider organization** received the result. Semantics still an open product decision (`MVP-1.0.md` §10 item 6)~~ — **DEPRECATED 2026-09-27, ADR-064: owns nothing.** External provider-system delivery is `ConnectorDeliveryObligation`/`ConnectorDelivery`. Still untouched by this ticket, and now by every future one |
| `ConnectorDelivery` | a **partner EMR/LIS** was handed a canonical Connect message |
| `DoctorReview` | a **clinician** recorded a follow-up disposition |

And within notification itself:

```
PENDING → SENDING → SENT → DELIVERED
                 ↘ FAILED
```

* `SENT` — **Meta accepted the request.** That is the strongest claim a 2xx from the Cloud API
  supports. Nothing about a handset is known.
* `DELIVERED` — the provider sent an authenticated receipt saying a handset accepted it. It does
  **not** mean read, opened, understood, that the provider organization has the result, or that
  anyone has reviewed anything.
* `FAILED` — terminal and unresolved. A row an operator has to look at, not a deletion.

There is deliberately no `DEAD_LETTER`: `connect/delivery.py` needs one because `FAILED` there is a
per-attempt fact; here `FAILED` *is* the terminal state, and a synonym invites code to check one
and not the other.

---

## Why the release message has no link

The raw `PatientAccessGrant` secret exists exactly once, in the issuance response, and DHealth can
never recover it (`exchange/patient_access.py`; ARCHITECTURE §19: "No raw-token recovery, ever").
A message composed at release time therefore *cannot* carry a working link without either storing
the secret or re-issuing the grant — the first forbidden, the second fatal to the printed QR already
in the patient's hands.

So the link travels at issuance and only at issuance:

```
ISSUANCE (raw secret in memory)          RELEASE / AMENDMENT (secret gone)
  grant + enrollment + obligation          obligation committed with the release
  COMMIT                                   COMMIT
    ↓                                        ↓
  compose URL, send inline                 dispatcher claims it later
  "Voici votre lien sécurisé : <link>"     "Une mise à jour ... est disponible.
                                            Consultez le lien sécurisé qui vous
                                            a été transmis, ou contactez votre
                                            établissement de soins."
```

That asymmetry is enforced by `DISPATCHABLE_EVENT_TYPES`, which excludes `ACCESS_LINK_ISSUED`: a
background worker can never claim the one message that needs a URL it could not reconstruct. An
access-link send that fails is **terminal** — the recovery path is revoke and reissue, and the UI
says exactly that rather than offering a retry that would send a message with no link in it.

---

## What a message may contain

`notify/templates.py` holds every string that can ever be sent. It is the content policy, not a
guideline about it: the release and amendment templates contain **no substitution slot at all**, so
there is no field through which a value, unit, reference range, interpretation, follow-up
disposition, test name, diagnosis, date of birth, identifier or clinician name could reach a
handset. The only variable anywhere in the module is `patient_access_url`, in the one template sent
while the secret is in memory.

Release and amendment messages are **textually identical**, and that is the decision rather than an
oversight. "Your earlier result has been corrected" tells whoever is holding the phone that a
correction happened to a named person — a clinical fact, on a lock screen, in a household. Lock
screens and shared phones are the assumed reading environment.

French is first-class; English exists beside it. No other locale is invented here.

---

## Consent, and what is never inferred from it

A patient contact number is not permission to send. Enrolment requires, in one deliberate gesture:
a destination, a channel, and an explicit affirmation that the patient agreed to be messaged at
that number. `patient_consent_confirmed` is `Literal[True]` in the API schema and `true` in the
TypeScript type, so a request that does not affirm it cannot be constructed and is a 422 if sent
anyway.

What is persisted is the minimum operational fact: **when**, and **which `ExchangeIdentity`**
affirmed it. There is no free-text note column, because a free-text consent field is where
jurisdiction-specific legal language gets invented by whoever is typing, and this ticket invents
none.

Consent is never inferred from registration, from having a phone number, from having scanned a QR,
or from having previously received a message. Revoking patient access withdraws the enrolment in the
same transaction — the link is dead, so telling the patient to use it would be pointing them at
nothing. Messages *already* owed stay owed: the patient is still entitled to learn there is an
update, and their facility is the fallback the message already names.

---

## Destinations

`phonenumbers` (libphonenumber), never string manipulation, and **no country guessing**. With no
`DHEALTH_NOTIFICATION_DEFAULT_REGION` configured — the default — a number without a country code is
refused rather than assumed to be local. Guessing from the server's timezone would mean the same
typed digits resolve to different real people depending on where the container runs. Djibouti is not
hard-coded anywhere in the notification path; the region is a configuration seam that is empty.

Validation happens at the API boundary, before the grant is issued, so a typo costs a clinician a
corrected digit rather than a live grant they must now revoke.

---

## Durability

The obligation commits **in the release's own transaction**. A result released and the patient not
owed a message are two facts that must never be able to disagree, and enqueueing afterwards is
exactly the window where they can. The provider call happens afterwards, in the dispatcher's own
transaction: no clinical release is ever held open across vendor network I/O, and a messaging vendor
can never roll back a release.

Idempotency is a UNIQUE index on a key minted from the immutable `LabResultReleaseVersion` — not an
application-level "check then insert". Keyed on the *order* instead, an amendment would look like a
replay of the release it corrects and be silently dropped, and the patient would never learn the
result had changed. That is the single worst outcome this domain has.

The dispatcher claims a bounded batch under a lease with `SELECT ... FOR UPDATE SKIP LOCKED`, one
transaction per message so a poisoned row cannot block the queue, with bounded exponential retry
(six attempts over roughly half an hour) and then a `FAILED` row an operator is told about. A worker
that dies holding work blocks it for one lease, not forever.

**Tenancy:** one dispatcher drains the whole deployment, across facilities. The tenant boundary is
on the surfaces where a principal exists — enrolment and the status/retry reads are gated on the
owning provider's `CLINICIAN` — and the dispatcher has no principal: it acts on the deployment's own
committed obligations, and the row already carries everything it needs.

---

## The webhook

A delivery receipt is the only way `DELIVERED` is ever written, and
`/notifications/v1/meta/webhook` is the only place one is accepted — published at the public edge
as `https://exchange.<BASE_DOMAIN>/notifications/v1/meta/webhook`, and that one exact path rather
than the `/notifications/v1/*` namespace, so a notification route added later does not become
public by omission (DH-NOTIFY-META-001R; before it, the proxy routed neither). Two routes, because
Meta's registration handshake and its event delivery are different things. `GET` is the
subscription handshake: a constant-time verify-token comparison, then the raw `hub.challenge`
echoed back as `text/plain`. `POST` carries the events, and its authority is an **HMAC-SHA256
signature over the raw request body**, keyed with the Meta app secret and validated in constant
time *before* anything is parsed. A missing signature is refused exactly like a wrong one.

**The body is bounded while it arrives, at 256 KiB.** This route is the one unauthenticated POST
surface DHealth has, and the signature cannot be checked until there are bytes to check it against,
so the bytes are counted as they come rather than measured once they have all arrived: DHealth reads
the request incrementally, refuses at the bound, and never accumulates more than it. Exactly 256 KiB
is legal; one byte more is a 413 that never reaches JSON parsing or touches a notification row.
Caddy applies the same limit on the callback route one hop earlier, so an oversized request from the
internet need not reach the application at all — defence in depth, not the control.
**[Corrected 2026-09-25, DH-NOTIFY-META-001R2.]** Until then the route called `request.body()` and
checked the length afterwards: the limit was real but it was applied *after* asking for everything
the caller chose to send, which is not what the code's own comment claimed. Note what is still not
claimed: nothing here bounds what uvicorn, Caddy or the kernel may hold — a single chunk can arrive
larger than the whole limit. The property is that **DHealth never asks for the whole body and never
accumulates more than the limit it advertises**.

Resolution is by `wamid` and nothing else. The WABA id, the `metadata.phone_number_id`, the
`recipient_id` and any organization, order or notification id a payload carries are never read as
authority over which row is touched. Receipts are idempotent and monotonic, and defended twice:
the webhook treats `sent` as a no-op and `apply_delivery_receipt` independently refuses to move a
terminal row, so both guards would have to be removed for a replayed or out-of-order callback to
walk a row backwards. `read` is ignored entirely — a chat thread being open is not
acknowledgement, comprehension or review. Inbound `messages[]` are ignored too: patient chat is a
non-goal, and a handler that parsed them would be the first half of an inbox nobody decided to
build.

It can change a notification's state and nothing else. It imports no clinical model, so it cannot
reach `LabOrder`, a release version, `DoctorReview` or `ResultDelivery` — §15's requirement enforced
by the import list rather than by review.

---

## Configuration

Off by default, and the default is the decision. Three operationally distinct states:

| state | meaning |
|---|---|
| **disabled** | `DHEALTH_NOTIFICATIONS_ENABLED=false`. Nothing is owed and nothing is wrong. Every test run and development stack. |
| **enabled and configured** | messages flow |
| **enabled but incomplete** | a **startup refusal naming the missing variable** — never a service that boots green and silently sends nothing |

`.env.example` documents the full set. `DHEALTH_PATIENT_PUBLIC_BASE_URL` must be `https://`: a
patient access URL carries a live bearer credential in its fragment, and over `http://` that
fragment is exposed before TLS would protect it.

`META_GRAPH_API_VERSION` is **required with no default** and shape-validated. DHealth has
qualified no Graph API version, so there is nothing a default could honestly claim, and the
version is the single value most likely to be silently wrong after being pasted from a stale
example. It is surfaced in the operator read and the CLI `status` output, because an operator
who cannot see which API a deployment talks to would otherwise have to read the container's
environment — which is also where the access token lives.

Template names are configuration too, for the same reason: Meta approves templates per WhatsApp
Business Account, so DHealth never invents one. The server owns the event → template mapping and
`NotificationTransport.send` has no `template_name` parameter at all.

Running the dispatcher:

```
docker compose --profile notify up -d patient-notifier

python -m daher_adapter.notify.cli run        # poll until interrupted
python -m daher_adapter.notify.cli run --once
python -m daher_adapter.notify.cli status     # queue state; contacts nothing
```

**There is no destructive subcommand, and none may be added without a Human Decision**
(`AGENTS.md`). No `purge`, no `reset`, no `force-send`. A `PENDING` row is a message DHealth owes a
patient about a released clinical result, and a `purge` on a stuck queue deletes exactly that —
reached for at precisely the moment a stuck queue makes somebody want to try anything.

Operators answer "is anything backing up, is Meta rejecting them, did credentials stop working,
how old is the oldest pending message, which Graph API version is this deployment on, which
channels can it actually send on" from `GET /exchange/v1/notifications/operations` (platform
admin). It carries counts, one age in seconds, a failure-category histogram, the pinned Graph API
version and the available channels — no phone number, no order, no patient, no message id, no
body and no credential.

---

## What this does not make true

* **No real Meta endpoint has ever been contacted.** Every test runs against an in-process fake or
  a stub HTTP client with no network code at all. ADR-003's gate is unchanged: WhatsApp stays
  **BLOCKED**, not DONE, until real credentials have run the flow to a real handset. A passing
  fake is not a completion, and neither is a green qualification run that never reached a phone.
* **The real-provider qualification path exists and has never been run.** See
  [`META-WHATSAPP-RUNBOOK.md`](META-WHATSAPP-RUNBOOK.md); its own evidence artifact records
  whether it ran, and its delivery-receipt leg is manual because a CI runner has no public URL
  for Meta to call back.
* **Meta's primary documentation was never read** from the implementing environment. The wire
  shapes are corroborated from secondary sources, not verified against Meta's own current text.
* **SMS has no provider.** The channel exists in the domain and nothing can send on it. The
  server tells the UI which channels are available rather than the UI assuming.
* **`read` is not recorded.** Meta reports it; DHealth ignores it. A chat thread being open is
  not acknowledgement, comprehension, review or follow-up.
* **Pilot gate 5 is not cleared.**
* **No push notification exists for anything but a release or an amendment.** Not order creation,
  not acceptance, not collection, not processing.
* **Notification is optional and must stay optional** (Law 4, `MVP-1.0.md` §6). A patient with no
  phone, no smartphone, no WhatsApp, no data plan or no confidence with digital interfaces still
  completes the journey through the QR code, the printed sheet and their clinic. Nothing in order
  creation, laboratory work, result release, clinician review or patient follow-up requires a
  notification to have been sent, and the staff UI offers the QR-only path in one gesture without
  routing through a form asking for a number the patient may not have.
* **No email, no voice, no patient accounts, no OTP, no proxy authorization, no bidirectional
  messaging, no reminders, no bulk messaging** — every one a `§28` non-goal, and absent rather than
  stubbed.
