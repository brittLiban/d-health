"""DH-NOTIFY-META-001 §22/§23 — the Meta WhatsApp adapter, and the webhook that may not be spoofed.

Group M drives the **real** ``MetaWhatsAppTransport`` against an injected stub HTTP client, so a
failing test means this adapter is wrong rather than that a mock drifted. It never reaches the
network.

Group W drives the **real** webhook routes through the **real** ``create_app`` assembly, so the
signature check under test is the one a deployment actually mounts. ``NC-META-3`` removes it and
``test_w4``/``test_w5`` are what must go red.

Also here, and load-bearing for §4: the configuration matrix. "WhatsApp disabled" and "WhatsApp
configured but broken" are operationally different states, and the second is a refusal to start
naming the missing variable.

**What none of this proves.** Every test below runs against a stub. No real Meta endpoint was
contacted, and ADR-003's gate is unchanged: WhatsApp stays BLOCKED until real credentials have run
the flow to a real handset (``docs/META-WHATSAPP-RUNBOOK.md``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from exchange_support import make_lab
from fastapi import Request
from fastapi.testclient import TestClient
from notify_support import (
    SYNTHETIC_DESTINATION,
    build_repos,
    enrol,
    enter_result,
    order_in_progress,
    release,
)
from sqlalchemy import Engine, select

from daher_adapter.app import create_app
from daher_adapter.audit import AuditWriter
from daher_adapter.config import ConfigurationError, Settings
from daher_adapter.exchange.models import (
    DoctorReview,
    LabOrder,
    PatientAccessGrant,
    ResultDelivery,
)
from daher_adapter.notify.dispatcher import PatientNotificationDispatcher
from daher_adapter.notify.fake import FakeNotificationTransport
from daher_adapter.notify.meta import MetaWhatsAppTransport
from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationEventType,
    NotificationFailureCategory,
    NotificationState,
    PatientNotification,
    PatientNotificationAttempt,
)
from daher_adapter.notify.transport import NotificationTransport, TransportFailure
from daher_adapter.notify.webhook import (
    CallbackTooLarge,
    _read_bounded_body,
    meta_signature,
)

APP_SECRET = "unit-test-app-secret-not-real"  # noqa: S105 - fixture value
VERIFY_TOKEN = "unit-test-verify-token-not-real"  # noqa: S105 - fixture value
ACCESS_TOKEN = "unit-test-access-token-not-real"  # noqa: S105 - fixture value
PHONE_NUMBER_ID = "111222333444555"
WABA_ID = "999888777666555"
GRAPH_VERSION = "v23.0"
ACCESS_TEMPLATE = "dhealth_patient_access_fr"
UPDATE_TEMPLATE = "dhealth_patient_update_fr"
TEMPLATE_NAMES = {
    "META_TEMPLATE_PATIENT_ACCESS_FR": ACCESS_TEMPLATE,
    "META_TEMPLATE_PATIENT_UPDATE_FR": UPDATE_TEMPLATE,
}
SECURE_URL = "https://dhealth.example/?app=patient#access=SUPERSECRETTOKEN"


# ---------------------------------------------------------------------------------------------
# Group M: the Meta adapter
# ---------------------------------------------------------------------------------------------


@dataclass
class _StubResponse:
    status_code: int
    payload: Any = None

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@dataclass
class _StubClient:
    """Records exactly what the adapter asked for, and answers with a scripted response."""

    response: _StubResponse | Exception
    calls: list[dict] = field(default_factory=list)

    def post(self, url: str, *, json: dict, headers: dict, timeout: float) -> _StubResponse:  # noqa: A002
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _transport(response, **overrides) -> tuple[MetaWhatsAppTransport, _StubClient]:
    client = _StubClient(response=response)
    kwargs: dict[str, Any] = dict(
        access_token=ACCESS_TOKEN,
        phone_number_id=PHONE_NUMBER_ID,
        graph_api_version=GRAPH_VERSION,
        template_names=TEMPLATE_NAMES,
        template_language="fr",
        business_account_id=WABA_ID,
        client=client,
    )
    kwargs.update(overrides)
    return MetaWhatsAppTransport(**kwargs), client


def _accepted(sid: str = "wamid.TEST123") -> _StubResponse:
    return _StubResponse(
        200,
        {
            "messaging_product": "whatsapp",
            "contacts": [{"input": "25377123456", "wa_id": "25377123456"}],
            "messages": [{"id": sid}],
        },
    )


def test_m1_m2_m4_m5_m6_endpoint_version_phone_number_and_recipient() -> None:
    """§22.1/2/4/5/6 in one request, because they are one request.

    The configured Graph API version appears in the path -- not a hard-coded constant, and not an
    unversioned endpoint. §3 is the reason: the version is the value most likely to be wrong, so it
    is configuration and a test reads it back out of the URL the adapter actually built.
    """
    transport, client = _transport(_accepted())

    transport.send(
        channel=NotificationChannel.WHATSAPP,
        destination=SYNTHETIC_DESTINATION,
        event_type=NotificationEventType.RESULT_RELEASED,
        locale="fr",
    )

    call = client.calls[0]
    assert call["url"] == f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
    assert f"/{GRAPH_VERSION}/" in call["url"], "the configured version must be in the path"
    body = call["json"]
    assert body["messaging_product"] == "whatsapp"
    assert body["recipient_type"] == "individual"
    # §19: DHealth's canonical form keeps the '+'; the digits-only wire form exists only here.
    assert body["to"] == SYNTHETIC_DESTINATION.lstrip("+")
    assert SYNTHETIC_DESTINATION.startswith("+"), "the domain representation is unchanged"
    assert body["type"] == "template"


def test_m3_authorization_header_is_correct_and_the_token_never_leaves_it() -> None:
    """§22.3. The bearer token is in exactly one place and in no other field of the request."""
    transport, client = _transport(_accepted())

    transport.send(
        channel=NotificationChannel.WHATSAPP,
        destination=SYNTHETIC_DESTINATION,
        event_type=NotificationEventType.RESULT_RELEASED,
        locale="fr",
    )

    call = client.calls[0]
    assert call["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert ACCESS_TOKEN not in json.dumps(call["json"]), "the token is a header, never a field"
    assert ACCESS_TOKEN not in call["url"]


def test_m7_m10_issuance_uses_the_approved_template_and_carries_the_link() -> None:
    """§22.7/§22.10. The one message allowed a link, and the only one."""
    transport, client = _transport(_accepted())

    transport.send(
        channel=NotificationChannel.WHATSAPP,
        destination=SYNTHETIC_DESTINATION,
        event_type=NotificationEventType.ACCESS_LINK_ISSUED,
        locale="fr",
        patient_access_url=SECURE_URL,
    )

    template = client.calls[0]["json"]["template"]
    assert template["name"] == ACCESS_TEMPLATE
    assert template["language"] == {"code": "fr"}
    assert template["components"] == [
        {"type": "body", "parameters": [{"type": "text", "text": SECURE_URL}]}
    ]


@pytest.mark.parametrize(
    "event",
    [NotificationEventType.RESULT_RELEASED, NotificationEventType.AMENDMENT_RELEASED],
)
def test_m8_m9_m11_release_and_amendment_use_the_update_template_with_no_parameters(
    event: NotificationEventType,
) -> None:
    """§22.8/9/11 — and the strongest form of §9's content boundary.

    The update payload has **no ``components`` key at all**. Not "no clinical parameters": no
    parameter array exists, so there is nothing into which a value, a unit, a range, a disposition
    or a link could ever be threaded. ``NC-META-4`` adds one and this goes red.
    """
    transport, client = _transport(_accepted())

    transport.send(
        channel=NotificationChannel.WHATSAPP,
        destination=SYNTHETIC_DESTINATION,
        event_type=event,
        locale="fr",
    )

    template = client.calls[0]["json"]["template"]
    assert template["name"] == UPDATE_TEMPLATE
    assert "components" not in template, "an update message has no parameter array at all"
    serialized = json.dumps(client.calls[0]["json"])
    for forbidden in ("SUPERSECRETTOKEN", "access=", "http://", "https://", "#"):
        assert forbidden not in serialized, f"{forbidden!r} reached an update payload"


def test_m11_a_release_send_refuses_a_patient_access_url_outright() -> None:
    """Refused, never silently dropped.

    Dropping it would let a caller believe a link was sent. This is ADR-058's A′ enforced at the one
    place a provider payload is built, and it is why the release path physically cannot carry a URL
    even if some future caller passed one.
    """
    transport, client = _transport(_accepted())

    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.RESULT_RELEASED,
            locale="fr",
            patient_access_url=SECURE_URL,
        )
    assert excinfo.value.category is NotificationFailureCategory.CONFIGURATION
    assert client.calls == [], "nothing reached the provider"


def test_an_issuance_with_no_url_is_refused_rather_than_sent_empty() -> None:
    """A link message with no link is worse than none: the patient follows nothing."""
    transport, client = _transport(_accepted())
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
            locale="fr",
        )
    assert excinfo.value.category is NotificationFailureCategory.CONFIGURATION
    assert client.calls == []


def test_the_template_name_is_never_a_caller_parameter() -> None:
    """§8/§22 — the control ``NC-META-5`` breaks, asserted on the signature itself.

    A ``send(template_name=...)`` parameter would let any caller name any approved template. The
    server owns the mapping; this test is what notices if that stops being true.
    """
    for send in (MetaWhatsAppTransport.send, NotificationTransport.send):
        parameters = set(inspect.signature(send).parameters)
        assert "template_name" not in parameters
        assert "template" not in parameters
        assert "body" not in parameters, "a caller-supplied body would bypass the content policy"

    transport, _ = _transport(_accepted())
    with pytest.raises(TypeError):
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.RESULT_RELEASED,
            locale="fr",
            template_name="something_else",
        )


def test_m17_timeout_is_transient() -> None:
    transport, _ = _transport(TimeoutError("read timeout"))
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.RESULT_RELEASED,
            locale="fr",
        )
    assert excinfo.value.category is NotificationFailureCategory.TRANSIENT_PROVIDER


@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    [
        # §22.14 -- authentication.
        (401, {}, NotificationFailureCategory.AUTHENTICATION),
        (403, {}, NotificationFailureCategory.AUTHENTICATION),
        (400, {"error": {"code": 190}}, NotificationFailureCategory.AUTHENTICATION),
        # §22.15 -- rate limiting.
        (429, {}, NotificationFailureCategory.RATE_LIMIT),
        (400, {"error": {"code": 4}}, NotificationFailureCategory.RATE_LIMIT),
        (400, {"error": {"code": 80007}}, NotificationFailureCategory.RATE_LIMIT),
        # §22.16 -- permanent rejection and its neighbours.
        (400, {"error": {"code": 131026}}, NotificationFailureCategory.INVALID_DESTINATION),
        (400, {"error": {"code": 132000}}, NotificationFailureCategory.CONFIGURATION),
        (400, {"error": {"code": 100}}, NotificationFailureCategory.CONFIGURATION),
        (400, {}, NotificationFailureCategory.PROVIDER_REJECTED),
        (404, {}, NotificationFailureCategory.CONFIGURATION),
        (500, {}, NotificationFailureCategory.TRANSIENT_PROVIDER),
        (503, {}, NotificationFailureCategory.TRANSIENT_PROVIDER),
        # An error code this adapter does not recognise falls through to the HTTP status, which is
        # the load-bearing path -- see meta.py on why the code table is deliberately short.
        (400, {"error": {"code": 999999}}, NotificationFailureCategory.PROVIDER_REJECTED),
        (503, {"error": {"code": 999999}}, NotificationFailureCategory.TRANSIENT_PROVIDER),
    ],
)
def test_m14_m15_m16_error_classification(status_code, payload, expected) -> None:
    transport, _ = _transport(_StubResponse(status_code, payload))
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.RESULT_RELEASED,
            locale="fr",
        )
    assert excinfo.value.category is expected
    assert excinfo.value.http_status_code == status_code


def test_m19_meta_error_body_cannot_leak_into_the_recorded_detail() -> None:
    """§22.19. Meta's ``error.message`` quotes the offending parameter.

    For an access-link send that parameter is the patient's live bearer URL. The recorded detail is
    DHealth's own sentence carrying only the status number.
    """
    leaky = {
        "error": {
            "message": f"(#132012) Parameter format does not match: {SECURE_URL}",
            "type": "OAuthException",
            "code": 132012,
            "error_data": {"details": f"recipient {SYNTHETIC_DESTINATION} rejected"},
            "fbtrace_id": "A1b2C3d4",
        }
    }
    transport, _ = _transport(_StubResponse(400, leaky))

    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
            locale="fr",
            patient_access_url=SECURE_URL,
        )

    detail = excinfo.value.detail
    assert detail == "provider rejected the request with HTTP 400"
    assert "SUPERSECRETTOKEN" not in detail
    assert SYNTHETIC_DESTINATION not in detail
    assert "fbtrace" not in detail.lower()
    assert "OAuthException" not in detail


@pytest.mark.parametrize(
    "payload",
    [{}, {"messages": []}, {"messages": [{}]}, {"messages": "not-a-list"}],
    ids=["empty", "no-messages", "no-id", "messages-not-a-list"],
)
def test_m18_a_malformed_meta_response_fails_safely(payload) -> None:
    """§22.18. A 2xx with no message id is refused rather than recorded as SENT.

    A SENT row with no provider id is a row no delivery receipt could ever resolve back to, and the
    ``wamid`` is the webhook's only correlation key.
    """
    transport, _ = _transport(_StubResponse(200, payload))
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.RESULT_RELEASED,
            locale="fr",
        )
    assert excinfo.value.category is NotificationFailureCategory.UNKNOWN


def test_m18_a_non_json_success_body_fails_safely() -> None:
    transport, _ = _transport(_StubResponse(200, ValueError("not json")))
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.RESULT_RELEASED,
            locale="fr",
        )
    assert excinfo.value.category is NotificationFailureCategory.UNKNOWN


def test_m12_m13_acceptance_persists_the_wamid_as_sent_not_delivered(sessions) -> None:
    """§22.12/13 — through the real dispatcher, so the mapping under test is the production one."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab M12")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="m12")
    enrol(repos, sessions, context, channel=NotificationChannel.WHATSAPP)
    enter_result(repos, context, key="m12")
    release(repos, context, key="m12")

    transport, _ = _transport(_accepted("wamid.HBgLMjUzNzcxMjM0NTYVAgAR"))
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-m12").dispatch_once()

    with sessions() as session:
        row = session.scalars(
            select(PatientNotification).where(PatientNotification.lab_order_id == context.order_id)
        ).one()
        attempts = list(
            session.scalars(
                select(PatientNotificationAttempt).where(
                    PatientNotificationAttempt.patient_notification_id == row.id
                )
            ).all()
        )
    assert row.provider_message_id == "wamid.HBgLMjUzNzcxMjM0NTYVAgAR"
    assert row.state == NotificationState.SENT.value, "a 200 from Meta is acceptance, not delivery"
    assert row.delivered_at is None
    assert len(attempts) == 1
    assert attempts[0].outcome == "ACCEPTED"
    assert attempts[0].provider_status_code == 200


