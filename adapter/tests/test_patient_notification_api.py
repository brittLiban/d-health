"""DH-NOTIFY-001 §5/§17/§20 — the HTTP surface: consent, authorization, and the inline link send.

Same harness pattern as ``test_exchange_patient_access.py``: a bare ``FastAPI()`` with
``build_exchange_router`` mounted directly and a ``StaticKeySource``-backed validator, so these
tests exercise the real authorization dependencies without a network or a Keycloak.

The ones that matter most:

* ``test_a17_*`` -- a clinician in organization A cannot enrol, read, or retry organization B's
  order, and possession of a notification UUID is not authority. ``NC-NOTIFY-7`` turns these red.
* ``test_consent_*`` -- ``patient_consent_confirmed`` is ``Literal[True]``, so "false" and "absent"
  are both 422s at the schema boundary, before any handler decides anything.
* ``test_link_*`` -- the access-link message is composed from configured deployment state, carries
  the fragment form, and the raw token still never reaches storage.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import (
    make_lab,
    make_patient_reference,
    make_practitioner_reference,
    make_priced_offering,
    make_provider,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent, PatientAccessGrant
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.notify.fake import FakeNotificationTransport
from daher_adapter.notify.models import (
    NotificationState,
    PatientNotification,
    PatientNotificationEnrollment,
)

TTL_HOURS = 168
BASE_URL = "https://dhealth.example"
DESTINATION = "+25377123456"


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def transport() -> FakeNotificationTransport:
    return FakeNotificationTransport()


def _make_client(
    sessions, keypair, tmp_path: Path, *, transport=None, base_url: str | None = BASE_URL
) -> TestClient:
    _, public_key = keypair
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
        audit_writer=ExchangeAuditWriter(tmp_path / "exchange-audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
        patient_access_ttl_hours=TTL_HOURS,
        notification_transport=transport,
        patient_public_base_url=base_url,
        notification_receipts_configured=transport is not None,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def client(sessions, keypair, tmp_path, transport) -> TestClient:
    return _make_client(sessions, keypair, tmp_path, transport=transport)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap_identity(client: TestClient, private_key, subject: str) -> str:
    response = client.get(
        "/exchange/v1/me", headers=_auth(make_token(private_key, subject=subject))
    )
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _grant_roles(client: TestClient, private_key, org_id, identity_id, roles: list[str]) -> None:
    admin = make_token(private_key, subject=f"admin-{org_id}", roles=[PLATFORM_ADMIN_ROLE])
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(admin),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text


def _clinician(client, private_key, organizations, name: str, *, roles=None):
    provider = make_provider(organizations, name=name)
    identity_id = _bootstrap_identity(client, private_key, f"clinician-{name}")
    _grant_roles(client, private_key, provider.id, identity_id, roles or ["CLINICIAN"])
    return provider, make_token(private_key, subject=f"clinician-{name}")


def _assigned_order(sessions, organizations, provider, *, key: str) -> uuid.UUID:
    """An order with a settled lab, so issuance yields an ``ORDER_READ`` grant (DH-EX-010A)."""
    from decimal import Decimal

    from daher_adapter.exchange.directory import LabDirectoryProfileRepository
    from daher_adapter.exchange.models import LabPrice
    from daher_adapter.exchange.selection import LabSelectionRepository

    catalog = CatalogRepository(sessions, organizations)
    lab = make_lab(organizations, name=f"Lab-{key}")
    LabDirectoryProfileRepository(sessions, organizations).upsert(
        lab_organization_id=lab.id,
        public_address="Addr",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    entry, offering = make_priced_offering(
        catalog, lab_organization_id=lab.id, amount=Decimal("100"), code=f"DH-{key}"
    )
    patient = make_patient_reference(
        PatientReferenceRepository(sessions), provider_org_id=provider.id, source_identity_value=key
    )
    practitioner = make_practitioner_reference(
        PractitionerReferenceRepository(sessions),
        provider_org_id=provider.id,
        source_identity_value=f"doc-{key}",
    )
    order = (
        LabOrderRepository(sessions, organizations, catalog)
        .create(
            idempotency_key=key,
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        )
        .order
    )
    with sessions() as session:
        price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering.id, LabPrice.superseded_at.is_(None)
            )
        )
    LabSelectionRepository(sessions, organizations).select(
        lab_order_id=order.id, lab_organization_id=lab.id, expected_price_ids=frozenset({price_id})
    )
    return order.id


def _issue_url(provider_id, order_id) -> str:
    return f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/patient-access"


def _notify_url(provider_id, order_id) -> str:
    return f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/notifications"


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


# -- consent and destination, at the schema boundary ---------------------------------------------


@pytest.mark.parametrize(
    "notify",
    [
        {"channel": "SMS", "destination": DESTINATION, "patient_consent_confirmed": False},
        {"channel": "SMS", "destination": DESTINATION},
        {"channel": "EMAIL", "destination": DESTINATION, "patient_consent_confirmed": True},
        {"channel": "SMS", "patient_consent_confirmed": True},
        {
            "channel": "SMS",
            "destination": DESTINATION,
            "patient_consent_confirmed": True,
            "note": "patient said ok",
        },
    ],
    ids=["consent-false", "consent-absent", "unsupported-channel", "no-destination", "extra-field"],
)
def test_consent_and_shape_are_enforced_before_any_handler_runs(
    client, keypair, organizations, sessions, notify
) -> None:
    """§5: consent is ``Literal[True]``, the channel set is closed, and ``extra="forbid"`` means a
    free-text note has nowhere to go -- which is where invented legal language would accumulate."""
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "consent")
    order_id = _assigned_order(sessions, organizations, provider, key="consent")

    response = client.post(
        _issue_url(provider.id, order_id), headers=_auth(token), json={"notify": notify}
    )

    assert response.status_code == 422, response.text
    with sessions() as session:
        assert session.scalars(select(PatientAccessGrant)).all() == [], (
            "a refused enrollment must not have issued a grant"
        )


def test_an_invalid_destination_is_refused_before_a_grant_exists(
    client, keypair, organizations, sessions, transport
) -> None:
    """§6: validation happens before issuance, so a typo costs a corrected digit rather than a live
    grant the clinician must now revoke."""
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "badnum")
    order_id = _assigned_order(sessions, organizations, provider, key="badnum")

    response = client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": "77123456",
                "patient_consent_confirmed": True,
            }
        },
    )

    assert response.status_code == 422
    assert "country code" in response.json()["detail"]
    with sessions() as session:
        assert session.scalars(select(PatientAccessGrant)).all() == []
        assert session.scalars(select(PatientNotification)).all() == []
    assert transport.sent == [], "nothing reached the provider"


def test_issuance_without_a_notify_block_is_unchanged(
    client, keypair, organizations, sessions, transport
) -> None:
    """Law 4 (§19): the QR/print path is the baseline and messaging is an addition.

    An issuance with no ``notify`` behaves exactly as it did before this ticket -- a grant, a raw
    token, no enrollment, no obligation, no provider call.
    """
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "plain")
    order_id = _assigned_order(sessions, organizations, provider, key="plain")

    response = client.post(_issue_url(provider.id, order_id), headers=_auth(token))

    assert response.status_code == 201, response.text
    assert response.json()["access_token"]
    with sessions() as session:
        assert session.scalars(select(PatientNotificationEnrollment)).all() == []
        assert session.scalars(select(PatientNotification)).all() == []
    assert transport.sent == []


# -- the inline link send -------------------------------------------------------------------------


def test_link_message_is_sent_inline_with_the_fragment_form(
    client, keypair, organizations, sessions, transport
) -> None:
    """§17: the credential travels in the fragment, and the origin is configured, not derived.

    ``NC-NOTIFY-4`` moves it into a query parameter, and this is the test that must go red.
    """
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "link")
    order_id = _assigned_order(sessions, organizations, provider, key="link")

    response = client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )

    assert response.status_code == 201, response.text
    raw = response.json()["access_token"]
    assert len(transport.sent) == 1
    body = transport.sent[0].body
    assert f"{BASE_URL}/?app=patient#access={raw}" in body
    assert f"?access={raw}" not in body, "the credential must never be a query parameter"
    assert f"&access={raw}" not in body
    # The same content boundary the release messages have: a link, and nothing clinical.
    assert str(order_id) not in body
    assert DESTINATION not in body

    with sessions() as session:
        rows = session.scalars(select(PatientNotification)).all()
        assert len(rows) == 1
        assert rows[0].state == NotificationState.SENT.value
        assert rows[0].event_type == "ACCESS_LINK_ISSUED"
        assert rows[0].release_version_id is None
        # And the invariant the whole A′ design exists to preserve.
        blobs = [str(getattr(rows[0], c.key)) for c in PatientNotification.__table__.columns]
        assert not any(raw in blob for blob in blobs)


def test_a_failed_link_send_is_terminal_and_never_fails_the_issuance(
    client, keypair, organizations, sessions, transport
) -> None:
    """The grant is already committed, and the clinician's browser holds the only copy of the raw
    token. Raising here would lose it -- so the message fails, the issuance succeeds, and the row
    says so."""
    from daher_adapter.notify.models import NotificationFailureCategory

    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "linkfail")
    order_id = _assigned_order(sessions, organizations, provider, key="linkfail")
    transport.fail_next(NotificationFailureCategory.TRANSIENT_PROVIDER)

    response = client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )

    assert response.status_code == 201, "the clinician still receives their link"
    assert response.json()["access_token"]
    with sessions() as session:
        row = session.scalars(select(PatientNotification)).one()
    assert row.state == NotificationState.FAILED.value, (
        "terminal even for a retryable category -- no worker can rebuild this URL"
    )


def test_enrollment_is_refused_when_notifications_are_disabled(
    sessions, keypair, tmp_path, organizations
) -> None:
    """§10: refusing beats accepting a consent record the deployment can never act on."""
    private_key, _ = keypair
    disabled = _make_client(sessions, keypair, tmp_path, transport=None, base_url=None)
    provider, token = _clinician(disabled, private_key, organizations, "disabled")
    order_id = _assigned_order(sessions, organizations, provider, key="disabled")

    response = disabled.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )

    assert response.status_code == 503
    with sessions() as session:
        assert session.scalars(select(PatientAccessGrant)).all() == []


def test_revoking_access_withdraws_the_enrollment_over_http(
    client, keypair, organizations, sessions, transport
) -> None:
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "revoke")
    order_id = _assigned_order(sessions, organizations, provider, key="revoke")
    client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )

    assert client.delete(_issue_url(provider.id, order_id), headers=_auth(token)).status_code == 204

    status_response = client.get(_notify_url(provider.id, order_id), headers=_auth(token))
    assert status_response.status_code == 200
    assert status_response.json()["enrolled"] is False
    assert status_response.json()["destination"] is None


# -- §20: authorization ----------------------------------------------------------------------------


def test_a17_a_clinician_cannot_enrol_another_organizations_order(
    client, keypair, organizations, sessions, transport
) -> None:
    private_key, _ = keypair
    provider_a, _token_a = _clinician(client, private_key, organizations, "orgA")
    provider_b, token_b = _clinician(client, private_key, organizations, "orgB")
    order_a = _assigned_order(sessions, organizations, provider_a, key="orgA")

    body = {
        "notify": {
            "channel": "SMS",
            "destination": DESTINATION,
            "patient_consent_confirmed": True,
        }
    }
    # Both shapes are **404**, and both for the anti-enumeration reasons `require_clinician`
    # already documents. Naming A's organization: a caller with no relationship to an organization
    # cannot distinguish it from one that does not exist. Naming their own organization with A's
    # order id: another provider's real order id is likewise indistinguishable from a fabricated
    # one. A 403 in either position would confirm that the organization, or the order, is real.
    assert (
        client.post(
            _issue_url(provider_a.id, order_a), headers=_auth(token_b), json=body
        ).status_code
        == 404
    )
    assert (
        client.post(
            _issue_url(provider_b.id, order_a), headers=_auth(token_b), json=body
        ).status_code
        == 404
    )

    with sessions() as session:
        assert session.scalars(select(PatientNotification)).all() == []
    assert transport.sent == []


def test_a17_notification_status_and_retry_are_tenant_scoped(
    client, keypair, organizations, sessions, transport
) -> None:
    """Possession of a notification UUID is not authority.

    The notification below genuinely exists and belongs to A. B holds its real id, their own valid
    token and their own organization id, and still cannot read or retry it.
    """
    private_key, _ = keypair
    provider_a, token_a = _clinician(client, private_key, organizations, "tenA")
    provider_b, token_b = _clinician(client, private_key, organizations, "tenB")
    order_a = _assigned_order(sessions, organizations, provider_a, key="tenA")
    client.post(
        _issue_url(provider_a.id, order_a),
        headers=_auth(token_a),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )
    with sessions() as session:
        notification_id = session.scalars(select(PatientNotification)).one().id

    # 404 rather than 403, for the anti-enumeration reason above: B has no relationship to A, so A
    # is indistinguishable from an organization that does not exist.
    assert (
        client.get(_notify_url(provider_a.id, order_a), headers=_auth(token_b)).status_code == 404
    )
    assert (
        client.get(_notify_url(provider_b.id, order_a), headers=_auth(token_b)).status_code == 404
    )
    retry_b = f"{_notify_url(provider_b.id, order_a)}/{notification_id}/retry"
    assert client.post(retry_b, headers=_auth(token_b)).status_code == 404


def test_a_notification_id_from_another_order_is_a_404_even_for_the_right_clinician(
    client, keypair, organizations, sessions
) -> None:
    """The id is checked against the order in the path, not merely against the caller's tenant."""
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "xorder")
    order_one = _assigned_order(sessions, organizations, provider, key="xorder1")
    order_two = _assigned_order(sessions, organizations, provider, key="xorder2")
    body = {
        "notify": {
            "channel": "SMS",
            "destination": DESTINATION,
            "patient_consent_confirmed": True,
        }
    }
    client.post(_issue_url(provider.id, order_one), headers=_auth(token), json=body)
    with sessions() as session:
        notification_id = session.scalars(select(PatientNotification)).one().id

    response = client.post(
        f"{_notify_url(provider.id, order_two)}/{notification_id}/retry", headers=_auth(token)
    )
    assert response.status_code == 404


