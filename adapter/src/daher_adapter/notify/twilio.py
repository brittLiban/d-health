"""DH-NOTIFY-001 §9/§10 — the Twilio adapter. The only file in this repository that knows Twilio.

Chosen per ADR-002, which already permitted "the WhatsApp Cloud API (or Twilio) directly", and
because Launch 1 needs **both** SMS and WhatsApp (§5) and Twilio is the one provider covering both
behind a single credential. Meta's WhatsApp Cloud API would be a second class implementing the same
``NotificationTransport`` protocol, with no change anywhere else -- see ``transport.py``.

**Credentials live in the environment and in this object's memory, nowhere else.** Never logged,
never in an exception message, never written to a notification or attempt row. ``_sanitize`` exists
because the single easiest way to leak an auth token is an error string that helpfully includes the
request.

**Idempotency, stated honestly** (§13). The Twilio Messages API has **no request idempotency key**.
A request that times out after Twilio accepted it will, if retried, send a second message. This
adapter therefore does not claim exactly-once, and neither does the dispatcher above it: what the
system actually provides is a durable at-least-once obligation with idempotent local processing and
a bounded retry budget. The user-visible consequence is bounded and non-clinical -- a patient may
receive the same content-free "there is an update" message twice -- and that is materially better
than the alternative failure, which is never sending it at all. The mitigation that *is* real:
``TIMEOUT`` maps to ``TRANSIENT_PROVIDER`` and the attempt is counted at hand-over, so a message
whose fate is unknown consumes its budget and reaches ``FAILED`` rather than being retried forever.
"""

from __future__ import annotations

import logging
from typing import Any

from daher_adapter.notify.models import NotificationChannel, NotificationFailureCategory
from daher_adapter.notify.transport import TransportAccepted, TransportFailure

# ``httpx2`` is the distribution this environment pins (httpx 2.x ships under that name); the
# fallback keeps the module importable where the 1.x name is installed instead. Same shape
# ``connect_agent/client.py`` already uses, so both resolve the same library.
try:  # pragma: no cover - one branch runs per environment
    import httpx2 as httpx
except ModuleNotFoundError:  # pragma: no cover
    import httpx

logger = logging.getLogger("daher_adapter.notify.twilio")

TWILIO_API_ROOT = "https://api.twilio.com/2010-04-01"

#: Short enough that a hung provider does not hold a dispatcher worker open through a whole poll
#: cycle; long enough for an ordinary international send. A timeout is always safe to *record* --
#: see the module docstring on why it is not always safe to blindly retry.
DEFAULT_TIMEOUT_SECONDS = 15.0

#: Twilio's own error codes, mapped to DHealth's vocabulary. Only the ones whose classification
#: genuinely changes behaviour are enumerated; everything else falls to the HTTP-status mapping
#: below, which is the conservative path rather than a guess dressed as knowledge.
#: https://www.twilio.com/docs/api/errors
_TWILIO_ERROR_CODES: dict[int, NotificationFailureCategory] = {
    20003: NotificationFailureCategory.AUTHENTICATION,
    20404: NotificationFailureCategory.CONFIGURATION,  # account/resource not found
    20429: NotificationFailureCategory.RATE_LIMIT,
    21211: NotificationFailureCategory.INVALID_DESTINATION,  # invalid 'To'
    21212: NotificationFailureCategory.CONFIGURATION,  # invalid 'From'
    21214: NotificationFailureCategory.INVALID_DESTINATION,  # 'To' not mobile/reachable
    21408: NotificationFailureCategory.CONFIGURATION,  # region not enabled on the account
    21606: NotificationFailureCategory.CONFIGURATION,  # 'From' not SMS-capable
    21610: NotificationFailureCategory.PROVIDER_REJECTED,  # recipient unsubscribed
    21612: NotificationFailureCategory.INVALID_DESTINATION,  # unreachable
    21614: NotificationFailureCategory.INVALID_DESTINATION,  # not a valid mobile number
    63016: NotificationFailureCategory.PROVIDER_REJECTED,  # WhatsApp: outside session, no template
    63018: NotificationFailureCategory.RATE_LIMIT,  # WhatsApp rate limit
}

#: Twilio's terminal delivery statuses reported through the status callback. ``delivered`` and
#: ``read`` are the two that mean a handset had it; the rest mean it did not arrive.
TWILIO_DELIVERED_STATUSES = frozenset({"delivered", "read"})
TWILIO_FAILED_STATUSES = frozenset({"undelivered", "failed", "canceled"})