def test_meta_is_not_an_sms_provider_and_says_so() -> None:
    """§6. The channel enum exists; the capability does not, and the transport is honest."""
    transport, client = _transport(_accepted())
    assert transport.supports(NotificationChannel.WHATSAPP) is True
    assert transport.supports(NotificationChannel.SMS) is False

    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.SMS,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.RESULT_RELEASED,
            locale="fr",
        )
    assert excinfo.value.category is NotificationFailureCategory.CONFIGURATION
    assert client.calls == []


@pytest.mark.parametrize("bad", ["", "23.0", "v23", "latest", "vX.Y", "v23.0.1"])
def test_the_transport_refuses_to_construct_without_a_pinned_graph_version(bad) -> None:
    """§3. No default, and a malformed value is refused rather than pasted into a URL."""
    with pytest.raises(ValueError, match="META_GRAPH_API_VERSION"):
        _transport(_accepted(), graph_api_version=bad)


def test_the_transport_refuses_to_construct_without_credentials() -> None:
    with pytest.raises(ValueError, match="access token"):
        _transport(_accepted(), access_token="")
    with pytest.raises(ValueError, match="phone number id"):
        _transport(_accepted(), phone_number_id="")
    with pytest.raises(ValueError, match="template language"):
        _transport(_accepted(), template_language="")