def test_a_non_clinician_member_cannot_enrol(client, keypair, organizations, sessions) -> None:
    """§20: not frontend visibility, and not "any member of the organization"."""
    private_key, _ = keypair
    provider, _ = _clinician(client, private_key, organizations, "roles")
    admin_identity = _bootstrap_identity(client, private_key, "orgadmin-roles")
    _grant_roles(client, private_key, provider.id, admin_identity, ["ORG_ADMIN"])
    admin_token = make_token(private_key, subject="orgadmin-roles")
    order_id = _assigned_order(sessions, organizations, provider, key="roles")

    response = client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(admin_token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )
    assert response.status_code == 403


def test_a_platform_admin_gains_no_clinical_messaging_authority(
    client, keypair, organizations, sessions
) -> None:
    """§20 explicitly. A platform admin may read cross-tenant operational health and may not send a
    patient a message -- ``_require_clinician`` is the boundary they do not bypass."""
    private_key, _ = keypair
    provider, _ = _clinician(client, private_key, organizations, "padmin")
    order_id = _assigned_order(sessions, organizations, provider, key="padmin")
    admin_token = make_token(private_key, subject="padmin", roles=[PLATFORM_ADMIN_ROLE])

    send = client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(admin_token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )
    assert send.status_code == 403, "platform admin is not clinical messaging authority"

    ops = client.get("/exchange/v1/notifications/operations", headers=_auth(admin_token))
    assert ops.status_code == 200, "but cross-tenant operational health is exactly their job"


