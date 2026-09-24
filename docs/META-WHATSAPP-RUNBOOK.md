# Meta WhatsApp — what an operator configures, and what "qualified" requires

**DH-NOTIFY-META-001, ADR-059.** WhatsApp goes directly to Meta's Cloud API. This document is the
operator's side of that: what must exist in Meta before DHealth can send anything, what goes in
`.env`, and what has to actually happen before anyone may write the word *qualified*.

> **Nothing here has been done.** No Meta Business Portfolio, no WhatsApp Business Account, no
> approved template, no credential, no message, no handset. ADR-003's gate is **BLOCKED** and pilot
> gate 5 is uncleared. This is a runbook for work that has not started, not a record of work done.

Read [`PATIENT-NOTIFICATION.md`](PATIENT-NOTIFICATION.md) first — it defines what a message may
contain and why the release message carries no link.

---

## A limitation to know before you start

`developers.facebook.com` was unreachable from the environment this was implemented in, so **Meta's
current primary documentation was never read**. The request shape, webhook payload shape, signature
scheme and subscription handshake DHealth implements are corroborated from several independent
secondary sources, and every value that might drift — the Graph API version, the template names, the
template language — is configuration with no default rather than a constant somebody guessed.

**Your first qualification task is therefore to confirm the shapes against the live API**, not to
assume they are right. If Meta's console or documentation disagrees with anything below, Meta is
correct and this file is the thing to fix.

---

## The chain to configure

```
   Meta Business Portfolio
            ↓
   WhatsApp Business Account (WABA)          → META_WHATSAPP_BUSINESS_ACCOUNT_ID
            ↓
   business phone number                      → META_WHATSAPP_PHONE_NUMBER_ID
            ↓
   Meta app (System User + permanent token)   → META_WHATSAPP_ACCESS_TOKEN, META_APP_SECRET
            ↓
   two approved message templates             → META_TEMPLATE_PATIENT_*_FR, META_TEMPLATE_LANGUAGE
            ↓
   webhook callback URL + verify token        → META_WEBHOOK_VERIFY_TOKEN
            ↓
   WABA subscription to the `messages` field
```

DHealth automates none of this, deliberately (§18): a business-account provisioning system is a
product of its own, and this deployment needs one WABA configured once by a person.

**Use a System User token, not a personal one.** A token tied to an individual's account dies when
they lose access, change password or leave — at which point patient notifications stop with an
`AUTHENTICATION` failure and nobody knows why.

**The access token is a bearer credential for the whole WABA.** It goes in the deployment's secret
store and in `.env` (gitignored from the first commit). It is never committed, never logged, never
written to a database row, an audit detail or a metric label — DHealth enforces that side; you
enforce the rest.

---

## The two templates

DHealth sends **only** these two, and the server owns which event uses which. A caller cannot name a
template; `NotificationTransport.send` has no such parameter, and a test asserts its absence.

### 1. Patient access — carries the one-time secure link

Config key: `META_TEMPLATE_PATIENT_ACCESS_FR`. One body parameter: the secure URL.

```
DHealth

Voici votre lien sécurisé pour suivre votre demande d'analyses :
{{1}}

Conservez ce message. Vous pouvez aussi contacter votre établissement de soins.
```

### 2. Patient update — carries nothing at all

Config key: `META_TEMPLATE_PATIENT_UPDATE_FR`. **Zero parameters.** Used for both a result release
and an amendment release, with identical wording — telling whoever is holding the phone that a
*correction* happened is itself a clinical fact about a named person.

```
DHealth

Une mise à jour concernant votre demande d'analyses est disponible.

Consultez le lien sécurisé qui vous a été transmis, ou contactez votre établissement de soins.
```

**Submit exactly this text.** It is the same wording that lives in
`adapter/src/daher_adapter/notify/templates.py`, which stays the canonical record of what DHealth
sends. If Meta's review requires an edit, **update `templates.py` to match before configuring the
template name** — otherwise the file and the real message diverge silently, and the file is what
every content test and every future reader trusts.

Categorise both as **Utility** (a transactional service update), not Marketing. Submit the French
versions and set `META_TEMPLATE_LANGUAGE` to the exact language code Meta approved (`fr`, or
`fr_FR` if that is what the console shows — a mismatch rejects every send).

**Do not add a parameter to the update template**, not even a facility name. Zero parameters is the
strongest form of the content boundary: there is no slot a result value could ever be threaded
through, and `NC-META-4` exists to prove a test notices if one appears.

---

## The webhook