def test_an_unconfigured_template_name_is_a_terminal_configuration_failure() -> None:
    transport, client = _transport(_accepted(), template_names={})
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP,
            destination=SYNTHETIC_DESTINATION,
            event_type=NotificationEventType.RESULT_RELEASED,
            locale="fr",
        )
    assert excinfo.value.category is NotificationFailureCategory.CONFIGURATION
    assert client.calls == []


# ---------------------------------------------------------------------------------------------
# Group W: the Meta webhook
# ---------------------------------------------------------------------------------------------


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base = dict(
        timezone="Africa/Djibouti",
        db_host="unused",
        db_port=5432,
        db_name="adapter",
        db_user="adapter",
        db_password="not-a-real-secret",
        audit_path=tmp_path / "audit" / "audit.jsonl",
        retry_max_attempts=5,
        retry_multiplier_seconds=1,
        retry_max_seconds=30,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _webhook_app(tmp_path: Path, engine: Engine, **overrides):
    settings = _settings(
        tmp_path,
        meta_app_secret=APP_SECRET,
        meta_webhook_verify_token=VERIFY_TOKEN,
        meta_phone_number_id=PHONE_NUMBER_ID,
        **overrides,
    )
    return create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))


def _status_payload(
    wamid: str,
    status: str,
    *,
    phone_number_id: str = PHONE_NUMBER_ID,
    waba_id: str = WABA_ID,
    extra_value: dict | None = None,
) -> bytes:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "25321000000", "phone_number_id": phone_number_id},
        "statuses": [
            {
                "id": wamid,
                "status": status,
                "timestamp": "1790000000",
                "recipient_id": SYNTHETIC_DESTINATION.lstrip("+"),
            }
        ],
    }
    if extra_value:
        value.update(extra_value)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": waba_id, "changes": [{"field": "messages", "value": value}]}],
    }
    return json.dumps(payload).encode("utf-8")