class TwilioNotificationTransport:
    """``NotificationTransport`` over Twilio's Messages API.

    The HTTP client is injectable for exactly the reason ``connect_agent/client.py`` gives: a test
    can drive the real adapter against a transport stub without a network, so a failing test means
    this code is wrong rather than that a mock drifted from it.
    """

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        sms_from: str | None,
        whatsapp_from: str | None,
        status_callback_url: str | None = None,
        client: Any | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        api_root: str = TWILIO_API_ROOT,
    ) -> None:
        if not account_sid or not auth_token:
            raise ValueError("Twilio requires both an account SID and an auth token")
        if sms_from is None and whatsapp_from is None:
            raise ValueError(
                "Twilio requires at least one sender: DHEALTH_SMS_FROM or DHEALTH_WHATSAPP_FROM"
            )
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._sms_from = sms_from
        self._whatsapp_from = whatsapp_from
        self._status_callback_url = status_callback_url
        self._timeout = timeout_seconds
        self._api_root = api_root.rstrip("/")
        self._client = client

    @property
    def provider_name(self) -> str:
        return "twilio"

    def supports(self, channel: NotificationChannel) -> bool:
        if channel is NotificationChannel.SMS:
            return self._sms_from is not None
        if channel is NotificationChannel.WHATSAPP:
            return self._whatsapp_from is not None
        return False  # pragma: no cover - the enum has exactly two members

    def send(
        self, *, channel: NotificationChannel, destination: str, body: str
    ) -> TransportAccepted:
        sender = self._sender_for(channel)
        if sender is None:
            # A configuration fact, not a provider failure -- and terminal, because retrying a
            # missing sender produces the identical missing sender.
            raise TransportFailure(
                NotificationFailureCategory.CONFIGURATION,
                f"no Twilio sender is configured for the {channel.value} channel",
            )

        payload = {
            "To": _address(channel, destination),
            "From": sender,
            "Body": body,
        }
        if self._status_callback_url:
            payload["StatusCallback"] = self._status_callback_url

        url = f"{self._api_root}/Accounts/{self._account_sid}/Messages.json"
        try:
            response = self._post(url, payload)
        except Exception as error:  # noqa: BLE001 - every transport error is classified, not raised
            # Deliberately broad: httpx raises a family of connection/timeout/protocol errors and
            # the *classification* is identical for all of them. `_sanitize` keeps the destination
            # and the body out of the recorded detail; the exception type name is safe and is the
            # only part of the original kept.
            raise TransportFailure(
                NotificationFailureCategory.TRANSIENT_PROVIDER,
                f"provider request failed ({type(error).__name__})",
            ) from None

        return self._interpret(response)

    def _sender_for(self, channel: NotificationChannel) -> str | None:
        return self._sms_from if channel is NotificationChannel.SMS else self._whatsapp_from

    def _post(self, url: str, payload: dict[str, str]) -> Any:
        if self._client is not None:
            return self._client.post(
                url, data=payload, auth=(self._account_sid, self._auth_token), timeout=self._timeout
            )
        with httpx.Client(timeout=self._timeout) as client:  # pragma: no cover - real network
            return client.post(url, data=payload, auth=(self._account_sid, self._auth_token))

    def _interpret(self, response: Any) -> TransportAccepted:
        status_code = int(getattr(response, "status_code", 0))
        if 200 <= status_code < 300:
            body = _json_or_empty(response)
            message_id = body.get("sid")
            if not message_id:
                # A 2xx with no sid is a response this adapter does not understand. Refusing is
                # correct: recording SENT with no provider id would produce a row no delivery
                # receipt could ever resolve back to.
                raise TransportFailure(
                    NotificationFailureCategory.UNKNOWN,
                    "provider accepted the request but returned no message identifier",
                    http_status_code=status_code,
                )
            return TransportAccepted(
                provider_message_id=str(message_id),
                # Twilio's own word for where it is. Never read as delivery -- see transport.py.
                provider_status=str(body.get("status") or "accepted"),
                http_status_code=status_code,
            )

        raise TransportFailure(
            self._classify(response, status_code),
            _sanitize(status_code),
            http_status_code=status_code,
        )

    def _classify(self, response: Any, status_code: int) -> NotificationFailureCategory:
        body = _json_or_empty(response)
        raw_code = body.get("code")
        if isinstance(raw_code, int | str):
            try:
                mapped = _TWILIO_ERROR_CODES.get(int(raw_code))
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
            # A 4xx Twilio did not give a recognised code for. PROVIDER_REJECTED rather than
            # UNKNOWN because the one behavioural consequence -- do not retry -- is certainly
            # right for a client error, and UNKNOWN is reserved for "this adapter genuinely
            # cannot tell", which a 4xx is not.
            return NotificationFailureCategory.PROVIDER_REJECTED
        return NotificationFailureCategory.UNKNOWN


def _address(channel: NotificationChannel, destination: str) -> str:
    """Twilio's channel-prefixed address form. The only place the prefix convention exists."""
    return f"whatsapp:{destination}" if channel is NotificationChannel.WHATSAPP else destination


def _json_or_empty(response: Any) -> dict[str, Any]:
    try:
        parsed = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body is itself information-free here
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize(status_code: int) -> str:
    """The recorded failure detail: DHealth's own sentence, carrying only the status number.

    Not the provider's message. Twilio's error bodies routinely contain the ``To`` value, and for a
    ``patient_access_link`` message the ``Body`` they echo contains a live bearer credential. A
    number cannot leak either.
    """
    return f"provider rejected the request with HTTP {status_code}"
