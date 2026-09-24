"""DH-NOTIFY-001 §3/§16 — every string DHealth can ever send to a patient.

**This module is the sensitive-content policy.** Not a guideline applied by reviewers, not a
convention: the enumerated set below is the complete range of the transport, `render()` is the only
way to produce a body, and `tests/test_patient_notification_content.py` asserts that nothing else
can reach a handset.

**The message is not the result.** It is an invitation to return to a path the patient already
holds. It never names the test, the value, the unit, the reference range, the interpretation, the
follow-up disposition, the clinician, the laboratory, the diagnosis, or the patient. §3's list of
forbidden content is enforced structurally rather than by inspection: **no template below has a
substitution slot capable of carrying any of it.** The only variable that exists anywhere in this
module is ``patient_access_url``, and it appears in exactly one template.

Lock screens and shared phones are the assumed reading environment (§3). Someone other than the
patient will see these messages. Every one of them is written so that this is harmless -- a person
who reads the whole body learns that somebody has a laboratory order, which they already knew if
they are holding that person's phone, and nothing clinical whatsoever.

**Why the release message carries no link (Liban's A′ decision, DH-NOTIFY-001).** The raw
``PatientAccessGrant`` secret exists only in the issuance response and DHealth can never recover it
(``exchange/patient_access.py``; ``docs/ARCHITECTURE.md`` §19: "No raw-token recovery, ever"). A
message composed at release time therefore *cannot* contain a working link without either
persisting the secret or re-issuing the grant -- the first forbidden by §4/§8, the second would
kill the printed QR already in the patient's hands and breach Law 4. So the update message points
at the link the patient was already given, and at their facility, which is a real fallback rather
than a hedge: §19 and Law 4 require the non-digital path to work anyway.

French is first-class (``AGENTS.md``), so ``fr`` is the default and the one every deployment uses
today. English exists beside it because §16 asks for it; no other locale is invented here, and
adding one is a translation decision with a named owner, not a code change an agent makes alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from daher_adapter.notify.models import NotificationEventType

#: Bumped when the *wording* of any template below changes in a way that matters operationally.
#: Stored on every notification row, so "what exactly did we send that patient in October?" is
#: answerable from the row plus this file's history -- which is the whole reason the rendered body
#: is not persisted (§8).
TEMPLATE_VERSION = 1

DEFAULT_LOCALE = "fr"
SUPPORTED_LOCALES = ("fr", "en")


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    """A body, and the identity of the template that produced it.

    The body is returned, never stored. The caller hands it to a transport and drops it; the two
    identity fields are what land in the database.
    """

    template_id: str
    template_version: int
    locale: str
    body: str


#: ``{patient_access_url}`` is the **only** substitution slot in this module, and it exists in
#: exactly one template -- the one sent inline by the issuing request, while the secret is still in
#: memory. Grep for it: if it ever appears in a second entry, a release-time sender would have
#: acquired a reason to want a stored token, which is the failure this whole design avoids.
_TEMPLATES: dict[tuple[NotificationEventType, str], tuple[str, str]] = {
    (NotificationEventType.ACCESS_LINK_ISSUED, "fr"): (
        "patient_access_link",
        "DHealth\n"
        "\n"
        "Voici votre lien sécurisé pour suivre votre demande d'analyses :\n"
        "{patient_access_url}\n"
        "\n"
        "Conservez ce message. Vous pouvez aussi contacter votre établissement de soins.",
    ),
    (NotificationEventType.ACCESS_LINK_ISSUED, "en"): (
        "patient_access_link",
        "DHealth\n"
        "\n"
        "Here is your secure link to follow your laboratory order:\n"
        "{patient_access_url}\n"
        "\n"
        "Keep this message. You can also contact your healthcare facility.",
    ),
    # No link, deliberately, and no slot that could carry one. See the module docstring.
    (NotificationEventType.RESULT_RELEASED, "fr"): (
        "patient_result_update",
        "DHealth\n"
        "\n"
        "Une mise à jour concernant votre demande d'analyses est disponible.\n"
        "\n"
        "Consultez le lien sécurisé qui vous a été transmis, ou contactez votre établissement "
        "de soins.",
    ),
    (NotificationEventType.RESULT_RELEASED, "en"): (
        "patient_result_update",
        "DHealth\n"
        "\n"
        "There is an update to your laboratory order.\n"
        "\n"
        "Use the secure link you were given, or contact your healthcare facility.",
    ),
    # Identical wording to the release message, and that is the decision rather than laziness.
    # "Your earlier result has been corrected" tells a person holding somebody else's phone that a
    # correction happened, which is a clinical fact about a named patient. The patient learns what
    # changed from the authoritative status page, which is the only surface allowed to say it.
    (NotificationEventType.AMENDMENT_RELEASED, "fr"): (
        "patient_result_update",
        "DHealth\n"
        "\n"
        "Une mise à jour concernant votre demande d'analyses est disponible.\n"
        "\n"
        "Consultez le lien sécurisé qui vous a été transmis, ou contactez votre établissement "
        "de soins.",
    ),
    (NotificationEventType.AMENDMENT_RELEASED, "en"): (
        "patient_result_update",
        "DHealth\n"
        "\n"
        "There is an update to your laboratory order.\n"
        "\n"
        "Use the secure link you were given, or contact your healthcare facility.",
    ),
}


class UnsupportedTemplate(LookupError):
    """No controlled template exists for this (event, locale).

    Raised rather than falling back to a nearby locale or to a generic string. A fallback is how an
    untranslated message reaches a patient in a language they do not read, and how a new event type
    silently acquires wording nobody wrote.
    """


@dataclass(frozen=True, slots=True)
class MetaTemplateBinding:
    """DH-NOTIFY-META-001 §8 — which approved Meta template an event maps to, and what it carries.

    **The server owns this mapping, and that is the whole point.** A caller-supplied template name
    would let any code path name any approved template, which is how a message intended as "there
    is an update" becomes one that says something else. ``NotificationTransport.send`` therefore has
    no ``template_name`` parameter at all, and a test asserts its absence.

    ``config_key`` names the environment variable holding the *approved* template's name as
    registered in the WhatsApp Business Account. DHealth does not invent template names: Meta
    approves them per WABA, so the name is deployment configuration and there is no default.

    ``carries_access_url`` is ADR-058's A′ expressed as data. Exactly one binding has it true, and
    it is the one sent while the raw grant secret is still in the issuing request's memory. Every
    other binding takes **zero parameters** — not "no clinical parameters", none at all — so the
    update message has no substitution slot a value could ever be threaded through. §9 asks for
    "preferably no dynamic clinical substitution fields"; zero is the strongest form of that, and it
    is what makes ``NC-META-4`` a one-line change that a payload-level test catches.
    """

    config_key: str
    carries_access_url: bool


#: Event → approved-template binding. Closed, server-owned, and deliberately tiny.
_META_BINDINGS: dict[NotificationEventType, MetaTemplateBinding] = {
    NotificationEventType.ACCESS_LINK_ISSUED: MetaTemplateBinding(
        config_key="META_TEMPLATE_PATIENT_ACCESS_FR", carries_access_url=True
    ),
    NotificationEventType.RESULT_RELEASED: MetaTemplateBinding(
        config_key="META_TEMPLATE_PATIENT_UPDATE_FR", carries_access_url=False
    ),
    # The same template as a release, for the reason the wording is identical: telling somebody
    # holding another person's phone that a *correction* happened is itself a clinical fact.
    NotificationEventType.AMENDMENT_RELEASED: MetaTemplateBinding(
        config_key="META_TEMPLATE_PATIENT_UPDATE_FR", carries_access_url=False
    ),
}


def meta_binding(event_type: NotificationEventType) -> MetaTemplateBinding:
    """The approved-template binding for this event. Raises rather than guessing.

    A new event type with no binding must fail loudly here rather than fall back to some nearby
    template — a fallback is how an unreviewed message reaches a patient under an approved name.
    """
    binding = _META_BINDINGS.get(event_type)
    if binding is None:
        raise UnsupportedTemplate(f"no approved Meta template binding for {event_type.value}")
    return binding


def template_identity(
    event_type: NotificationEventType, locale: str = DEFAULT_LOCALE
) -> tuple[str, int]:
    """The ``(template_id, template_version)`` that will be stored for this event.

    Resolved at obligation-creation time so the row records the template that was *current when the
    obligation was made*, and a later wording change is visible as a version difference between old
    and new rows rather than silently rewriting history.
    """
    key = (event_type, locale)
    if key not in _TEMPLATES:
        raise UnsupportedTemplate(f"no template for {event_type.value} in locale {locale!r}")
    return _TEMPLATES[key][0], TEMPLATE_VERSION


def render(
    event_type: NotificationEventType,
    *,
    locale: str = DEFAULT_LOCALE,
    patient_access_url: str | None = None,
) -> RenderedMessage:
    """Produce the exact bytes a transport will send.

    ``patient_access_url`` is accepted only by the one template that has a slot for it, and is
    **required** there: a link message whose link failed to render would be a message telling a
    patient to follow nothing. Passing a URL for any other event type is a programming error and
    raises, rather than being ignored -- silently dropping it is how a caller comes to believe a
    release message contains a link when it does not.
    """
    key = (event_type, locale)
    if key not in _TEMPLATES:
        raise UnsupportedTemplate(f"no template for {event_type.value} in locale {locale!r}")
    template_id, body = _TEMPLATES[key]

    carries_link = "{patient_access_url}" in body
    if carries_link:
        if not patient_access_url:
            raise ValueError(
                f"{event_type.value} requires a patient access URL and none was supplied"
            )
        body = body.replace("{patient_access_url}", patient_access_url)
    elif patient_access_url is not None:
        raise ValueError(
            f"{event_type.value} has no link slot; refusing to discard a supplied access URL "
            "rather than let a caller believe one was sent"
        )

    return RenderedMessage(
        template_id=template_id,
        template_version=TEMPLATE_VERSION,
        locale=locale,
        body=body,
    )