def _signed(raw: bytes, *, secret: str = APP_SECRET) -> dict[str, str]:
    return {"X-Hub-Signature-256": meta_signature(secret, raw), "Content-Type": "application/json"}


def _sent_notification(sessions, repos, lab_name: str, key: str):
    """An order released, dispatched and accepted -- a row a receipt can hit."""
    lab = make_lab(repos.organizations, name=lab_name)
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    enrol(repos, sessions, context, channel=NotificationChannel.WHATSAPP)
    enter_result(repos, context, key=key)
    release(repos, context, key=key)
    PatientNotificationDispatcher(
        sessions, transport=FakeNotificationTransport(), worker_id=f"w-{key}"
    ).dispatch_once()
    with sessions() as session:
        row = session.scalars(
            select(PatientNotification).where(PatientNotification.lab_order_id == context.order_id)
        ).one()
        assert row.state == NotificationState.SENT.value
        return row.id, row.provider_message_id, context


def test_w1_verification_succeeds_and_echoes_the_raw_challenge(tmp_path, engine) -> None:
    """§23.1. The challenge comes back as plain text, verbatim.

    A JSON-serialised or quoted challenge is the single most common reason a Meta webhook fails to
    register, so the exact body is what this asserts -- not merely a 200.
    """
    app = _webhook_app(tmp_path, engine)
    with TestClient(app) as client:
        response = client.get(
            "/notifications/v1/meta/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "1158201444",
            },
        )
    assert response.status_code == 200
    assert response.text == "1158201444"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "x"},
        {"hub.verify_token": VERIFY_TOKEN, "hub.challenge": "x"},
        {"hub.mode": "subscribe", "hub.challenge": "x"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN},
    ],
    ids=["wrong-token", "wrong-mode", "no-mode", "no-token", "no-challenge"],
)
def test_w2_incorrect_verification_fails(tmp_path, engine, params) -> None:
    """§23.2."""
    app = _webhook_app(tmp_path, engine)
    with TestClient(app) as client:
        response = client.get("/notifications/v1/meta/webhook", params=params)
    assert response.status_code == 403
    assert "1158201444" not in response.text


def test_w3_w8_a_valid_delivered_callback_advances_the_state(tmp_path, engine, sessions) -> None:
    """§23.3/§23.8."""
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W3", "w3")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "delivered")

    with TestClient(app) as client:
        response = client.post("/notifications/v1/meta/webhook", content=raw, headers=_signed(raw))

    assert response.status_code == 200
    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
    assert row.state == NotificationState.DELIVERED.value
    assert row.delivered_at is not None


def test_w4_an_invalid_signature_is_rejected_and_changes_nothing(
    tmp_path, engine, sessions
) -> None:
    """§23.4 — the test ``NC-META-3`` must turn red."""
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W4", "w4")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "delivered")
    forged = "sha256=" + hmac.new(b"wrong-secret", raw, hashlib.sha256).hexdigest()

    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/meta/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": forged, "Content-Type": "application/json"},
        )

    assert response.status_code == 403
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        ), "a spoofed callback must not be able to mark anything delivered"


def test_w5_a_missing_signature_is_rejected(tmp_path, engine, sessions) -> None:
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W5", "w5")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "delivered")

    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/meta/webhook",
            content=raw,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 403
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        )


def test_w4_a_signature_over_different_bytes_is_rejected(tmp_path, engine, sessions) -> None:
    """The raw-body property, made explicit.

    A signature computed over semantically-equivalent but differently-serialised JSON must fail.
    This is what stops a future "parse then validate" refactor from quietly accepting forgeries.
    """
    repos = build_repos(sessions)
    _, wamid, _ = _sent_notification(sessions, repos, "Lab W4B", "w4b")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "delivered")
    reserialized = json.dumps(json.loads(raw), indent=2).encode("utf-8")

    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/meta/webhook", content=raw, headers=_signed(reserialized)
        )
    assert response.status_code == 403


def test_w6_a_known_wamid_resolves_the_correct_notification(tmp_path, engine, sessions) -> None:
    """§23.6. Two live notifications; only the one named by the wamid moves."""
    repos = build_repos(sessions)
    first_id, first_wamid, _ = _sent_notification(sessions, repos, "Lab W6a", "w6a")
    second_id, _second, _ = _sent_notification(sessions, repos, "Lab W6b", "w6b")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(first_wamid, "delivered")

    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )

    with sessions() as session:
        assert session.get(PatientNotification, first_id).state == NotificationState.DELIVERED.value
        assert session.get(PatientNotification, second_id).state == NotificationState.SENT.value


