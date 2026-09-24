"""DH-NOTIFY-001 §15 — the provider status callback, and everything it is forbidden to do.

A delivery receipt is the only way ``DELIVERED`` is ever written, and this endpoint is the only
place a receipt is accepted. Everything about it is built around one sentence: **a spoofed callback
must be unable to mark anything delivered.**

What it may change: ``PatientNotification.state``, and the attempt history that explains it.

What it may not change, and structurally cannot -- this module imports none of them:
``LabOrder``, ``LabResult``, ``LabObservation``, a release version, ``DoctorReview``, and
``ResultDelivery``. A messaging vendor is not an authority on clinical state. §15 says it and the
import list enforces it.

**Resolution is by provider message id and nothing else.** The payload's own account identifier,
any organization or patient field a provider might add, and anything else in the body are never
read as authority. DHealth minted the message id when *it* accepted the provider's response, so it
is the one field in the callback that is a fact DHealth already knows.

**Mounted outside the Exchange router.** It carries no OIDC, because a vendor cannot hold a Keycloak
token; its authentication is the signature, and putting it on the clinical router would mean a route
there whose auth dependency is different from every other route's.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.notify.models import NotificationFailureCategory
from daher_adapter.notify.repository import PatientNotificationRepository
from daher_adapter.notify.twilio import TWILIO_DELIVERED_STATUSES, TWILIO_FAILED_STATUSES

logger = logging.getLogger("daher_adapter.notify.webhook")

#: A real Twilio status callback is a few hundred bytes. Generous, and still a bound.
_MAX_CALLBACK_BODY_BYTES = 64 * 1024


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's documented scheme: HMAC-SHA1 over the full URL with sorted params appended.

    Implemented here rather than pulled from the vendor SDK so the repository has no runtime
    dependency on it, and so the *validation* -- the part that matters for security -- is readable
    in one place.
    """
    payload = url
    for key in sorted(params):
        payload += key + params[key]
    digest = hmac.new(
        auth_token.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,  # noqa: S324
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def build_notification_webhook_router(
    *,
    session_factory: sessionmaker[Session],
    auth_token: str | None,
    callback_url: str | None,
    prefix: str = "/notifications/v1",
) -> APIRouter:
    """The one webhook route.

    ``auth_token`` and ``callback_url`` are both required for the endpoint to accept anything.
    Unset -- the ordinary state for a deployment with notifications off, and for every test that is
    not about this endpoint -- means **every** request is refused. Fails closed, exactly as the
    Exchange router does when its OIDC is unconfigured: a deployment that has provisioned no
    provider has no receipts to accept.

    ``callback_url`` is the **configured** public URL, never derived from the incoming ``Host``
    header. That is the whole reason it is a parameter: Twilio signs the URL it was told to call,
    and validating against an attacker-controlled ``Host`` would let anyone who can set a header
    produce a signature that verifies.
    """
    router = APIRouter(prefix=prefix, tags=["notifications"])
    repository = PatientNotificationRepository()

    @router.post("/twilio/status", status_code=status.HTTP_204_NO_CONTENT)
    async def twilio_status_callback(
        request: Request,
        x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
    ) -> None:
        """The form is parsed **once**, by hand, and that is deliberate on two counts.

        First, correctness: the signature is computed over the exact parameter set, so reading the
        fields from a second, independently-parsed source would let a payload verify under one
        parse and act under another. One dict, one truth.

        Second, dependencies: both ``Form(...)`` parameters and ``request.form()`` require
        ``python-multipart``, and the former requires it at *route-definition* time -- which would
        make the whole adapter refuse to construct on every deployment, notifications or not, for a
        route almost none of them use. Twilio status callbacks are always
        ``application/x-www-form-urlencoded``, which the standard library decodes.

        A body that is *not* urlencoded decodes to a parameter set that does not match what the
        provider signed, so it is refused by the signature check below -- fail-closed, which is the
        right outcome for a payload shape this endpoint does not expect.
        """
        if not auth_token or not callback_url:
            # Not 404: an operator whose callbacks are being refused needs to see that the
            # deployment is not configured for them, and a provider's retry dashboard showing 403
            # is the fastest route to that. It reveals nothing -- the endpoint's existence is
            # already implied by the URL they were configured with.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="provider callbacks are not configured on this deployment",
            )
        if not x_twilio_signature:
            # A missing signature is refused exactly like a wrong one. Treating "absent" as a
            # softer case than "invalid" is how signature checks get bypassed.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="missing provider signature"
            )

        raw = await request.body()
        if len(raw) > _MAX_CALLBACK_BODY_BYTES:
            # Bounded before decoding. An unauthenticated caller must not be able to make the
            # server allocate an arbitrary amount of memory, and a real Twilio callback is a few
            # hundred bytes.
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="payload too large"
            )
        # `keep_blank_values=True` matters and is not a default worth taking implicitly: Twilio
        # sends empty `ErrorCode`/`ErrorMessage` fields on success, and they are part of what it
        # signed. Dropping them would compute the digest over a different parameter set than the
        # provider did, and every genuine callback would be refused.
        params = {
            key: value
            for key, value in parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)
        }
        message_sid = params.get("MessageSid", "")
        message_status = params.get("MessageStatus", "")

        # Signature first, always -- before any field is trusted, before any lookup, and before a
        # missing-field complaint. Validating shape before authenticity would turn this endpoint
        # into an oracle that tells an unauthenticated caller which fields DHealth expects.
        expected = twilio_signature(auth_token, callback_url, params)
        # Constant-time, so a wrong signature cannot be refined byte by byte from timing.
        if not hmac.compare_digest(expected, x_twilio_signature):
            # Logged without the payload, the signature, or the message id: a forged callback's
            # body is attacker-controlled and would otherwise be written verbatim into the log.
            logger.warning("patient_notification_webhook_signature_rejected")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="invalid provider signature"
            )

        if not message_sid or not message_status:
            # An authenticated but malformed callback. 400 rather than 403: the caller proved they
            # hold the shared secret, so this is their bug to fix, and a provider's retry dashboard
            # distinguishing "rejected" from "malformed" is what makes it findable.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MessageSid and MessageStatus are required",
            )

        normalized = message_status.strip().lower()
        if normalized in TWILIO_DELIVERED_STATUSES:
            delivered, category = True, None
        elif normalized in TWILIO_FAILED_STATUSES:
            delivered, category = False, NotificationFailureCategory.PROVIDER_REJECTED
        else:
            # `queued`, `sending`, `sent`, `accepted` and anything a future provider version adds.
            # Acknowledged and ignored: they carry no information DHealth does not already have,
            # and a 2xx stops the provider retrying a callback that would never change anything.
            # Critically, this is also what prevents a late `sent` from walking a `DELIVERED` row
            # backwards -- though `apply_delivery_receipt` refuses that independently.
            return None

        with session_factory.begin() as session:
            updated = repository.apply_delivery_receipt(
                session,
                provider_message_id=message_sid,
                delivered=delivered,
                category=category,
            )
        if updated is None:
            # An authenticated callback for a message id this deployment does not know. Not an
            # error to the provider -- a 4xx would make them retry forever -- and deliberately not
            # a row created here: a receipt is never allowed to bring a notification into
            # existence, only to update one DHealth already owed and sent.
            logger.info("patient_notification_webhook_unknown_message")
        return None

    return router