def test_the_operations_read_refuses_a_clinician_and_carries_nothing_identifying(
    client, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "ops")
    order_id = _assigned_order(sessions, organizations, provider, key="ops")
    client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )

    assert (
        client.get("/exchange/v1/notifications/operations", headers=_auth(token)).status_code == 403
    )

    admin = make_token(private_key, subject="ops-admin", roles=[PLATFORM_ADMIN_ROLE])
    payload = client.get("/exchange/v1/notifications/operations", headers=_auth(admin)).json()
    blob = repr(payload)
    assert DESTINATION not in blob
    assert str(order_id) not in blob
    assert payload["enabled"] is True
    assert payload["provider"] == "fake"
    assert payload["by_state"][NotificationState.SENT.value] == 1
    # DH-NOTIFY-META-001 §6: channel availability is server-derived, not a frontend constant.
    assert payload["available_channels"] == ["SMS", "WHATSAPP"], (
        "the fake transport supports both; a real Meta deployment reports WHATSAPP only"
    )


def test_the_operations_read_shows_the_graph_version_and_never_a_credential(
    sessions, keypair, tmp_path, organizations
) -> None:
    """DH-NOTIFY-META-001 §3: the pinned version is visible to operators; the token is not.

    Both halves matter. An operator who cannot see which Graph API version a deployment talks to
    has to read the container's environment -- which is also where the access token lives, so the
    safe way to ask must be this read. And this read must never become the unsafe way.
    """
    from daher_adapter.notify.meta import MetaWhatsAppTransport

    private_key, _ = keypair
    secret_token = "meta-access-token-must-not-appear"  # noqa: S105 - fixture value
    meta = MetaWhatsAppTransport(
        access_token=secret_token,
        phone_number_id="111222333444555",
        graph_api_version="v23.0",
        template_names={
            "META_TEMPLATE_PATIENT_ACCESS_FR": "dh_access_fr",
            "META_TEMPLATE_PATIENT_UPDATE_FR": "dh_update_fr",
        },
        template_language="fr",
        client=object(),
    )
    meta_client = _make_client(sessions, keypair, tmp_path, transport=meta)
    admin = make_token(private_key, subject="ops-admin-meta", roles=[PLATFORM_ADMIN_ROLE])

    payload = meta_client.get("/exchange/v1/notifications/operations", headers=_auth(admin)).json()

    assert payload["provider"] == "meta_whatsapp"
    assert payload["graph_api_version"] == "v23.0"
    # §6: Meta is not an SMS provider, and the read must not imply a capability that does not exist.
    assert payload["available_channels"] == ["WHATSAPP"]
    assert secret_token not in repr(payload)
    assert "META_WHATSAPP_ACCESS_TOKEN" not in repr(payload)