def test_w7_a_sent_callback_is_a_no_op(tmp_path, engine, sessions) -> None:
    """§23.7. DHealth already recorded SENT when Meta accepted the send."""
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W7", "w7")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "sent")

    with sessions() as session:
        before = session.get(PatientNotification, notification_id).updated_at

    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )

    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
    assert row.state == NotificationState.SENT.value
    assert row.updated_at == before, "a sent callback changes nothing at all"


def test_w9_duplicate_delivered_callbacks_are_idempotent(tmp_path, engine, sessions) -> None:
    """§23.9."""
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W9", "w9")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "delivered")

    with TestClient(app) as client:
        for _ in range(3):
            assert (
                client.post(
                    "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
                ).status_code
                == 200
            )

    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
        attempts = list(
            session.scalars(
                select(PatientNotificationAttempt).where(
                    PatientNotificationAttempt.patient_notification_id == notification_id
                )
            ).all()
        )
    assert row.state == NotificationState.DELIVERED.value
    assert len(attempts) == 1, "a receipt is not a hand-over and never writes an attempt row"


def test_w10_a_late_sent_after_delivered_cannot_regress_the_state(
    tmp_path, engine, sessions
) -> None:
    """§23.10 / §12 — the test ``NC-META-2`` must turn red.

    Providers reorder. The guarantee is defended twice, deliberately: the webhook treats ``sent`` as
    a no-op, and ``apply_delivery_receipt`` independently refuses to move a terminal row. Both would
    have to be removed for a regression to occur.
    """
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W10", "w10")
    app = _webhook_app(tmp_path, engine)

    def _state_and_delivered_at():
        with sessions() as session:
            row = session.get(PatientNotification, notification_id)
            return row.state, row.delivered_at

    with TestClient(app) as client:
        raw = _status_payload(wamid, "delivered")
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )
        state, delivered_at = _state_and_delivered_at()
        assert state == NotificationState.DELIVERED.value
        assert delivered_at is not None

        # Asserted after **each** late callback, not only at the end. A sequence that finishes with
        # another `delivered` would mask an intermediate regression entirely -- which is exactly
        # what running NC-META-2 against the first version of this test revealed.
        for late in ("sent", "failed", "read", "queued", "accepted"):
            raw = _status_payload(wamid, late)
            assert (
                client.post(
                    "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
                ).status_code
                == 200
            )
            assert _state_and_delivered_at() == (state, delivered_at), (
                f"a late {late!r} callback moved a DELIVERED row"
            )


def test_w11_read_never_touches_the_state_machine(tmp_path, engine, sessions) -> None:
    """§13/§23.11. ``read`` means a chat thread was open. It is not acknowledgement of anything."""
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W11", "w11")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "read")

    with sessions() as session:
        before = session.get(PatientNotification, notification_id).updated_at

    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )

    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
    assert row.state == NotificationState.SENT.value
    assert row.updated_at == before
    assert row.delivered_at is None


def test_w12_a_failed_provider_event_maps_to_failed(tmp_path, engine, sessions) -> None:
    """§23.12. And the provider's own error text never reaches the row."""
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W12", "w12")
    app = _webhook_app(tmp_path, engine)
    payload = json.loads(_status_payload(wamid, "failed"))
    payload["entry"][0]["changes"][0]["value"]["statuses"][0]["errors"] = [
        {
            "code": 131026,
            "title": "Message undeliverable",
            "message": f"Recipient {SYNTHETIC_DESTINATION} is not a WhatsApp user",
        }
    ]
    raw = json.dumps(payload).encode("utf-8")

    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )

    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
        attempts = list(
            session.scalars(
                select(PatientNotificationAttempt).where(
                    PatientNotificationAttempt.patient_notification_id == notification_id
                )
            ).all()
        )
    assert row.state == NotificationState.FAILED.value
    assert row.failure_category == NotificationFailureCategory.PROVIDER_REJECTED.value
    assert row.delivered_at is None
    for attempt in attempts:
        assert SYNTHETIC_DESTINATION not in (attempt.failure_detail or "")
        assert "undeliverable" not in (attempt.failure_detail or "").lower()


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        b'{"object": "instagram", "entry": []}',
        b'{"entry": []}',
        b"[]",
        b'{"object": "whatsapp_business_account", "entry": "not-a-list"}',
        b'{"object": "whatsapp_business_account", "entry": [{"changes": {}}]}',
        b'{"object": "whatsapp_business_account", "entry": [{"changes": [{"value": null}]}]}',
        b'{"object": "whatsapp_business_account", "entry": [{"changes": [{"value": '
        b'{"statuses": "nope"}}]}]}',
        b'{"object": "whatsapp_business_account", "entry": [{"changes": [{"value": '
        b'{"statuses": [{"status": "delivered"}]}}]}]}',
    ],
    ids=[
        "unparseable",
        "wrong-object",
        "no-object",
        "not-an-object",
        "entry-not-list",
        "changes-not-list",
        "null-value",
        "statuses-not-list",
        "status-without-id",
    ],
)
def test_w13_a_malformed_callback_fails_closed(tmp_path, engine, sessions, body) -> None:
    """§23.13. Authenticated but malformed changes nothing and never raises an unhandled error."""
    repos = build_repos(sessions)
    notification_id, _wamid, _ = _sent_notification(sessions, repos, "Lab W13", f"w13{len(body)}")
    app = _webhook_app(tmp_path, engine)

    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/meta/webhook", content=body, headers=_signed(body)
        )

    assert response.status_code in (200, 400)
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        )


