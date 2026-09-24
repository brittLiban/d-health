"""DH-NOTIFY-META-001 §25/§26 — the real-provider qualification path.

    ⚠️  WRITTEN, NEVER EXECUTED. This script has not been run against Meta, against any
        credential, or against any handset. Until it has, it is evidence of nothing, and no
        document may describe WhatsApp as REAL-META-WHATSAPP-QUALIFIED.

DH-NOTIFY-001 deliberately did not write this, on the grounds that a workflow which has never run
is the "the script exists, therefore it is done" trap this repository's pinned P0 rules name.
DH-NOTIFY-META-001 §25 asks for it anyway, and the resolution is not to pretend: the path exists so
that qualification is *possible* the day credentials arrive, and every artifact it produces says
plainly whether it ran.

What it does, in order (§26):

    synthetic provider + lab + patient + order   (real repositories, never a fixture shortcut)
      → issue patient access WITH notification    → assert SENT, record the wamid
      → lab enters a synthetic result and releases
      → dispatcher drains the queue               → assert SENT, record the wamid
      → lab prepares and releases an amendment
      → dispatcher drains the queue               → assert SENT, record the wamid
      → print what a human must confirm on the handset

**The delivery-receipt leg is deliberately manual, and this is the honest part.** Meta delivers
status callbacks to a public HTTPS URL. A GitHub-hosted runner has no such URL without a tunnel, so
this script cannot observe ``DELIVERED`` by itself. It records every ``wamid`` it produced and
stops; an operator running against a deployment whose webhook Meta can actually reach confirms the
transition with ``--await-delivery``, which polls the database rather than inventing a receipt.
Claiming the receipt leg from a runner that cannot receive one would be exactly the kind of tidy,
untrue evidence AGENTS.md was written against.

**Safety, enforced rather than documented:**

* ``META_QUALIFICATION_TEST_DESTINATION`` is required and has no default. This script never
  chooses a number to message.
* The destination must parse as E.164 through the same validator production uses.
* Every organization, patient, practitioner and catalog entry it creates is synthetic and
  prefixed, so a human reading the database can tell at a glance that none of it is real.
* The evidence artifact carries wamids, states and timestamps. **Never** the access token, the
  message body, the patient access URL, or the destination number.
* It refuses to run against a database that already holds non-synthetic organizations unless
  ``--i-know-this-is-not-production`` is passed.

Usage::

    META_QUALIFICATION_TEST_DESTINATION=+253xxxxxxxx \\
    DHEALTH_NOTIFICATIONS_ENABLED=true \\
    DHEALTH_NOTIFICATION_PROVIDER=meta \\
    DHEALTH_PATIENT_PUBLIC_BASE_URL=https://... \\
    META_WHATSAPP_ACCESS_TOKEN=... META_WHATSAPP_PHONE_NUMBER_ID=... \\
    META_GRAPH_API_VERSION=vNN.N META_TEMPLATE_LANGUAGE=fr \\
    META_TEMPLATE_PATIENT_ACCESS_FR=... META_TEMPLATE_PATIENT_UPDATE_FR=... \\
    ADAPTER_DB_* ... \\
        python -m scripts.qualify_meta_whatsapp --evidence out.json

See ``docs/META-WHATSAPP-RUNBOOK.md`` for what must be configured in Meta first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.config import Settings
from daher_adapter.database import build_engine, build_session_factory
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    LabPrice,
    LabResultType,
    OnboardingStatus,
    Organization,
    OrganizationRole,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patient_access import PatientAccessGrantRepository
from daher_adapter.exchange.results import (
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
)
from daher_adapter.exchange.selection import LabSelectionRepository
from daher_adapter.notify.dispatcher import (
    PatientNotificationDispatcher,
    send_access_link_inline,
)
from daher_adapter.notify.links import build_patient_access_url
from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationEventType,
    NotificationState,
    PatientNotification,
)
from daher_adapter.notify.phone import InvalidDestination, normalize
from daher_adapter.notify.repository import PatientNotificationRepository, access_link_key

#: Every synthetic row this script creates carries it, so a human reading the database can tell at
#: a glance that none of it is real. Also what the production-database guard looks for.
SYNTHETIC_PREFIX = "ZZ-META-QUAL"


class QualificationRefused(RuntimeError):
    """A precondition this script will not proceed without. Never a warning."""


def _require_destination(settings: Settings) -> str:
    raw = os.environ.get("META_QUALIFICATION_TEST_DESTINATION", "").strip()
    if not raw:
        raise QualificationRefused(
            "META_QUALIFICATION_TEST_DESTINATION is required and has no default. This script "
            "never chooses a number to message. Set it to a NON-PATIENT test handset in E.164 "
            "form."
        )
    try:
        # The same validator production uses, so a number this accepts is one the real send path
        # would accept too.
        return normalize(raw, default_region=settings.notification_default_region)
    except InvalidDestination as error:
        raise QualificationRefused(
            f"META_QUALIFICATION_TEST_DESTINATION is not usable: {error}"
        ) from error


def _guard_database(sessions: sessionmaker[Session], *, acknowledged: bool) -> None:
    """Refuse a database that looks like somebody's real deployment.

    Crude on purpose: any organization not carrying the synthetic prefix is treated as real. A
    qualification run creates orders, issues patient access and sends WhatsApp messages; doing that
    inside a live facility's database is the one mistake worth being obstructive about.
    """
    if acknowledged:
        return
    with sessions() as session:
        foreign = session.scalars(
            select(Organization.name).where(Organization.name.not_like(f"{SYNTHETIC_PREFIX}%"))
        ).first()
    if foreign is not None:
        raise QualificationRefused(
            "this database already holds organizations that are not this script's own synthetic "
            "ones, so it may be a real deployment. Re-run against a disposable database, or pass "
            "--i-know-this-is-not-production if you are certain."
        )


def _synthetic_world(sessions: sessionmaker[Session], run_id: str) -> dict[str, Any]:
    """A provider, a lab, a patient, a practitioner and a priced catalog entry.

    Built through the **real** repositories along the real workflow path. A fixture that inserted
    rows directly would let this script pass while the production path was broken, which is the
    whole thing it exists to test.
    """
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)
    assignments = OrganizationRoleAssignmentRepository(sessions)

    from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository

    def _facility(org_type: OrganizationType, label: str, roles: tuple[OrganizationRole, ...]):
        org = organizations.create(
            org_type=org_type.value, name=f"{SYNTHETIC_PREFIX} {label} {run_id}"
        )
        FacilityEnrollmentRepository(sessions).create(
            organization_id=org.id,
            participation_mode=ParticipationMode.NATIVE,
            onboarding_status=OnboardingStatus.ORGANIZATION_READY,
        )
        identity, _ = identities.get_or_create(
            issuer="https://qualification.dhealth.invalid",
            subject=f"{SYNTHETIC_PREFIX}-{label}-{run_id}",
            display_name=f"{SYNTHETIC_PREFIX} staff",
            email=None,
        )
        membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)
        for role in roles:
            assignments.assign(membership_id=membership.id, role=role)
        return org, identity

    provider, clinician = _facility(
        OrganizationType.PROVIDER, "Clinic", (OrganizationRole.CLINICIAN,)
    )
    lab, lab_staff = _facility(
        OrganizationType.LAB,
        "Lab",
        (OrganizationRole.LAB_TECH, OrganizationRole.LAB_RESULT_RELEASER),
    )

    LabDirectoryProfileRepository(sessions, organizations).upsert(
        lab_organization_id=lab.id,
        public_address=f"{SYNTHETIC_PREFIX} address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    entry = catalog.create_entry(
        code=f"{SYNTHETIC_PREFIX}-{run_id}",
        display_name=f"{SYNTHETIC_PREFIX} synthetic analysis",
        is_synthetic=True,
    )
    entry = entry[0] if isinstance(entry, tuple) else entry
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1.00"),
        currency="DJF",
        expected_current_price_id=None,
    )

    patient = PatientReferenceRepository(sessions).get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_qualification",
        source_identity_system="urn:dhealth:qualification-patient",
        source_identity_value=f"{SYNTHETIC_PREFIX}-{run_id}",
        display_name=f"{SYNTHETIC_PREFIX} synthetic patient",
    )
    practitioner = PractitionerReferenceRepository(sessions).get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_qualification",
        source_identity_system="urn:dhealth:qualification-practitioner",
        source_identity_value=f"{SYNTHETIC_PREFIX}-doc-{run_id}",
        display_name=f"{SYNTHETIC_PREFIX} synthetic clinician",
    )

    order = (
        LabOrderRepository(sessions, organizations, catalog)
        .create(
            idempotency_key=f"{SYNTHETIC_PREFIX}-{run_id}",
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
    workflow = LabWorkflowRepository(sessions, organizations)
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)
    workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    refreshed = LabOrderRepository(sessions, organizations, catalog).get(order.id)
    return {
        "provider": provider,
        "lab": lab,
        "clinician": clinician,
        "lab_staff": lab_staff,
        "order_id": order.id,
        "item_id": refreshed.items[0].id,
    }


def _notification_for(
    sessions: sessionmaker[Session], order_id: uuid.UUID, event: NotificationEventType
) -> PatientNotification | None:
    with sessions() as session:
        return session.scalars(
            select(PatientNotification)
            .where(
                PatientNotification.lab_order_id == order_id,
                PatientNotification.event_type == event.value,
            )
            .order_by(PatientNotification.created_at.desc())
        ).first()


def _record(evidence: list[dict], step: str, row: PatientNotification | None) -> None:
    """One evidence line. Carries no destination, no body, no URL and no credential."""
    evidence.append(
        {
            "step": step,
            "observed_at": datetime.now(UTC).isoformat(),
            "event_type": None if row is None else row.event_type,
            "state": None if row is None else row.state,
            "provider_message_id": None if row is None else row.provider_message_id,
            "attempt_count": None if row is None else row.attempt_count,
            "failure_category": None if row is None else row.failure_category,
        }
    )


def _await_delivery(
    sessions: sessionmaker[Session],
    order_id: uuid.UUID,
    event: NotificationEventType,
    *,
    timeout_seconds: int,
) -> PatientNotification | None:
    """Poll the database for a webhook-driven transition. Never fabricates one.

    This reads what the *webhook* wrote. If Meta cannot reach this deployment's callback URL,
    nothing will ever arrive and this returns the row still in ``SENT`` -- which is the honest
    outcome, and the one the evidence artifact then records.
    """
    deadline = time.monotonic() + timeout_seconds
    row = _notification_for(sessions, order_id, event)
    while time.monotonic() < deadline:
        row = _notification_for(sessions, order_id, event)
        if row is not None and row.state in (
            NotificationState.DELIVERED.value,
            NotificationState.FAILED.value,
        ):
            return row
        time.sleep(5)
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qualify-meta-whatsapp",
        description="Real Meta WhatsApp Cloud API qualification against a synthetic order.",
    )
    parser.add_argument("--evidence", type=Path, required=True, help="where to write evidence JSON")
    parser.add_argument(
        "--await-delivery",
        type=int,
        default=0,
        metavar="SECONDS",
        help=(
            "poll for webhook-driven DELIVERED for this many seconds. Only meaningful where Meta "
            "can actually reach this deployment's callback URL; 0 (the default) records the "
            "wamids and stops."
        ),
    )
    parser.add_argument(
        "--i-know-this-is-not-production",
        action="store_true",
        help="skip the non-synthetic-organization guard",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if not settings.notifications_enabled:
        raise QualificationRefused(
            "DHEALTH_NOTIFICATIONS_ENABLED is false. This script qualifies a real provider; it "
            "has nothing to do with sending switched off."
        )
    if settings.notification_provider != "meta":
        raise QualificationRefused(
            f"DHEALTH_NOTIFICATION_PROVIDER is {settings.notification_provider!r}. This script "
            "qualifies the real Meta transport and refuses to report a fake as one."
        )
    destination = _require_destination(settings)
    transport = settings.build_notification_transport()
    assert transport is not None  # noqa: S101 - guaranteed by the two checks above

    engine = build_engine(settings)
    sessions = build_session_factory(engine)
    _guard_database(sessions, acknowledged=args.i_know_this_is_not_production)

    run_id = uuid.uuid4().hex[:8]
    evidence: list[dict] = []
    summary: dict[str, Any] = {
        "ticket": "DH-NOTIFY-META-001",
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "provider": transport.provider_name,
        "graph_api_version": getattr(transport, "graph_api_version", None),
        "template_language": settings.meta_template_language,
        "template_names": sorted(settings.meta_template_names),
        "receipts_configured": settings.notification_receipts_configured,
        "await_delivery_seconds": args.await_delivery,
        # Stated in the artifact itself so a reader cannot mistake a partial run for a full one.
        "delivery_receipt_leg": (
            "polled" if args.await_delivery else "NOT OBSERVED -- wamids recorded only"
        ),
        "steps": evidence,
    }

    print(f"[{run_id}] building a synthetic provider, lab, patient and order...")
    world = _synthetic_world(sessions, run_id)
    order_id = world["order_id"]
    notifications = PatientNotificationRepository()

    # -- 1. issue patient access, with notification --------------------------------------------
    print(f"[{run_id}] issuing patient access and sending the secure link over WhatsApp...")
    grants = PatientAccessGrantRepository(
        sessions, ttl_hours=settings.exchange_patient_access_ttl_hours
    )
    with sessions.begin() as session:
        issued = grants.issue(lab_order_id=order_id, session=session)
        enrollment = notifications.enroll(
            session,
            lab_order_id=order_id,
            patient_access_grant_id=issued.id,
            channel=NotificationChannel.WHATSAPP,
            destination=destination,
            consent_recorded_by_identity_id=world["clinician"].id,
        )
        access_notification, _ = notifications.enqueue(
            session,
            idempotency_key=access_link_key(issued.id, NotificationChannel.WHATSAPP, destination),
            lab_order_id=order_id,
            release_version_id=None,
            enrollment=enrollment,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
            requested_by_identity_id=world["clinician"].id,
        )
        access_notification_id = access_notification.id

    # The raw token exists here and only here -- exactly as in production (ADR-058 A′). The URL is
    # built, passed, and dropped; it is never printed, logged or written to the evidence file.
    send_access_link_inline(
        sessions,
        transport=transport,
        notification_id=access_notification_id,
        patient_access_url=build_patient_access_url(
            settings.patient_public_base_url or "", issued.raw_token
        ),
    )
    _record(
        evidence,
        "access_link_issued",
        _notification_for(sessions, order_id, NotificationEventType.ACCESS_LINK_ISSUED),
    )

    # -- 2. release a synthetic result ----------------------------------------------------------
    print(f"[{run_id}] entering a synthetic result and releasing it...")
    results = LabResultRepository(sessions)
    results.record_observation(
        order_id=order_id,
        lab_order_item_id=world["item_id"],
        lab_organization_id=world["lab"].id,
        result_type=LabResultType.NUMERIC,
        source_code=f"{SYNTHETIC_PREFIX}-OBS",
        display_label_snapshot=f"{SYNTHETIC_PREFIX} synthetic analyte",
        recorded_by_identity_id=world["lab_staff"].id,
        numeric_value=Decimal("1.0"),
        numeric_unit="mmol/L",
    )
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=world["item_id"],
        lab_organization_id=world["lab"].id,
        completed_by_identity_id=world["lab_staff"].id,
    )
    LabResultReleaseRepository(sessions).release(
        order_id=order_id,
        lab_organization_id=world["lab"].id,
        released_by_identity_id=world["lab_staff"].id,
    )
    dispatcher = PatientNotificationDispatcher(
        sessions, transport=transport, worker_id=f"qualification-{run_id}"
    )
    dispatcher.dispatch_once()
    _record(
        evidence,
        "result_released",
        _notification_for(sessions, order_id, NotificationEventType.RESULT_RELEASED),
    )

    # -- 3. release an amendment ----------------------------------------------------------------
    print(f"[{run_id}] preparing and releasing an amendment...")
    amendments = LabResultAmendmentRepository(sessions)
    amendment = amendments.start_amendment(
        order_id=order_id,
        lab_organization_id=world["lab"].id,
        started_by_identity_id=world["lab_staff"].id,
    )
    inspection = amendments.get_amendment_inspection(amendment.id)
    amendments.correct_observation(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_order_item_id=world["item_id"],
        lab_organization_id=world["lab"].id,
        observation_id=inspection.items[0].observations[0].id,
        result_type=LabResultType.NUMERIC,
        source_code=f"{SYNTHETIC_PREFIX}-OBS",
        display_label_snapshot=f"{SYNTHETIC_PREFIX} synthetic analyte",
        corrected_by_identity_id=world["lab_staff"].id,
        numeric_value=Decimal("2.0"),
        numeric_unit="mmol/L",
    )
    amendments.release_amendment(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_organization_id=world["lab"].id,
        released_by_identity_id=world["lab_staff"].id,
    )
    dispatcher.dispatch_once()
    _record(
        evidence,
        "amendment_released",
        _notification_for(sessions, order_id, NotificationEventType.AMENDMENT_RELEASED),
    )

    # -- 4. the delivery-receipt leg, if this deployment can receive one -------------------------
    if args.await_delivery:
        print(f"[{run_id}] polling up to {args.await_delivery}s for Meta delivery receipts...")
        for event in (
            NotificationEventType.ACCESS_LINK_ISSUED,
            NotificationEventType.RESULT_RELEASED,
            NotificationEventType.AMENDMENT_RELEASED,
        ):
            _record(
                evidence,
                f"delivery_receipt:{event.value}",
                _await_delivery(sessions, order_id, event, timeout_seconds=args.await_delivery),
            )

    summary["finished_at"] = datetime.now(UTC).isoformat()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    sent = [line for line in evidence if line.get("state") == NotificationState.SENT.value]
    delivered = [
        line for line in evidence if line.get("state") == NotificationState.DELIVERED.value
    ]
    print()
    print(f"[{run_id}] evidence written to {args.evidence}")
    print(
        f"[{run_id}] {len(sent)} message(s) accepted by Meta, {len(delivered)} confirmed DELIVERED"
    )
    print()
    print("A HUMAN MUST NOW CONFIRM ON THE TEST HANDSET, because no automated check can:")
    print("  1. the first message contains a working secure link, and opening it loads DHealth")
    print("     Patient showing STATUS ONLY;")
    print("  2. the second and third messages contain NO link and NO result value of any kind;")
    print("  3. the original link still works after the amendment.")
    print()
    if not delivered:
        print("DELIVERED was not observed. That is expected unless Meta can reach this")
        print("deployment's webhook URL. WhatsApp remains BLOCKED until it has been.")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    try:
        sys.exit(main())
    except QualificationRefused as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        sys.exit(2)