DHealth exposes two routes under the configured public origin:

```
GET  https://<your-host>/notifications/v1/meta/webhook   ← subscription handshake
POST https://<your-host>/notifications/v1/meta/webhook   ← status events
```

Configure that URL in the Meta app's WhatsApp webhook settings, set the verify token to match
`META_WEBHOOK_VERIFY_TOKEN`, and **subscribe the WABA to the `messages` field** — status events
arrive on it, not on a separate one.

Meta will `GET` the URL with `hub.mode=subscribe`, `hub.verify_token` and `hub.challenge`. DHealth
compares the token in constant time and echoes the raw challenge as `text/plain`. If registration
fails, the usual cause is a JSON-encoded or quoted challenge body — DHealth returns it verbatim, so
check the reverse proxy is not rewriting it.

Every `POST` must carry `X-Hub-Signature-256`, an HMAC-SHA256 of the **raw** body keyed with
`META_APP_SECRET`. DHealth validates before parsing. Without both the app secret and the verify
token, the webhook refuses every request and notifications truthfully stop at `SENT`.

**Caddy must not buffer or rewrite the request body** on this route. A body that is decoded and
re-encoded is not byte-identical to what Meta signed, and every genuine callback would be refused.

---

## Configuration

See `.env.example` for the full annotated set. The ones that most often go wrong:

| Variable | Note |
|---|---|
| `META_GRAPH_API_VERSION` | **Required, no default.** `vNN.N`. Pin the version you have qualified. Shape-validated at startup and shown in the operator read. |
| `META_TEMPLATE_LANGUAGE` | Must match Meta's approved language code exactly. |
| `META_WHATSAPP_PHONE_NUMBER_ID` | The phone number's **ID**, not the number. |
| `DHEALTH_PATIENT_PUBLIC_BASE_URL` | Must be `https://`. Patient links carry a live bearer credential in the URL fragment. |

With sending enabled and anything missing, the adapter **refuses to start and names the variable**.
That is deliberate: "disabled" and "broken" must not look the same.

Check what a running deployment thinks it is configured with:

```
GET /exchange/v1/notifications/operations        # platform admin; shows provider, version, channels
python -m daher_adapter.notify.cli status        # same, from a shell; contacts nothing
```

Neither shows a credential.

---

## Qualification — what has to actually happen

```
[ ] Meta Business Portfolio and WABA created
[ ] business phone number added and verified
[ ] System User created, permanent access token issued
[ ] both templates submitted and APPROVED (not merely submitted)
[ ] webhook URL registered, verify token matched, WABA subscribed to `messages`
[ ] a non-patient test handset available and its number set as
    META_QUALIFICATION_TEST_DESTINATION
[ ] repository secrets populated
```

Then run the qualification — **manually, never on a push**:

```
Actions → "DH-NOTIFY-META-001 — real Meta WhatsApp qualification" → Run workflow
```

or locally against a disposable database:

```
python -m scripts.qualify_meta_whatsapp --evidence evidence.json --await-delivery 120
```

It builds a synthetic provider, lab, patient and order through the real repositories, issues patient
access with notification, releases a result, releases an amendment, and records each `wamid`. It
refuses to run without an explicit test destination, refuses a destination that is not E.164, and
refuses a database holding non-synthetic organizations.

**The delivery-receipt leg is manual, and this is the honest part.** Meta posts callbacks to a
public HTTPS URL; a GitHub-hosted runner has none without a tunnel. Run with `--await-delivery`
only against a deployment Meta can actually reach. Otherwise the run records `SENT` and stops, which
the evidence artifact states in `delivery_receipt_leg`.

### What a green run does not prove

A human must confirm on the handset, because no automated check can:

1. the first message contains a **working** secure link, and opening it loads DHealth Patient
   showing **status only**;
2. the second and third messages contain **no link** and **no result value, unit, range,
   interpretation or disposition** of any kind;
3. the original link still works after the amendment — A′ depends on it, and a patient whose
   printed QR died would discover it at the laboratory door.

Then deliberately break it: invalidate the access token, release another synthetic result, and
confirm the release still commits while the notification sits `PENDING`/`FAILED`; restore the token,
retry, and confirm it delivers. **That failure and recovery is part of acceptance, not an extra.**

### Only then

Record the outcome in `PROJECT_STATE.md` with wamids and timestamps, and only then may any document
say **REAL-META-WHATSAPP-QUALIFIED** and pilot gate 5 be treated as cleared. A green workflow run
against a fake provider, or a green run that never reached a handset, is not that.