def test_w14_w15_w16_the_callback_never_touches_clinical_state(tmp_path, engine, sessions) -> None:
    """§23.14/15/16 — a snapshot diff across an authenticated, *successful* callback.

    The callback below genuinely works (the notification reaches DELIVERED), so the equality is not
    satisfied by nothing having happened. ``PatientAccessGrant`` is included because §11 names it:
    a delivery receipt must never revoke, rotate or extend a patient's access.
    """
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab W14", "w14")

    def snapshot() -> dict:
        with sessions() as session:
            return {
                "deliveries": {
                    row.id: (row.status, row.delivered_at)
                    for row in session.scalars(select(ResultDelivery)).all()
                },
                "reviews": {
                    row.id: (row.status, row.release_version_id, row.reviewed_at)
                    for row in session.scalars(select(DoctorReview)).all()
                },
                "orders": {row.id: row.status for row in session.scalars(select(LabOrder)).all()},
                "grants": {
                    row.id: (row.token_hash, row.revoked_at, row.expires_at, row.access_mode)
                    for row in session.scalars(select(PatientAccessGrant)).all()
                },
            }

    before = snapshot()
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "delivered")
    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )

    assert snapshot() == before
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.DELIVERED.value
        )


def test_w17_payload_fields_cannot_choose_tenant_authority(tmp_path, engine, sessions) -> None:
    """§23.5/§23.17. Resolution is by wamid and nothing else.

    The forged payload carries a real order id, a real provider id, a real notification id and a
    foreign WABA id -- all correctly signed. None is read, so the unknown wamid it names is what
    decides the outcome: nothing happens.
    """
    repos = build_repos(sessions)
    notification_id, _wamid, context = _sent_notification(sessions, repos, "Lab W17", "w17")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(
        "wamid.NOT-OURS",
        "delivered",
        waba_id="000000000000000",
        extra_value={
            "organization_id": str(context.provider_id),
            "lab_order_id": str(context.order_id),
            "patient_notification_id": str(notification_id),
        },
    )

    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )

    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        ), "an id in the payload is not authority over the row it names"


def test_a_foreign_phone_number_id_is_ignored(tmp_path, engine, sessions) -> None:
    """Defence in depth: a correctly-signed event for another business number is not applied.

    It also could not have been -- the wamid would not resolve -- but two deployments sharing an app
    secret is something an operator must find out about rather than silently absorb.
    """
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab WPN", "wpn")
    app = _webhook_app(tmp_path, engine)
    raw = _status_payload(wamid, "delivered", phone_number_id="000000000000000")

    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )

    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        )


def test_inbound_patient_messages_are_ignored_entirely(tmp_path, engine, sessions) -> None:
    """§29: inbound chat is a non-goal, and a handler that parsed it would be half an inbox."""
    repos = build_repos(sessions)
    notification_id, _wamid, _ = _sent_notification(sessions, repos, "Lab WIN", "win")
    app = _webhook_app(tmp_path, engine)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA_ID,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                            "messages": [
                                {
                                    "from": SYNTHETIC_DESTINATION.lstrip("+"),
                                    "id": "wamid.INBOUND",
                                    "type": "text",
                                    "text": {"body": "mon résultat est-il normal ?"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode("utf-8")

    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 200
        )

    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        )
        assert (
            session.scalars(
                select(PatientNotification).where(
                    PatientNotification.provider_message_id == "wamid.INBOUND"
                )
            ).first()
            is None
        ), "an inbound message must never create a notification row"


def test_the_webhook_fails_closed_when_meta_is_not_configured(tmp_path, engine) -> None:
    """The ordinary state for a deployment with notifications off: every request refused."""
    settings = _settings(tmp_path)
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))
    raw = _status_payload("wamid.X", "delivered")
    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=raw, headers=_signed(raw)
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/notifications/v1/meta/webhook",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": VERIFY_TOKEN,
                    "hub.challenge": "x",
                },
            ).status_code
            == 403
        )


def test_an_oversized_callback_is_refused_before_it_is_parsed(tmp_path, engine) -> None:
    """One byte over the bound is refused.

    DH-NOTIFY-META-001R2 kept this test and stopped relying on it. It passed against the original
    implementation, which called ``await request.body()`` and measured the result afterwards -- a
    413 says the limit is enforced *somewhere*, and says nothing at all about whether DHealth asked
    for the whole body first. `TestClient` has already assembled the payload by the time the route
    runs, so this shape of test structurally cannot see the defect. The tests below can.
    """
    app = _webhook_app(tmp_path, engine)
    raw = b"x" * (256 * 1024 + 1)
    with TestClient(app) as client:
        response = client.post("/notifications/v1/meta/webhook", content=raw, headers=_signed(raw))
    assert response.status_code == 413
    # Nothing of the request may come back: the body is attacker-controlled and unauthenticated.
    assert response.json() == {"detail": "payload too large"}


# ---------------------------------------------------------------------------------------------
# DH-NOTIFY-META-001R2: the callback body is bounded WHILE it is read, not measured afterwards.
#
# The original code was `raw = await request.body()` followed by `if len(raw) > MAX`. Its own
# comment said an unauthenticated caller "must not be able to make the server allocate arbitrary
# memory before the signature has even been checked", and the code did the opposite: it asked
# Starlette to aggregate everything the caller chose to send, and consulted the limit only once it
# had all of it.
#
# What is proven below is a property of DHealth's own code: it never requests the whole body, and
# it never accumulates more than the limit it advertises. What is NOT proven, anywhere, is
# anything about allocation inside uvicorn, Caddy or the kernel -- a single ASGI message can arrive
# larger than the entire bound, and no application code can prevent that.
# ---------------------------------------------------------------------------------------------

MAX_BODY = 256 * 1024


def _asgi_request(chunks: list[bytes]) -> tuple[Request, list[int]]:
    """A real Starlette ``Request`` over a receive channel this test controls.

    ``consumed`` records the size of every chunk the application actually asked for, which is the
    observation that distinguishes a bounded reader from a buffering one: a reader that gives up at
    the bound never asks for what follows.
    """
    consumed: list[int] = []
    remaining = list(chunks)

    async def receive() -> dict[str, Any]:
        if not remaining:
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk = remaining.pop(0)
        consumed.append(len(chunk))
        return {"type": "http.request", "body": chunk, "more_body": bool(remaining)}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/notifications/v1/meta/webhook",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
    }
    return Request(scope, receive), consumed


