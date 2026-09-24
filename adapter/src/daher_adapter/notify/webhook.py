"""DH-NOTIFY-META-001 §11 — Meta's webhook, and everything it is forbidden to do.

A delivery receipt is the only way ``DELIVERED`` is ever written, and these two routes are the only
place one is accepted. Everything about them is built around one sentence: **a spoofed callback must
be unable to mark anything delivered.**

Two routes, because Meta's registration handshake and its event delivery are different things:

* ``GET`` -- the subscription handshake. Meta sends ``hub.mode=subscribe``, ``hub.verify_token`` and
  ``hub.challenge``; a correct verify token means echoing the raw challenge back as ``text/plain``.
* ``POST`` -- the events. Authenticity is ``X-Hub-Signature-256``, an HMAC-SHA256 of the **raw
  request body** keyed with the Meta app secret.

What they may change: ``PatientNotification.state``, and nothing else.

What they may not change, and structurally cannot -- this module imports none of them:
``LabOrder``, ``LabResult``, ``LabObservation``, ``LabResultReleaseVersion``,
``LabResultAmendment``, ``DoctorReview``, ``ResultDelivery``, ``ConnectorDeliveryObligation``,
``PatientAccessGrant``. §11 says it and the import list enforces it.

**Resolution is by ``wamid`` and nothing else.** The payload's WABA id (``entry[].id``), its
``metadata.phone_number_id``, its ``recipient_id`` and anything else in the body are never read as
authority over which row is touched. DHealth minted nothing here -- it *recorded* the message id
when it accepted Meta's send response, so that id is the one field in the callback that is a fact
DHealth already knows. A forged payload naming a real organization, order or notification id changes
nothing, because none of those fields is consulted.

**Inbound messages are ignored entirely.** ``value.messages[]`` carries patient replies; §29 makes
inbound chat a non-goal, and a handler that parsed them would be the first half of an inbox nobody
decided to build.

**``read`` is ignored** (§13). It is not a clinical fact: it means a chat thread was open on a
device, not that a person read, understood or acted on anything, and mapping it into the state
machine would create a value somebody would eventually read as acknowledgement.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.notify.meta import META_DELIVERED_STATUSES, META_FAILED_STATUSES
from daher_adapter.notify.models import NotificationFailureCategory
from daher_adapter.notify.repository import PatientNotificationRepository

logger = logging.getLogger("daher_adapter.notify.webhook")

#: Meta batches status events, so this is more generous than a single-message provider needs while
#: still being a bound: an unauthenticated caller must not be able to make the server allocate
#: arbitrary memory before the signature has even been checked.
_MAX_CALLBACK_BODY_BYTES = 256 * 1024

#: The only ``object`` value this endpoint will process. A payload for some other Meta product
#: reaching this URL is a misconfiguration, not an event.
_EXPECTED_OBJECT = "whatsapp_business_account"


def meta_signature(app_secret: str, raw_body: bytes) -> str:
    """Meta's scheme: ``sha256=`` + HMAC-SHA256 of the **raw** body, keyed with the app secret.

    Implemented here rather than pulled from a vendor SDK so the repository has no runtime
    dependency on one, and so the validation -- the part that matters for security -- is readable in
    one place.

    The raw bytes matter and are why ``webhook`` reads ``request.body()`` before parsing anything:
    JSON that is decoded and re-encoded is not byte-identical to what Meta signed, so a validator
    working from a parsed object rejects every genuine callback and would be "fixed" by removing it.
    """
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_notification_webhook_router(
    *,
    session_factory: sessionmaker[Session],
    app_secret: str | None,
    verify_token: str | None,
    phone_number_id: str | None = None,
    prefix: str = "/notifications/v1",
) -> APIRouter:
    """The two webhook routes.

    ``app_secret`` and ``verify_token`` are both required for the endpoint to accept anything.
    Unset -- the ordinary state for a deployment with notifications off, and for every test that is
    not about this endpoint -- means **every** request is refused. Fails closed, exactly as the
    Exchange router does when its OIDC is unconfigured: a deployment that has provisioned no
    WhatsApp Business Account receives no callbacks.

    ``phone_number_id`` is optional defence in depth. When set, a status event whose
    ``metadata.phone_number_id`` names a different number is ignored -- not because the payload is
    authority (it is not, and the ``wamid`` lookup would fail for a foreign message anyway), but
    because a correctly-signed callback for the wrong business number means two deployments share an
    app secret, which an operator needs to find out about.
    """
    router = APIRouter(prefix=prefix, tags=["notifications"])
    repository = PatientNotificationRepository()

    @router.get("/meta/webhook")
    async def verify_subscription(
        hub_mode: str | None = Query(default=None, alias="hub.mode"),
        hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
        hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    ) -> Response:
        """Meta's subscription handshake.

        Echoes the raw ``hub.challenge`` as ``text/plain`` -- not JSON, not quoted. Meta compares
        the body verbatim, and a JSON-serialised challenge is the single most common reason a
        webhook fails to register.

        The verify token comparison is constant-time. It is a shared secret like any other, and the
        fact that this route is otherwise unauthenticated is exactly why it must not leak its value
        one byte at a time.
        """
        if not app_secret or not verify_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="provider callbacks are not configured on this deployment",
            )
        if hub_mode != "subscribe" or not hub_verify_token or not hub_challenge:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="invalid subscription request"
            )
        if not hmac.compare_digest(hub_verify_token, verify_token):
            logger.warning("patient_notification_webhook_verify_token_rejected")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="invalid verify token"
            )
        return Response(content=hub_challenge, media_type="text/plain")

    @router.post("/meta/webhook", status_code=status.HTTP_200_OK)
    async def receive_events(request: Request) -> dict[str, str]:
        """Status events.

        Returns 200 for everything it has authenticated, including payloads it chooses to ignore.
        That is deliberate: a 4xx makes Meta retry, and retrying a callback DHealth will never act
        on is a loop that ends in the endpoint being disabled for flapping.
        """
        if not app_secret or not verify_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="provider callbacks are not configured on this deployment",
            )

        signature = request.headers.get("X-Hub-Signature-256")
        if not signature:
            # A missing signature is refused exactly like a wrong one. Treating "absent" as a softer
            # case than "invalid" is how signature checks get bypassed.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="missing provider signature"
            )

        raw = await request.body()
        if len(raw) > _MAX_CALLBACK_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="payload too large"
            )

        # Signature over the raw bytes, **before** anything is parsed or trusted. Validating shape
        # first would turn this route into an oracle telling an unauthenticated caller which fields
        # DHealth expects.
        if not hmac.compare_digest(meta_signature(app_secret, raw), signature):
            # Logged without the payload, the signature or any id: a forged callback's body is
            # attacker-controlled and would otherwise be written verbatim into the log.
            logger.warning("patient_notification_webhook_signature_rejected")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="invalid provider signature"
            )

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Authenticated but unparseable. 400, because the caller proved they hold the app secret
            # and this is their bug to see on their own delivery dashboard.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="malformed payload"
            ) from None
        if not isinstance(payload, dict) or payload.get("object") != _EXPECTED_OBJECT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="unexpected payload object"
            )

        for event in _status_events(payload, expected_phone_number_id=phone_number_id):
            _apply(repository, session_factory, event)
        return {"status": "ok"}

    return router


def _status_events(
    payload: dict[str, Any], *, expected_phone_number_id: str | None
) -> list[tuple[str, str]]:
    """Every ``(wamid, status)`` pair in the payload, defensively.

    Meta batches: one callback carries ``entry[]``, each with ``changes[]``, each with a ``value``
    that may hold ``statuses[]``. Every level is type-checked rather than indexed, because this
    parses attacker-shaped input in the sense that matters -- it is authenticated, but a bug in
    Meta's fan-out or a replayed body from another deployment must produce nothing rather than an
    unhandled exception that a retry loop then hammers.

    ``value.messages[]`` is never read: inbound patient replies are a §29 non-goal.
    """
    events: list[tuple[str, str]] = []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return events
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            if expected_phone_number_id is not None:
                metadata = value.get("metadata")
                actual = metadata.get("phone_number_id") if isinstance(metadata, dict) else None
                if actual is not None and str(actual) != expected_phone_number_id:
                    # Correctly signed, wrong business number: two deployments sharing an app
                    # secret. Ignored rather than applied, and worth an operator's attention.
                    logger.warning("patient_notification_webhook_foreign_phone_number")
                    continue
            statuses = value.get("statuses")
            if not isinstance(statuses, list):
                continue
            for item in statuses:
                if not isinstance(item, dict):
                    continue
                message_id = item.get("id")
                state = item.get("status")
                if isinstance(message_id, str) and isinstance(state, str) and message_id:
                    events.append((message_id, state.strip().lower()))
    return events


def _apply(
    repository: PatientNotificationRepository,
    session_factory: sessionmaker[Session],
    event: tuple[str, str],
) -> None:
    """One status event, in its own transaction.

    ``sent`` is a no-op: DHealth already recorded ``SENT`` when Meta accepted the send, and applying
    it again would be the path by which a late ``sent`` arriving after a ``delivered`` walks a row
    backwards. ``read`` is a no-op for §13's reason. Everything unrecognised is a no-op too --
    Meta adding a status value must not make DHealth invent a meaning for it.

    Out-of-order safety does not rest on this function alone: ``apply_delivery_receipt`` refuses to
    move a row that is already ``DELIVERED`` or ``FAILED`` independently, so the two guards would
    both have to be removed for a regression to occur.
    """
    message_id, state = event
    if state in META_DELIVERED_STATUSES:
        delivered, category = True, None
    elif state in META_FAILED_STATUSES:
        delivered, category = False, NotificationFailureCategory.PROVIDER_REJECTED
    else:
        return

    with session_factory.begin() as session:
        updated = repository.apply_delivery_receipt(
            session,
            provider_message_id=message_id,
            delivered=delivered,
            category=category,
        )
    if updated is None:
        # An authenticated callback for a message id this deployment does not know -- another
        # environment sharing the WABA, or a message from before a database restore. Logged without
        # the id, and deliberately not created: a receipt may update an obligation DHealth already
        # owed and sent, never bring one into existence.
        logger.info("patient_notification_webhook_unknown_message")
