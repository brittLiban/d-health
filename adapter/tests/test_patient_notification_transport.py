"""DH-NOTIFY-001 §23 groups B and C — the Twilio adapter, and the webhook that may not be spoofed.

Group B drives the **real** ``TwilioNotificationTransport`` against an injected stub HTTP client,
which is what makes a failing test mean this adapter is wrong rather than that a mock drifted. The
one thing it never does is reach the network.

Group C drives the **real** webhook route through the **real** ``create_app`` assembly, so the
signature check under test is the one a deployment actually mounts. ``NC-NOTIFY-5`` removes that
check, and ``test_c2``/``test_c3`` are what must go red.

Also here, and load-bearing for §10: the configuration matrix. "Notifications disabled" and
"notifications enabled but incomplete" are operationally different states, and the second is a
refusal to start rather than a service that boots green and silently sends nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from exchange_support import make_lab
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
from daher_adapter.exchange.models import DoctorReview, LabOrder, ResultDelivery
from daher_adapter.notify.dispatcher import PatientNotificationDispatcher
from daher_adapter.notify.fake import FakeNotificationTransport
from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationFailureCategory,
    NotificationState,
    PatientNotification,
    PatientNotificationAttempt,
)
from daher_adapter.notify.transport import TransportFailure
from daher_adapter.notify.twilio import TwilioNotificationTransport
from daher_adapter.notify.webhook import twilio_signature

CALLBACK_URL = "https://dhealth.example/notifications/v1/twilio/status"
AUTH_TOKEN = "unit-test-token-not-a-real-credential"  # noqa: S105 - fixture value


# ---------------------------------------------------------------------------------------------
# Group B: the Twilio adapter
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

    def post(self, url: str, *, data: dict, auth: tuple[str, str], timeout: float) -> _StubResponse:
        self.calls.append({"url": url, "data": data, "auth": auth, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _transport(response, **overrides) -> tuple[TwilioNotificationTransport, _StubClient]:
    client = _StubClient(response=response)
    kwargs = dict(
        account_sid="AC-test",
        auth_token=AUTH_TOKEN,
        sms_from="+15550000001",
        whatsapp_from="+15550000002",
        status_callback_url=CALLBACK_URL,
        client=client,
    )
    kwargs.update(overrides)
    return TwilioNotificationTransport(**kwargs), client


def test_b1_correct_provider_request_shape() -> None:
    transport, client = _transport(_StubResponse(201, {"sid": "SM123", "status": "queued"}))

    accepted = transport.send(
        channel=NotificationChannel.SMS, destination=SYNTHETIC_DESTINATION, body="hello"
    )

    assert accepted.provider_message_id == "SM123"
    assert accepted.provider_status == "queued"
    call = client.calls[0]
    assert call["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC-test/Messages.json"
    assert call["data"]["To"] == SYNTHETIC_DESTINATION
    assert call["data"]["From"] == "+15550000001"
    assert call["data"]["Body"] == "hello"
    assert call["data"]["StatusCallback"] == CALLBACK_URL
    assert call["auth"] == ("AC-test", AUTH_TOKEN)


def test_b1_whatsapp_uses_the_channel_prefixed_address() -> None:
    """The one Twilio-specific convention in the codebase, and it lives only here."""
    transport, client = _transport(_StubResponse(201, {"sid": "SM124", "status": "queued"}))

    transport.send(
        channel=NotificationChannel.WHATSAPP, destination=SYNTHETIC_DESTINATION, body="hi"
    )

    assert client.calls[0]["data"]["To"] == f"whatsapp:{SYNTHETIC_DESTINATION}"
    assert client.calls[0]["data"]["From"] == "+15550000002"


def test_b2_timeout_is_transient() -> None:
    transport, _ = _transport(TimeoutError("read timeout"))
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(channel=NotificationChannel.SMS, destination=SYNTHETIC_DESTINATION, body="x")
    assert excinfo.value.category is NotificationFailureCategory.TRANSIENT_PROVIDER


@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    [
        (401, {}, NotificationFailureCategory.AUTHENTICATION),  # B3
        (403, {}, NotificationFailureCategory.AUTHENTICATION),
        (429, {}, NotificationFailureCategory.RATE_LIMIT),  # B4
        (500, {}, NotificationFailureCategory.TRANSIENT_PROVIDER),  # B5
        (503, {}, NotificationFailureCategory.TRANSIENT_PROVIDER),
        (400, {"code": 21211}, NotificationFailureCategory.INVALID_DESTINATION),  # B6
        (400, {"code": 21614}, NotificationFailureCategory.INVALID_DESTINATION),
        (400, {"code": 21610}, NotificationFailureCategory.PROVIDER_REJECTED),
        (400, {"code": 21606}, NotificationFailureCategory.CONFIGURATION),
        (400, {"code": 63018}, NotificationFailureCategory.RATE_LIMIT),
        (400, {}, NotificationFailureCategory.PROVIDER_REJECTED),  # unrecognised 4xx
        (418, {"code": "99999"}, NotificationFailureCategory.PROVIDER_REJECTED),
    ],
)
def test_b3_to_b6_error_classification(status_code, payload, expected) -> None:
    transport, _ = _transport(_StubResponse(status_code, payload))
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(channel=NotificationChannel.SMS, destination=SYNTHETIC_DESTINATION, body="x")
    assert excinfo.value.category is expected
    assert excinfo.value.http_status_code == status_code


def test_b7_provider_response_is_sanitized() -> None:
    """The recorded detail is DHealth's own sentence with a status number, never their body.

    Twilio's error bodies echo ``To`` and ``Body``. For a ``patient_access_link`` message the body
    contains a live patient bearer credential, so quoting the provider here would write one into
    the database, the attempt history and every log that touches the row.
    """
    leaky = {
        "code": 21610,
        "message": f"From/To pair violates a blacklist rule. To={SYNTHETIC_DESTINATION}",
        "more_info": "https://www.twilio.com/docs/errors/21610",
        "body": "DHealth ... https://dhealth.example/?app=patient#access=SUPERSECRETTOKEN",
    }
    transport, _ = _transport(_StubResponse(400, leaky))

    with pytest.raises(TransportFailure) as excinfo:
        transport.send(channel=NotificationChannel.SMS, destination=SYNTHETIC_DESTINATION, body="x")

    detail = excinfo.value.detail
    assert detail == "provider rejected the request with HTTP 400"
    assert SYNTHETIC_DESTINATION not in detail
    assert "SUPERSECRETTOKEN" not in detail
    assert "blacklist" not in detail


def test_b7_a_non_json_error_body_is_still_classified() -> None:
    transport, _ = _transport(_StubResponse(503, ValueError("not json")))
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(channel=NotificationChannel.SMS, destination=SYNTHETIC_DESTINATION, body="x")
    assert excinfo.value.category is NotificationFailureCategory.TRANSIENT_PROVIDER


def test_b8_and_b9_acceptance_persists_the_message_id_as_sent_not_delivered(sessions) -> None:
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab B8")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="b8")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="b8")
    release(repos, context, key="b8")

    transport, _ = _transport(_StubResponse(201, {"sid": "SM-B8", "status": "queued"}))
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-b8").dispatch_once()

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
    assert row.provider_message_id == "SM-B8"
    assert row.state == NotificationState.SENT.value, "a 201 is acceptance, never delivery"
    assert row.delivered_at is None
    assert len(attempts) == 1
    assert attempts[0].outcome == "ACCEPTED"
    assert attempts[0].provider_status_code == 201


def test_b8_a_2xx_without_a_message_id_is_refused() -> None:
    """Recording SENT with no provider id would produce a row no receipt could ever resolve."""
    transport, _ = _transport(_StubResponse(201, {"status": "queued"}))
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(channel=NotificationChannel.SMS, destination=SYNTHETIC_DESTINATION, body="x")
    assert excinfo.value.category is NotificationFailureCategory.UNKNOWN


def test_an_unconfigured_channel_is_a_terminal_configuration_failure() -> None:
    transport, client = _transport(
        _StubResponse(201, {"sid": "x", "status": "queued"}), whatsapp_from=None
    )
    assert transport.supports(NotificationChannel.SMS)
    assert not transport.supports(NotificationChannel.WHATSAPP)
    with pytest.raises(TransportFailure) as excinfo:
        transport.send(
            channel=NotificationChannel.WHATSAPP, destination=SYNTHETIC_DESTINATION, body="x"
        )
    assert excinfo.value.category is NotificationFailureCategory.CONFIGURATION
    assert client.calls == [], "no provider call is made for a channel this deployment cannot send"


def test_twilio_refuses_to_construct_without_credentials_or_a_sender() -> None:
    with pytest.raises(ValueError, match="account SID"):
        TwilioNotificationTransport(
            account_sid="", auth_token=AUTH_TOKEN, sms_from="+1", whatsapp_from=None
        )
    with pytest.raises(ValueError, match="at least one sender"):
        TwilioNotificationTransport(
            account_sid="AC", auth_token=AUTH_TOKEN, sms_from=None, whatsapp_from=None
        )


# ---------------------------------------------------------------------------------------------
# Group C: the webhook
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
        twilio_auth_token=AUTH_TOKEN,
        notification_status_callback_url=CALLBACK_URL,
        **overrides,
    )
    return create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))


def _signed(params: dict[str, str], *, token: str = AUTH_TOKEN, url: str = CALLBACK_URL) -> str:
    return twilio_signature(token, url, params)


def _sent_notification(sessions, repos, lab_name: str, key: str) -> tuple[uuid.UUID, str, Any]:
    """An order released, dispatched and accepted by the provider -- a row a receipt can hit."""
    lab = make_lab(repos.organizations, name=lab_name)
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    enrol(repos, sessions, context)
    enter_result(repos, context, key=key)
    release(repos, context, key=key)
    transport = FakeNotificationTransport()
    PatientNotificationDispatcher(
        sessions, transport=transport, worker_id=f"w-{key}"
    ).dispatch_once()
    with sessions() as session:
        row = session.scalars(
            select(PatientNotification).where(PatientNotification.lab_order_id == context.order_id)
        ).one()
        assert row.state == NotificationState.SENT.value
        return row.id, row.provider_message_id, context


def test_c1_valid_signature_marks_delivered(tmp_path, engine, sessions) -> None:
    repos = build_repos(sessions)
    notification_id, sid, _ = _sent_notification(sessions, repos, "Lab C1", "c1")
    app = _webhook_app(tmp_path, engine)

    params = {"MessageSid": sid, "MessageStatus": "delivered"}
    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/twilio/status",
            data=params,
            headers={"X-Twilio-Signature": _signed(params)},
        )

    assert response.status_code == 204
    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
    assert row.state == NotificationState.DELIVERED.value
    assert row.delivered_at is not None


def test_c2_invalid_signature_is_rejected_and_changes_nothing(tmp_path, engine, sessions) -> None:
    repos = build_repos(sessions)
    notification_id, sid, _ = _sent_notification(sessions, repos, "Lab C2", "c2")
    app = _webhook_app(tmp_path, engine)

    params = {"MessageSid": sid, "MessageStatus": "delivered"}
    forged = base64.b64encode(
        hmac.new(b"wrong-token", b"anything", hashlib.sha1).digest()  # noqa: S324
    ).decode()
    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/twilio/status",
            data=params,
            headers={"X-Twilio-Signature": forged},
        )

    assert response.status_code == 403
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        ), "a spoofed callback must not be able to mark anything delivered"


def test_c3_missing_signature_is_rejected(tmp_path, engine, sessions) -> None:
    repos = build_repos(sessions)
    notification_id, sid, _ = _sent_notification(sessions, repos, "Lab C3", "c3")
    app = _webhook_app(tmp_path, engine)

    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/twilio/status",
            data={"MessageSid": sid, "MessageStatus": "delivered"},
        )

    assert response.status_code == 403
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        )


def test_c3_a_signature_computed_over_a_different_url_is_rejected(
    tmp_path, engine, sessions
) -> None:
    """§17's real attack: the signed URL is configuration, never the request's ``Host`` header.

    If validation used the incoming host, anyone able to set that header could produce a signature
    that verifies. Here the attacker signs correctly for *their* URL and is still refused.
    """
    repos = build_repos(sessions)
    _, sid, _ = _sent_notification(sessions, repos, "Lab C3B", "c3b")
    app = _webhook_app(tmp_path, engine)

    params = {"MessageSid": sid, "MessageStatus": "delivered"}
    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/twilio/status",
            data=params,
            headers={
                "X-Twilio-Signature": _signed(params, url="https://attacker.example/callback"),
                "Host": "attacker.example",
            },
        )
    assert response.status_code == 403


def test_c4_duplicate_callbacks_are_idempotent(tmp_path, engine, sessions) -> None:
    repos = build_repos(sessions)
    notification_id, sid, _ = _sent_notification(sessions, repos, "Lab C4", "c4")
    app = _webhook_app(tmp_path, engine)

    params = {"MessageSid": sid, "MessageStatus": "delivered"}
    with TestClient(app) as client:
        for _ in range(3):
            assert (
                client.post(
                    "/notifications/v1/twilio/status",
                    data=params,
                    headers={"X-Twilio-Signature": _signed(params)},
                ).status_code
                == 204
            )

    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
        first_delivered_at = row.delivered_at
        attempts = list(
            session.scalars(
                select(PatientNotificationAttempt).where(
                    PatientNotificationAttempt.patient_notification_id == notification_id
                )
            ).all()
        )
    assert row.state == NotificationState.DELIVERED.value
    assert first_delivered_at is not None
    assert len(attempts) == 1, "a receipt is not a hand-over and never writes an attempt row"


def test_c5_unknown_message_id_fails_safely(tmp_path, engine, sessions) -> None:
    """Acknowledged, and it creates nothing.

    A 4xx would make the provider retry forever; creating a row would let an authenticated but
    mistaken callback bring a notification into existence that DHealth never owed.
    """
    app = _webhook_app(tmp_path, engine)
    params = {"MessageSid": "SM-does-not-exist", "MessageStatus": "delivered"}
    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/twilio/status",
            data=params,
            headers={"X-Twilio-Signature": _signed(params)},
        )
    assert response.status_code == 204
    with sessions() as session:
        assert session.scalars(select(PatientNotification)).all() == []


def test_c6_a_late_status_cannot_regress_a_terminal_state(tmp_path, engine, sessions) -> None:
    """Providers replay and reorder. A ``sent`` arriving after ``delivered`` must change nothing,
    and a ``failed`` arriving after ``delivered`` must not undo a delivery that happened."""
    repos = build_repos(sessions)
    notification_id, sid, _ = _sent_notification(sessions, repos, "Lab C6", "c6")
    app = _webhook_app(tmp_path, engine)

    with TestClient(app) as client:
        for status_value in ("delivered", "sent", "queued", "failed", "undelivered"):
            params = {"MessageSid": sid, "MessageStatus": status_value}
            assert (
                client.post(
                    "/notifications/v1/twilio/status",
                    data=params,
                    headers={"X-Twilio-Signature": _signed(params)},
                ).status_code
                == 204
            )

    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
    assert row.state == NotificationState.DELIVERED.value
    assert row.delivered_at is not None


def test_c6_a_non_terminal_status_leaves_a_sent_row_alone(tmp_path, engine, sessions) -> None:
    repos = build_repos(sessions)
    notification_id, sid, _ = _sent_notification(sessions, repos, "Lab C6B", "c6b")
    app = _webhook_app(tmp_path, engine)

    params = {"MessageSid": sid, "MessageStatus": "sending"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/twilio/status",
                data=params,
                headers={"X-Twilio-Signature": _signed(params)},
            ).status_code
            == 204
        )
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        )


def test_undelivered_marks_failed(tmp_path, engine, sessions) -> None:
    repos = build_repos(sessions)
    notification_id, sid, _ = _sent_notification(sessions, repos, "Lab CUD", "cud")
    app = _webhook_app(tmp_path, engine)

    params = {"MessageSid": sid, "MessageStatus": "undelivered"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/twilio/status",
                data=params,
                headers={"X-Twilio-Signature": _signed(params)},
            ).status_code
            == 204
        )
    with sessions() as session:
        row = session.get(PatientNotification, notification_id)
    assert row.state == NotificationState.FAILED.value
    assert row.failure_category == NotificationFailureCategory.PROVIDER_REJECTED.value
    assert row.delivered_at is None


def test_c7_webhook_cannot_alter_clinical_state(tmp_path, engine, sessions) -> None:
    """A snapshot diff across an authenticated, *successful* callback.

    The callback below genuinely works -- the notification reaches ``DELIVERED`` -- so the equality
    below is not satisfied by nothing having happened.
    """
    repos = build_repos(sessions)
    notification_id, sid, context = _sent_notification(sessions, repos, "Lab C7", "c7")

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
            }

    before = snapshot()
    app = _webhook_app(tmp_path, engine)
    params = {"MessageSid": sid, "MessageStatus": "delivered"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/twilio/status",
                data=params,
                headers={"X-Twilio-Signature": _signed(params)},
            ).status_code
            == 204
        )

    assert snapshot() == before
    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.DELIVERED.value
        )


def test_c8_payload_cannot_choose_tenant_authority(tmp_path, engine, sessions) -> None:
    """Resolution is by provider message id and nothing else.

    The forged payload below carries a real order id, a real provider id and an ``AccountSid``,
    all correctly signed. None of them is read, so the message id it names -- which belongs to no
    notification -- is what decides the outcome: nothing happens.
    """
    repos = build_repos(sessions)
    notification_id, sid, context = _sent_notification(sessions, repos, "Lab C8", "c8")
    app = _webhook_app(tmp_path, engine)

    params = {
        "MessageSid": "SM-not-ours",
        "MessageStatus": "delivered",
        "AccountSid": "AC-attacker",
        "organization_id": str(context.provider_id),
        "lab_order_id": str(context.order_id),
        "patient_notification_id": str(notification_id),
    }
    with TestClient(app) as client:
        assert (
            client.post(
                "/notifications/v1/twilio/status",
                data=params,
                headers={"X-Twilio-Signature": _signed(params)},
            ).status_code
            == 204
        )

    with sessions() as session:
        assert session.get(PatientNotification, notification_id).state == (
            NotificationState.SENT.value
        ), "an id in the payload is not authority over the row it names"


def test_webhook_fails_closed_when_receipts_are_not_configured(tmp_path, engine, sessions) -> None:
    """The ordinary state for a deployment with notifications off: every request refused."""
    settings = _settings(tmp_path)
    app = create_app(settings, engine=engine, audit_writer=AuditWriter(settings.audit_path))
    params = {"MessageSid": "SM1", "MessageStatus": "delivered"}
    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/twilio/status",
            data=params,
            headers={"X-Twilio-Signature": _signed(params)},
        )
    assert response.status_code == 403


def test_an_authenticated_but_malformed_callback_is_a_400(tmp_path, engine) -> None:
    app = _webhook_app(tmp_path, engine)
    params = {"MessageStatus": "delivered"}
    with TestClient(app) as client:
        response = client.post(
            "/notifications/v1/twilio/status",
            data=params,
            headers={"X-Twilio-Signature": _signed(params)},
        )
    assert response.status_code == 400


# ---------------------------------------------------------------------------------------------
# §10: the three configuration states
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


def test_notifications_disabled_needs_no_credentials_at_all() -> None:
    settings = Settings.from_env(_env())
    assert settings.notifications_enabled is False
    assert settings.build_notification_transport() is None
    assert settings.notification_receipts_configured is False


@pytest.mark.parametrize(
    ("missing", "match"),
    [
        ("DHEALTH_PATIENT_PUBLIC_BASE_URL", "DHEALTH_PATIENT_PUBLIC_BASE_URL"),
        ("TWILIO_ACCOUNT_SID", "TWILIO_ACCOUNT_SID"),
        ("TWILIO_AUTH_TOKEN", "TWILIO_AUTH_TOKEN"),
    ],
)
def test_enabled_but_incomplete_is_a_startup_refusal_naming_the_variable(missing, match) -> None:
    """§10: a half-configured service that boots green is what makes "disabled" and "broken" look
    the same. Refusing at startup, by name, is what keeps them different."""
    env = _env(
        DHEALTH_NOTIFICATIONS_ENABLED="true",
        DHEALTH_PATIENT_PUBLIC_BASE_URL="https://dhealth.example",
        TWILIO_ACCOUNT_SID="AC-x",
        TWILIO_AUTH_TOKEN="tok",
        DHEALTH_SMS_FROM="+15550000001",
    )
    env.pop(missing)
    with pytest.raises(ConfigurationError, match=match):
        Settings.from_env(env)


def test_enabled_with_no_sender_at_all_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="DHEALTH_SMS_FROM"):
        Settings.from_env(
            _env(
                DHEALTH_NOTIFICATIONS_ENABLED="true",
                DHEALTH_PATIENT_PUBLIC_BASE_URL="https://dhealth.example",
                TWILIO_ACCOUNT_SID="AC-x",
                TWILIO_AUTH_TOKEN="tok",
            )
        )


def test_a_plaintext_public_base_url_is_refused() -> None:
    """A patient access URL carries a live bearer credential in its fragment; over http:// that
    fragment is exposed before TLS would have protected it."""
    with pytest.raises(ConfigurationError, match="https"):
        Settings.from_env(
            _env(
                DHEALTH_NOTIFICATIONS_ENABLED="true",
                DHEALTH_PATIENT_PUBLIC_BASE_URL="http://dhealth.example",
                TWILIO_ACCOUNT_SID="AC-x",
                TWILIO_AUTH_TOKEN="tok",
                DHEALTH_SMS_FROM="+15550000001",
            )
        )


def test_an_unknown_provider_is_refused_rather_than_silently_defaulted() -> None:
    with pytest.raises(ConfigurationError, match="DHEALTH_NOTIFICATION_PROVIDER"):
        Settings.from_env(_env(DHEALTH_NOTIFICATION_PROVIDER="meta"))


def test_a_malformed_default_region_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="alpha-2"):
        Settings.from_env(_env(DHEALTH_NOTIFICATION_DEFAULT_REGION="Djibouti"))


def test_enabled_and_complete_builds_a_twilio_transport() -> None:
    settings = Settings.from_env(
        _env(
            DHEALTH_NOTIFICATIONS_ENABLED="true",
            DHEALTH_PATIENT_PUBLIC_BASE_URL="https://dhealth.example",
            TWILIO_ACCOUNT_SID="AC-x",
            TWILIO_AUTH_TOKEN="tok",
            DHEALTH_SMS_FROM="+15550000001",
            DHEALTH_NOTIFICATION_STATUS_CALLBACK_URL=CALLBACK_URL,
        )
    )
    transport = settings.build_notification_transport()
    assert transport is not None
    assert transport.provider_name == "twilio"
    assert transport.supports(NotificationChannel.SMS)
    assert not transport.supports(NotificationChannel.WHATSAPP)
    assert settings.notification_receipts_configured is True