def test_the_bounded_reader_returns_the_exact_bytes_it_was_given() -> None:
    """A. Below the bound: every byte, in order, unmodified. These are the bytes Meta signed."""
    chunks = [b'{"object":"whats', b"app_business_acc", b'ount","entry":[]}']
    request, consumed = _asgi_request(chunks)

    raw = asyncio.run(_read_bounded_body(request, max_bytes=MAX_BODY))

    assert raw == b"".join(chunks)
    assert consumed == [len(chunk) for chunk in chunks]


def test_the_bounded_reader_accepts_exactly_the_maximum() -> None:
    """B. The bound is inclusive: exactly 256 KiB is a legal callback, not a rejected one."""
    chunks = [b"x" * (64 * 1024)] * 4
    request, _consumed = _asgi_request(chunks)

    raw = asyncio.run(_read_bounded_body(request, max_bytes=MAX_BODY))

    assert len(raw) == MAX_BODY


def test_the_bounded_reader_stops_at_one_byte_over_and_never_reads_the_rest() -> None:
    """C + D. The load-bearing test of this ticket.

    256 KiB arrives in four chunks, then one byte crosses the bound, then eight megabytes follow.
    The eight megabytes must never be requested: DHealth has already decided, and reading on to
    discover a final length it will never use is the whole defect.

    ``await request.body()`` drains the stream to completion, so under the original implementation
    the last chunk *is* consumed and this assertion fails. That is what makes this test evidence
    rather than decoration -- see NC-BODY-1.
    """
    never_read = b"z" * (8 * 1024 * 1024)
    chunks = [b"x" * (64 * 1024)] * 4 + [b"!", never_read]
    request, consumed = _asgi_request(chunks)

    with pytest.raises(CallbackTooLarge):
        asyncio.run(_read_bounded_body(request, max_bytes=MAX_BODY))

    # Five chunks offered and taken; the sixth never asked for.
    assert consumed == [64 * 1024] * 4 + [1]
    assert len(never_read) not in consumed
    assert sum(consumed) <= MAX_BODY + 1


def test_the_bounded_reader_refuses_a_single_chunk_larger_than_the_bound() -> None:
    """D. One oversized chunk is refused without being accumulated.

    The honest limit of this: by the time the application sees it, that chunk already exists --
    the ASGI server built it. DHealth declining to copy it into its own buffer is all application
    code can do, and is what this asserts.
    """
    request, consumed = _asgi_request([b"y" * (MAX_BODY + 1), b"more"])

    with pytest.raises(CallbackTooLarge):
        asyncio.run(_read_bounded_body(request, max_bytes=MAX_BODY))

    assert consumed == [MAX_BODY + 1]


def _padded_callback(total_bytes: int) -> bytes:
    """A real Meta-shaped callback padded with insignificant JSON whitespace to an exact length.

    Whitespace between tokens, so the payload stays valid and semantically identical while its raw
    encoding reaches the byte count the bound is defined in.
    """
    base = _status_payload("wamid.SYNTHETIC-PADDED", "delivered")
    assert base.startswith(b"{")
    padding = total_bytes - len(base)
    assert padding >= 0, "base payload already exceeds the requested size"
    return b"{" + b" " * padding + base[1:]


def test_a_callback_of_exactly_the_maximum_is_accepted_over_http(tmp_path, engine) -> None:
    """B, through the real route: exactly 256 KiB is not refused for size."""
    raw = _padded_callback(MAX_BODY)
    assert len(raw) == MAX_BODY
    assert json.loads(raw)["object"] == "whatsapp_business_account"

    app = _webhook_app(tmp_path, engine)
    with TestClient(app) as client:
        response = client.post("/notifications/v1/meta/webhook", content=raw, headers=_signed(raw))

    assert response.status_code == 200, response.text


def test_a_callback_one_byte_over_the_maximum_is_refused_over_http(tmp_path, engine) -> None:
    """C, through the real route. The pair with the test above is what pins the boundary."""
    raw = _padded_callback(MAX_BODY + 1)
    assert len(raw) == MAX_BODY + 1

    app = _webhook_app(tmp_path, engine)
    with TestClient(app) as client:
        response = client.post("/notifications/v1/meta/webhook", content=raw, headers=_signed(raw))

    assert response.status_code == 413


def test_a_signature_does_not_carry_across_a_byte_different_encoding(tmp_path, engine) -> None:
    """E. The HMAC input is the raw bytes, not the parsed object.

    Two encodings of the *same* JSON: one compact, one with whitespace. They parse to equal
    dictionaries, so any validator working from parsed or canonicalised content would accept the
    signature of one for the other. Meta's scheme does not, and neither does this route.
    """
    payload = json.loads(_status_payload("wamid.SYNTHETIC-ENCODING", "delivered"))
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    spaced = json.dumps(payload, indent=2).encode("utf-8")
    assert compact != spaced
    assert json.loads(compact) == json.loads(spaced)

    app = _webhook_app(tmp_path, engine)
    with TestClient(app) as client:
        signed_for_compact = _signed(compact)
        assert (
            client.post(
                "/notifications/v1/meta/webhook", content=compact, headers=signed_for_compact
            ).status_code
            == 200
        )
        # Same signature, same meaning, different bytes.
        reused = client.post(
            "/notifications/v1/meta/webhook", content=spaced, headers=signed_for_compact
        )

    assert reused.status_code == 403
    assert reused.json() == {"detail": "invalid provider signature"}


def test_a_plausible_but_unsigned_callback_changes_nothing(tmp_path, engine, sessions) -> None:
    """F. Signature precedes trust: a well-formed payload naming a real wamid is still refused."""
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab R2F", "r2f")
    raw = _status_payload(wamid, "delivered")

    app = _webhook_app(tmp_path, engine)
    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/meta/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
        )

    assert response.status_code == 403
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        )


