"""DH-NOTIFY-META-001 — the Meta WhatsApp Cloud API transport. The only file that knows Meta.

WhatsApp now goes straight to Meta rather than through Twilio:

    DHealth → Meta WhatsApp Cloud API → WhatsApp → patient

ADR-002 permitted "the WhatsApp Cloud API (or Twilio) directly" and DH-NOTIFY-001 took the Twilio
branch because it covered both channels behind one credential. ADR-059 takes the other branch for
WhatsApp specifically. Everything above ``NotificationTransport`` is unchanged: the obligation, its
idempotency, the retry budget, the claim lease, the consent record, the audit. Only the wire is new.

**A source-availability limitation, stated because it shaped the design.**
``developers.facebook.com`` is unreachable from the sandbox this was written in (every request fails
at the connection), so the primary documentation could not be read. The request/response and webhook
shapes below are
corroborated across several independent secondary sources and match this repository's prior
knowledge, but they are **not** verified against Meta's own current text. The design response is
structural rather than a disclaimer:

* **No Graph API version is hard-coded, and there is no default.** ``META_GRAPH_API_VERSION`` is
  required configuration, shape-validated, and surfaced to operators. A guessed constant would be
  the single most likely thing to be silently wrong.
* **No template name is hard-coded.** Meta approves templates per WABA; the names are configuration.
* **HTTP status is the load-bearing failure classification**, and the Graph ``error.code`` table
  below is a *refinement* over it, kept deliberately short. An unrecognised code changes nothing.

The first task of real qualification (``docs/META-WHATSAPP-RUNBOOK.md``) is to confirm these shapes
against the live API. Until that has happened, nothing here may be described as qualified.

**Credentials live in the environment and in this object's memory, nowhere else.** Never logged,
never in an exception message, never written to a notification or attempt row. The access token is a
bearer credential for the whole WhatsApp Business Account; a single leaked log line is a takeover.

**Idempotency, stated honestly.** The Cloud API has no request idempotency key. A request that times
out after Meta accepted it will, if retried, send a second message. This adapter therefore claims
at-least-once with a bounded budget, never exactly-once: a timeout maps to ``TRANSIENT_PROVIDER``
and the attempt is counted at hand-over, so a message whose fate is unknown spends its budget and
reaches ``FAILED`` rather than being retried forever. The user-visible consequence is bounded and
non-clinical -- a patient may receive the same content-free "there is an update" twice.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationEventType,
    NotificationFailureCategory,
)
from daher_adapter.notify.templates import meta_binding
from daher_adapter.notify.transport import TransportAccepted, TransportFailure

# ``httpx2`` is the distribution this environment pins (httpx 2.x ships under that name); the
# fallback keeps the module importable where the 1.x name is installed instead. Same shape
# ``connect_agent/client.py`` already uses, so both resolve the same library.
try:  # pragma: no cover - one branch runs per environment
    import httpx2 as httpx
except ModuleNotFoundError:  # pragma: no cover
    import httpx

logger = logging.getLogger("daher_adapter.notify.meta")

GRAPH_API_ROOT = "https://graph.facebook.com"

#: ``vNN.N``. Validated rather than trusted because the version is the one value most likely to be
#: pasted from a stale example, and an unversioned or malformed path silently changes which API a
#: deployment is talking to. There is deliberately **no default**: §3 says pin and document the
#: version DHealth has actually qualified, and DHealth has qualified none.
GRAPH_API_VERSION_PATTERN = re.compile(r"^v\d+\.\d+$")

#: Short enough that a hung provider does not hold a dispatcher worker through a whole poll cycle;
#: long enough for an ordinary international send.
DEFAULT_TIMEOUT_SECONDS = 15.0

#: Graph API error codes whose classification genuinely changes behaviour. **Secondary-sourced and
#: unverified against Meta's own current documentation** (see the module docstring), which is why
#: this table is short and why the HTTP-status mapping below is the load-bearing path: an
#: unrecognised code simply falls through to it rather than being guessed at.
_META_ERROR_CODES: dict[int, NotificationFailureCategory] = {
    # Invalid/expired OAuth access token.
    190: NotificationFailureCategory.AUTHENTICATION,
    # Application-level throttling.
    4: NotificationFailureCategory.RATE_LIMIT,
    80007: NotificationFailureCategory.RATE_LIMIT,
    131056: NotificationFailureCategory.RATE_LIMIT,
    # Malformed request -- a DHealth bug or a configuration mismatch, never worth retrying.
    100: NotificationFailureCategory.CONFIGURATION,
    # Recipient cannot be reached / is not a WhatsApp user.
    131026: NotificationFailureCategory.INVALID_DESTINATION,
    # Re-engagement window: a non-template message outside 24h. Should be impossible here (every
    # message DHealth sends is a template) so it is a configuration fault if it ever appears.
    131047: NotificationFailureCategory.CONFIGURATION,
    # Template does not exist, is not approved, or its parameter count does not match.
    132000: NotificationFailureCategory.CONFIGURATION,
    132001: NotificationFailureCategory.CONFIGURATION,
    132005: NotificationFailureCategory.CONFIGURATION,
    132007: NotificationFailureCategory.CONFIGURATION,
    132012: NotificationFailureCategory.CONFIGURATION,
    132015: NotificationFailureCategory.CONFIGURATION,
}

#: Meta's message-status values, as reported on the ``statuses`` webhook. ``read`` is
#: deliberately absent from both sets -- see ``webhook.py`` and §13: it is not a clinical fact, and
#: DHealth's state machine gains nothing from it.
META_DELIVERED_STATUSES = frozenset({"delivered"})
META_FAILED_STATUSES = frozenset({"failed"})


class MetaWhatsAppTransport:
    """``NotificationTransport`` over the WhatsApp Cloud API.

    WhatsApp only. It answers ``supports(SMS)`` with ``False`` and refuses an SMS send outright,
    because Meta is not an SMS provider and a transport that silently accepted one would make the
    staff UI offer a channel nothing can deliver (§6).

    The HTTP client is injectable for the reason ``connect_agent/client.py`` gives: a test drives
    the real adapter against a stub, so a failing test means this code is wrong rather than that a
    mock drifted from it.
    """

    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        graph_api_version: str,
        template_names: dict[str, str],
        template_language: str,
        business_account_id: str | None = None,
        client: Any | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        api_root: str = GRAPH_API_ROOT,
    ) -> None:
        if not access_token:
            raise ValueError("Meta WhatsApp requires an access token")
        if not phone_number_id:
            raise ValueError("Meta WhatsApp requires a business phone number id")
        if not GRAPH_API_VERSION_PATTERN.fullmatch(graph_api_version or ""):
            raise ValueError(
                "META_GRAPH_API_VERSION must look like 'v23.0' -- an explicit, pinned Graph API "
                "version DHealth has qualified against; there is no default"
            )
        if not template_language:
            raise ValueError("Meta WhatsApp requires the approved template language code")
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._version = graph_api_version
        self._template_names = dict(template_names)
        self._template_language = template_language
        self._business_account_id = business_account_id
        self._timeout = timeout_seconds
        self._api_root = api_root.rstrip("/")
        self._client = client

    @property
    def provider_name(self) -> str:
        return "meta_whatsapp"

    @property
    def graph_api_version(self) -> str:
        """Surfaced to operators (§3) through the operations read and the CLI. Not a secret."""
        return self._version

    def supports(self, channel: NotificationChannel) -> bool:
        return channel is NotificationChannel.WHATSAPP

    def send(
        self,
        *,
        channel: NotificationChannel,
        destination: str,
        event_type: NotificationEventType,
        locale: str,
        patient_access_url: str | None = None,
    ) -> TransportAccepted:
        if channel is not NotificationChannel.WHATSAPP:
            # Configuration, and terminal: Meta is not an SMS provider and never will be.
            raise TransportFailure(
                NotificationFailureCategory.CONFIGURATION,
                f"the Meta WhatsApp transport cannot send on the {channel.value} channel",
            )

        payload = self._build_payload(
            destination=destination,
            event_type=event_type,
            patient_access_url=patient_access_url,
        )
        url = f"{self._api_root}/{self._version}/{self._phone_number_id}/messages"
        try:
            response = self._post(url, payload)
        except Exception as error:  # noqa: BLE001 - every transport error is classified, not raised
            # Deliberately broad: httpx raises a family of connection/timeout/protocol errors whose
            # classification is identical. Only the exception *type name* is kept -- a message could
            # carry the URL, and for an access-link send the payload carries a live credential.
            raise TransportFailure(
                NotificationFailureCategory.TRANSIENT_PROVIDER,
                f"provider request failed ({type(error).__name__})",
            ) from None

        return self._interpret(response)

    # -- payload ---------------------------------------------------------------------------------

    def _build_payload(
        self,
        *,
        destination: str,
        event_type: NotificationEventType,
        patient_access_url: str | None,
    ) -> dict[str, Any]:
        """The Cloud API template-message body.

        **The binding decides what may be carried, not the caller** (§8). ``meta_binding`` is a
        closed server-owned map; a URL supplied for an event whose binding does not carry one is
        refused here rather than quietly dropped, and an event whose binding *does* carry one but
        which was given no URL is refused too -- a link message with no link is worse than none.

        The update binding takes **zero parameters**, so the payload for a release or an amendment
        has no ``components`` key at all. That is the strongest form of §9's content boundary: there
        is no parameter array into which a value could ever be threaded.
        """
        binding = meta_binding(event_type)
        template_name = self._template_names.get(binding.config_key)
        if not template_name:
            raise TransportFailure(
                NotificationFailureCategory.CONFIGURATION,
                f"no approved template name is configured for {binding.config_key}",
            )

        if binding.carries_access_url:
            if not patient_access_url:
                raise TransportFailure(
                    NotificationFailureCategory.CONFIGURATION,
                    f"{event_type.value} requires a patient access URL and none was supplied",
                )
            components: list[dict[str, Any]] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": patient_access_url}],
                }
            ]
        else:
            if patient_access_url is not None:
                raise TransportFailure(
                    NotificationFailureCategory.CONFIGURATION,
                    f"{event_type.value} must not carry a patient access URL",
                )
            components = []

        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": self._template_language},
        }
        if components:
            template["components"] = components

        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": _wire_number(destination),
            "type": "template",
            "template": template,
        }

    # -- transport -------------------------------------------------------------------------------

    def _post(self, url: str, payload: dict[str, Any]) -> Any:
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        if self._client is not None:
            return self._client.post(url, json=payload, headers=headers, timeout=self._timeout)
        with httpx.Client(timeout=self._timeout) as client:  # pragma: no cover - real network
            return client.post(url, json=payload, headers=headers)

    def _interpret(self, response: Any) -> TransportAccepted:
        status_code = int(getattr(response, "status_code", 0))
        if 200 <= status_code < 300:
            body = _json_or_empty(response)
            messages = body.get("messages")
            message_id = None
            if isinstance(messages, list) and messages and isinstance(messages[0], dict):
                message_id = messages[0].get("id")
            if not message_id:
                # A 2xx this adapter does not understand. Refusing is correct: recording SENT with
                # no provider id would produce a row no delivery receipt could ever resolve back to,
                # and the webhook's only correlation key is that id.
                raise TransportFailure(
                    NotificationFailureCategory.UNKNOWN,
                    "provider accepted the request but returned no message identifier",
                    http_status_code=status_code,
                )
            return TransportAccepted(
                provider_message_id=str(message_id),
                # Meta does not return a per-message status on the send response; acceptance is what
                # a 2xx means and "accepted" is DHealth's own word for it. Never read as delivery.
                provider_status="accepted",
                http_status_code=status_code,
            )

        raise TransportFailure(
            self._classify(response, status_code),
            _sanitize(status_code),
            http_status_code=status_code,
        )

    def _classify(self, response: Any, status_code: int) -> NotificationFailureCategory:
        """Graph error code as a refinement; HTTP status as the answer.

        The order is deliberate and is the honest response to not having been able to read Meta's
        current error reference: a code this adapter recognises is used, and everything else falls
        through to a status mapping that cannot be wrong about the one thing that matters -- whether
        another attempt could plausibly succeed.
        """
        body = _json_or_empty(response)
        error = body.get("error")
        if isinstance(error, dict):
            raw_code = error.get("code")
            if isinstance(raw_code, int | str):
                try:
                    mapped = _META_ERROR_CODES.get(int(raw_code))
                except (TypeError, ValueError):
                    mapped = None
                if mapped is not None:
                    return mapped

        if status_code in (401, 403):
            return NotificationFailureCategory.AUTHENTICATION
        if status_code == 429:
            return NotificationFailureCategory.RATE_LIMIT
        if status_code == 404:
            return NotificationFailureCategory.CONFIGURATION
        if 500 <= status_code < 600:
            return NotificationFailureCategory.TRANSIENT_PROVIDER
        if 400 <= status_code < 500:
            # A 4xx with no recognised code. PROVIDER_REJECTED rather than UNKNOWN because the one
            # behavioural consequence -- do not retry -- is certainly right for a client error.
            return NotificationFailureCategory.PROVIDER_REJECTED
        return NotificationFailureCategory.UNKNOWN


def _wire_number(destination: str) -> str:
    """Meta's recipient form. DHealth's canonical representation is unchanged (§19).

    The domain stores and validates E.164 with the leading ``+`` (``phone.py``, libphonenumber).
    The Cloud API is documented as accepting the number with or without it; the digits-only form is
    what its examples use, so that is what goes on the wire. The translation lives here and nowhere
    else -- a wire format leaking into the domain would make two representations of one fact.
    """
    return destination.lstrip("+")


def _json_or_empty(response: Any) -> dict[str, Any]:
    try:
        parsed = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body is itself information-free here
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize(status_code: int) -> str:
    """The recorded failure detail: DHealth's own sentence, carrying only the status number.

    Never Meta's ``error.message``. Those quote the offending parameter, which for an access-link
    send is the patient's live bearer URL, and they carry an ``fbtrace_id`` that is useful only in a
    support ticket -- not worth the risk of writing a provider string into the database, the attempt
    history and every log that touches the row.
    """
    return f"provider rejected the request with HTTP {status_code}"