# -- §21: audit ------------------------------------------------------------------------------------


def test_enrollment_and_retry_are_audited_without_the_destination(
    client, keypair, organizations, sessions, transport
) -> None:
    """§21: the consent act is audited against a real ``ExchangeIdentity`` actor, and the audit row
    carries the notification's id -- never the phone number, never the link."""
    from daher_adapter.notify.models import NotificationFailureCategory

    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "audit")
    order_id = _assigned_order(sessions, organizations, provider, key="audit")
    transport.fail_next(NotificationFailureCategory.PROVIDER_REJECTED)
    client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )
    with sessions() as session:
        row = session.scalars(select(PatientNotification)).one()
    assert row.state == NotificationState.FAILED.value
    # An access-link row is never retryable; a 409 is the honest answer, and §4's recovery path is
    # revoke-and-reissue, which the UI surfaces instead.
    retry = client.post(
        f"{_notify_url(provider.id, order_id)}/{row.id}/retry", headers=_auth(token)
    )
    assert retry.status_code == 409
    assert "revoke" in retry.json()["detail"]

    with sessions() as session:
        events = list(
            session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.event_type == "patient_notification_requested"
                )
            ).all()
        )
    assert len(events) == 1
    assert events[0].resource_type == "patient_notification"
    assert events[0].resource_id == row.id
    assert events[0].actor_identity_id is not None, "a real identity, never a fabricated actor"
    blob = " ".join(
        str(getattr(events[0], c.key)) for c in ExchangeSecurityAuditEvent.__table__.columns
    )
    assert DESTINATION not in blob