def test_an_oversized_callback_naming_a_real_wamid_changes_nothing(
    tmp_path, engine, sessions
) -> None:
    """G. The size refusal happens before the payload is anything but bytes.

    The body is a genuine, correctly signed receipt for a live notification, padded past the bound.
    If the row moved, the route would have parsed and applied something it had already decided was
    too large to read.
    """
    repos = build_repos(sessions)
    notification_id, wamid, _ = _sent_notification(sessions, repos, "Lab R2G", "r2g")
    base = _status_payload(wamid, "delivered")
    raw = b"{" + b" " * (MAX_BODY + 1 - len(base)) + base[1:]
    assert len(raw) == MAX_BODY + 1

    app = _webhook_app(tmp_path, engine)
    with TestClient(app) as client:
        response = client.post("/notifications/v1/meta/webhook", content=raw, headers=_signed(raw))

    assert response.status_code == 413
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        )


# ---------------------------------------------------------------------------------------------
# §4: the three configuration states
# ---------------------------------------------------------------------------------------------


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "TZ": "Africa/Djibouti",
        "ADAPTER_DB_HOST": "db",
        "ADAPTER_DB_NAME": "adapter",
        "ADAPTER_DB_USER": "adapter",
        "ADAPTER_DB_PASSWORD": "x",
    }
    base.update(overrides)
    return base


def _enabled_env(**overrides: str) -> dict[str, str]:
    """A fully-configured Meta deployment. ``overrides`` replace rather than duplicate, so a test
    can both *add* a variable and *change* one of the defaults below."""
    enabled = {
        "DHEALTH_NOTIFICATIONS_ENABLED": "true",
        "DHEALTH_PATIENT_PUBLIC_BASE_URL": "https://dhealth.example",
        "META_WHATSAPP_ACCESS_TOKEN": ACCESS_TOKEN,
        "META_WHATSAPP_PHONE_NUMBER_ID": PHONE_NUMBER_ID,
        "META_GRAPH_API_VERSION": GRAPH_VERSION,
        "META_TEMPLATE_LANGUAGE": "fr",
        "META_TEMPLATE_PATIENT_ACCESS_FR": ACCESS_TEMPLATE,
        "META_TEMPLATE_PATIENT_UPDATE_FR": UPDATE_TEMPLATE,
    }
    enabled.update(overrides)
    return _env(**enabled)


def test_notifications_disabled_needs_no_credentials_at_all() -> None:
    settings = Settings.from_env(_env())
    assert settings.notifications_enabled is False
    assert settings.build_notification_transport() is None
    assert settings.notification_receipts_configured is False


@pytest.mark.parametrize(
    "missing",
    [
        "DHEALTH_PATIENT_PUBLIC_BASE_URL",
        "META_WHATSAPP_ACCESS_TOKEN",
        "META_WHATSAPP_PHONE_NUMBER_ID",
        "META_GRAPH_API_VERSION",
        "META_TEMPLATE_LANGUAGE",
        "META_TEMPLATE_PATIENT_ACCESS_FR",
        "META_TEMPLATE_PATIENT_UPDATE_FR",
    ],
)
def test_enabled_but_incomplete_is_a_startup_refusal_naming_the_variable(missing) -> None:
    """§4: a half-configured service that boots green is what makes "disabled" and "broken" look
    the same. Refusing at startup, by name, is what keeps them different."""
    env = _enabled_env()
    env.pop(missing)
    with pytest.raises(ConfigurationError, match=missing):
        Settings.from_env(env)


def test_a_plaintext_public_base_url_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="https"):
        Settings.from_env(_enabled_env(DHEALTH_PATIENT_PUBLIC_BASE_URL="http://dhealth.example"))


def test_a_malformed_graph_version_is_refused_even_when_disabled() -> None:
    """Shape-checked whenever supplied, not only when enabled -- a typo found at boot beats one
    found at the first patient handoff."""
    with pytest.raises(ConfigurationError, match="META_GRAPH_API_VERSION"):
        Settings.from_env(_env(META_GRAPH_API_VERSION="23.0"))


def test_twilio_is_no_longer_a_provider_this_build_can_select() -> None:
    """ADR-059 removed it. Naming it is a startup refusal, never a silent fallback."""
    with pytest.raises(ConfigurationError, match="DHEALTH_NOTIFICATION_PROVIDER"):
        Settings.from_env(_env(DHEALTH_NOTIFICATION_PROVIDER="twilio"))


def test_a_malformed_default_region_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="alpha-2"):
        Settings.from_env(_env(DHEALTH_NOTIFICATION_DEFAULT_REGION="Djibouti"))


def test_enabled_and_complete_builds_a_meta_transport_that_cannot_send_sms() -> None:
    settings = Settings.from_env(
        _enabled_env(META_APP_SECRET=APP_SECRET, META_WEBHOOK_VERIFY_TOKEN=VERIFY_TOKEN)
    )
    transport = settings.build_notification_transport()
    assert transport is not None
    assert transport.provider_name == "meta_whatsapp"
    assert transport.graph_api_version == GRAPH_VERSION
    assert transport.supports(NotificationChannel.WHATSAPP) is True
    assert transport.supports(NotificationChannel.SMS) is False, (
        "§6: no SMS provider is selected, and the transport must not pretend otherwise"
    )
    assert settings.notification_receipts_configured is True


def test_receipts_need_both_the_app_secret_and_the_verify_token() -> None:
    only_secret = Settings.from_env(_enabled_env(META_APP_SECRET=APP_SECRET))
    only_token = Settings.from_env(_enabled_env(META_WEBHOOK_VERIFY_TOKEN=VERIFY_TOKEN))
    assert only_secret.notification_receipts_configured is False
    assert only_token.notification_receipts_configured is False