def test_consent_is_recorded_as_who_and_when_and_nothing_else(
    client, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "cons2")
    order_id = _assigned_order(sessions, organizations, provider, key="cons2")
    client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "WHATSAPP",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )

    with sessions() as session:
        enrollment = session.scalars(select(PatientNotificationEnrollment)).one()
    assert enrollment.consent_recorded_at is not None
    assert enrollment.consent_recorded_by_identity_id is not None
    assert enrollment.channel == "WHATSAPP"
    assert enrollment.destination == DESTINATION
    # No free-text anywhere on the row.
    columns = {c.key for c in PatientNotificationEnrollment.__table__.columns}
    assert "note" not in columns
    assert "consent_text" not in columns


def test_status_read_reports_attempt_state_without_a_provider_message_id(
    client, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _clinician(client, private_key, organizations, "status")
    order_id = _assigned_order(sessions, organizations, provider, key="status")
    client.post(
        _issue_url(provider.id, order_id),
        headers=_auth(token),
        json={
            "notify": {
                "channel": "SMS",
                "destination": DESTINATION,
                "patient_consent_confirmed": True,
            }
        },
    )

    payload = client.get(_notify_url(provider.id, order_id), headers=_auth(token)).json()

    assert payload["enrolled"] is True
    assert payload["destination"] == DESTINATION
    assert len(payload["notifications"]) == 1
    entry = payload["notifications"][0]
    assert entry["state"] == NotificationState.SENT.value
    assert entry["event_type"] == "ACCESS_LINK_ISSUED"
    assert entry["retryable"] is False
    assert "provider_message_id" not in entry, "a vendor id is not a clinician-facing field"
    assert "body" not in entry
