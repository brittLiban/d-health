"""DH-011E2: live proof that `OrderPoller`'s stale-claim recovery writer -- now a per-event
closure over `self._deliver`, built inside `_handle`, rather than a poller-lifetime-fixed
`write_target` -- actually performs a real, correct OpenELIS write end to end.

This is the proof DH-011K's own crash matrix could not provide: DH-011K exercised
`RecoveryCoordinator.reconcile` directly with hand-built writers, which proved the *algorithm*
safe but never proved that a real, payload-dependent writer could be correctly *composed* through
`OrderPoller.poll_once()` itself -- because before this ticket, it structurally could not be (the
constructor-fixed `write_target` had no way to reach the event's own payload). This file proves
the composition, not the algorithm; DH-011K's own file remains the algorithm's live proof and is
not re-run here except at the three boundaries this ticket's own recovery-writer change could
plausibly affect (ABSENT retry, FOUND reconciliation, and — via `test_dh011k_crash_matrix.py`
itself, run separately — completed+stale-watermark replay).

**Manual / opt-in only**, mirroring every other DH-011 integration test file in this directory:
skipped by default (including in CI) unless every `DH011E2_LIVE_*` env var below is set. Never
point this at a real facility; nothing here checks that.

**DH-011E remains BUILT + VERIFIED, NOT ACTIVATED.** Nothing in this file starts a scheduler or
recurring loop -- every test here invokes `OrderPoller.poll_once()` exactly once, explicitly, the
same way every other DH-011 integration test does.

**Shares one real watermark stream with `test_dh011k_crash_matrix.py`** if both are run against
the same disposable stack: DH-011K's own dead-letter test permanently blocks the
`(ServiceRequest, openmrs_to_openelis)` stream (no operator-resolution mechanism exists -- see
that file's own module docstring), and this file's `poll_once()` calls would then halt on that
same stale dead letter before ever reaching their own fixtures. Run this file on its own stack, or
`TRUNCATE dead_letters, processed_messages, watermarks` on the adapter database between the two
files if sharing one stack manually.

Usage (against a disposable stack matching PROVENANCE.md's DH-011E2 compose recipe)::

    DH011E2_OMRS_BASE_URL=http://localhost:18581/openmrs \\
    DH011E2_OMRS_ADMIN_PASSWORD=<that stack's OMRS_CONFIG_ADMIN_USER_PASSWORD> \\
    DH011E2_OE_BASE_URL=https://localhost:18546/OpenELIS-Global/fhir \\
    DH011E2_OE_AUTH=admin:adminADMIN! \\
    DH011E2_ADAPTER_DB_PORT=15544 \\
    DH011E2_ADAPTER_DB_PASSWORD=<that stack's ADAPTER_DB_PASSWORD> \\
        pytest adapter/tests/integration/test_dh011e2_recovery_writer.py -v
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from daher_adapter.ledger import Direction, LedgerRepository
from daher_adapter.openelis import PATIENT_IDENTITY_SYSTEM, HttpResult, OpenElisClient
from daher_adapter.openmrs import SERVICE_REQUEST, OpenMrsOrderSource
from daher_adapter.order_poller import OrderPoller
from daher_adapter.recovery import (
    RecoveryCoordinator,
    SourceCorrelation,
    TargetPresence,
    source_correlation,
)
from daher_adapter.source_events import event_from_resource
from daher_adapter.watermark import WatermarkRepository

OMRS_BASE = os.environ.get("DH011E2_OMRS_BASE_URL")
OMRS_ADMIN_PASSWORD = os.environ.get("DH011E2_OMRS_ADMIN_PASSWORD")
OE_BASE = os.environ.get("DH011E2_OE_BASE_URL")
OE_AUTH = os.environ.get("DH011E2_OE_AUTH")
ADAPTER_DB_PORT = os.environ.get("DH011E2_ADAPTER_DB_PORT")
ADAPTER_DB_PASSWORD = os.environ.get("DH011E2_ADAPTER_DB_PASSWORD")

pytestmark = pytest.mark.skipif(
    not (
        OMRS_BASE
        and OMRS_ADMIN_PASSWORD
        and OE_BASE
        and OE_AUTH
        and ADAPTER_DB_PORT
        and ADAPTER_DB_PASSWORD
    ),
    reason="requires a live disposable DH-011E2 stack (DH011E2_LIVE_* env vars) -- never run in CI",
)

_SSL_CTX = ssl._create_unverified_context()  # noqa: S323 - disposable, self-signed stack only
_OE_AUTH_HEADER = "Basic " + base64.b64encode((OE_AUTH or "").encode()).decode() if OE_AUTH else ""
_OMRS_AUTH_HEADER = (
    "Basic " + base64.b64encode(f"admin:{OMRS_ADMIN_PASSWORD}".encode()).decode()
    if OMRS_ADMIN_PASSWORD
    else ""
)

# Real, already-committed Daher production reference config, reused structurally -- not a
# clinical mapping decision. Identical to test_dh011k_crash_matrix.py's own fixture constants.
VISIT_TYPE = "194bf546-68d7-42c0-8371-5b0f2b1cf5f3"  # Daher Maternity Admission
ENCOUNTER_TYPE = "e22e39fd-7db2-45e7-80f1-60fa0d5a4378"  # Admission
LOCATION = "6538f097-382d-49b6-a839-601d03c888c0"  # Maternité
CARE_SETTING = "6f0c9a92-6f24-11e3-af88-005056821db0"  # Outpatient
ORDER_TYPE = "52a447d3-a64a-11e3-9aeb-50e549534c5e"  # Test Order
PROVIDER = "f9badd80-ab76-11e2-9e96-0800200c9a66"  # Unknown Provider -- TRANSPORT FIXTURE ONLY
OLD_ID_NUMBER_TYPE = "8d79403a-c2cc-11de-8d13-0010c6dffd0f"  # no Luhn check digit


def _omrs_call(method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(  # noqa: S310
        f"{OMRS_BASE}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "Authorization": _OMRS_AUTH_HEADER},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()}") from e


def omrs_bundle_fetcher(url: str) -> Mapping[str, object]:
    req = urllib.request.Request(url, headers={"Authorization": _OMRS_AUTH_HEADER})  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read())


_TEST_CONCEPT_UUID: str | None = None


def _ensure_transport_fixture_concept() -> str:
    global _TEST_CONCEPT_UUID
    if _TEST_CONCEPT_UUID is not None:
        return _TEST_CONCEPT_UUID
    body = _omrs_call(
        "POST",
        "/ws/rest/v1/concept",
        {
            "names": [
                {
                    "name": f"DH011E2 Transport Fixture Test {secrets.token_hex(4)}",
                    "locale": "en",
                    "conceptNameType": "FULLY_SPECIFIED",
                }
            ],
            "conceptClass": "8d4907b2-c2cc-11de-8d13-0010c6dffd0f",  # Test
            "datatype": "8d4a4c94-c2cc-11de-8d13-0010c6dffd0f",  # N/A
        },
    )
    _TEST_CONCEPT_UUID = body["uuid"]
    return _TEST_CONCEPT_UUID


def create_synthetic_patient(label: str) -> str:
    body = _omrs_call(
        "POST",
        "/ws/rest/v1/patient",
        {
            "identifiers": [
                {
                    "identifierType": OLD_ID_NUMBER_TYPE,
                    "identifier": f"DH011E2-{label}",
                    "location": LOCATION,
                    "preferred": True,
                }
            ],
            "person": {
                "names": [{"givenName": label, "familyName": "Dh011e2Fixture"}],
                "gender": "F",
                "birthdate": "2000-01-01",
            },
        },
    )
    return body["uuid"]


def place_test_order(patient_uuid: str) -> dict:
    concept = _ensure_transport_fixture_concept()
    visit = _omrs_call(
        "POST",
        "/ws/rest/v1/visit",
        {"patient": patient_uuid, "visitType": VISIT_TYPE, "location": LOCATION},
    )
    encounter = _omrs_call(
        "POST",
        "/ws/rest/v1/encounter",
        {
            "patient": patient_uuid,
            "encounterType": ENCOUNTER_TYPE,
            "location": LOCATION,
            "visit": visit["uuid"],
        },
    )
    order = _omrs_call(
        "POST",
        "/ws/rest/v1/order",
        {
            "type": "testorder",
            "patient": patient_uuid,
            "encounter": encounter["uuid"],
            "orderer": PROVIDER,
            "concept": concept,
            "careSetting": CARE_SETTING,
            "orderType": ORDER_TYPE,
        },
    )
    return {"order_uuid": order["uuid"], "patient_uuid": patient_uuid}


def synthetic_order_event(label: str):
    patient_uuid = create_synthetic_patient(label)
    order = place_test_order(patient_uuid)
    resource = omrs_bundle_fetcher(f"{OMRS_BASE}/ws/fhir2/R4/ServiceRequest/{order['order_uuid']}")
    event = event_from_resource(resource, resource_type=SERVICE_REQUEST)
    return event, patient_uuid


def _oe_sync_get(url: str) -> HttpResult:
    req = urllib.request.Request(url, headers={"Authorization": _OE_AUTH_HEADER})  # noqa: S310
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode()
            return HttpResult(status=resp.status, body=json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return HttpResult(status=e.code, body=json.loads(body) if body else {})


def _oe_sync_post(url: str, payload) -> HttpResult:
    req = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": _OE_AUTH_HEADER},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode()
            return HttpResult(status=resp.status, body=json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return HttpResult(status=e.code, body=json.loads(body) if body else {})


async def _oe_real_get(url: str) -> HttpResult:
    return await asyncio.to_thread(_oe_sync_get, url)


async def _oe_real_post(url: str, payload) -> HttpResult:
    return await asyncio.to_thread(_oe_sync_post, url, payload)


def build_openelis_client() -> OpenElisClient:
    return OpenElisClient(OE_BASE, _oe_real_get, _oe_real_post)


def patient_payload() -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"family": "Transportfixture", "given": ["Synthetic"]}],
        "gender": "female",
        "birthDate": "2000-01-01",
    }


def service_request_payload(openelis_patient_id: str) -> dict:
    return {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "subject": {"reference": f"Patient/{openelis_patient_id}"},
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "777-3", "display": "PLT(Whole Blood)"}
            ]
        },
    }


async def deliver_event(client: OpenElisClient, event, patient_omrs_uuid: str):
    """The real production `DeliverOrder` shape (test-only composition, per DH-011K's own
    precedent -- not promoted to `adapter/src/`): resolve the native OpenELIS Patient, then
    deliver the ServiceRequest with DH-025's own correlation attached. This is the exact callable
    that both `OrderPoller`'s fresh-claim path (`_handle`'s `claim.claimed` branch) and, as of
    DH-011E2, its stale-claim recovery closure now both call -- proving that reuse live."""
    patient_correlation = SourceCorrelation(system=PATIENT_IDENTITY_SYSTEM, value=patient_omrs_uuid)
    patient_record = await client.ensure_openelis_patient(patient_correlation, patient_payload())
    correlation = source_correlation(event.identity)
    payload = service_request_payload(patient_record.record_id)
    return await client.deliver_service_request(correlation, payload)


def build_ledger() -> LedgerRepository:
    engine = create_engine(
        f"postgresql+psycopg://adapter:{ADAPTER_DB_PASSWORD}@localhost:{ADAPTER_DB_PORT}/adapter"
    )
    return LedgerRepository(sessionmaker(bind=engine, expire_on_commit=False))


def build_watermarks() -> WatermarkRepository:
    engine = create_engine(
        f"postgresql+psycopg://adapter:{ADAPTER_DB_PASSWORD}@localhost:{ADAPTER_DB_PORT}/adapter"
    )
    return WatermarkRepository(sessionmaker(bind=engine, expire_on_commit=False))


def build_source() -> OpenMrsOrderSource:
    return OpenMrsOrderSource(f"{OMRS_BASE}/ws/fhir2/R4", omrs_bundle_fetcher)


DIRECTION: Direction = "openmrs_to_openelis"


class _HeldFence:
    held = True


@pytest.fixture(autouse=True, scope="module")
def _bootstrap_watermark_before_any_fixture() -> None:
    """Same reasoning as `test_dh011k_crash_matrix.py`'s own fixture: force the watermark to
    bootstrap before any test creates an order, so the very first test's own fixture is not
    excluded by a bootstrap instant taken after it already exists (ADR-015)."""
    build_watermarks().get_or_bootstrap(
        resource_type=SERVICE_REQUEST,
        direction=DIRECTION,
        server_now=build_source().server_instant(SERVICE_REQUEST),
    )


def _oe_service_request_count(correlation_value: str) -> int:
    client = build_openelis_client()

    async def _count() -> int:
        match = await client.service_request_probe(
            SourceCorrelation(
                system="urn:daher-health:adapter:source-event", value=correlation_value
            )
        )
        return 1 if match.presence is TargetPresence.FOUND else 0

    return asyncio.run(_count())


# ==============================================================================================
# §11 -- live proof: OrderPoller.poll_once() recovers a stale ABSENT claim through the real
# per-event writer, using real production classes end to end.
# ==============================================================================================


def test_stale_claim_absent_recovers_through_the_real_composed_poller() -> None:
    event, patient_uuid = synthetic_order_event("ABSENT" + secrets.token_hex(3))
    ledger = build_ledger()
    client = build_openelis_client()
    correlation = source_correlation(event.identity)

    # A previous cycle claimed it and crashed before ever attempting the write.
    assert ledger.claim(event.identity).claimed
    assert _oe_service_request_count(correlation.value) == 0, "target genuinely absent"

    async def deliver(ev):
        return await deliver_event(client, ev, patient_uuid)

    # "Restart": a fresh OrderPoller, composed with the real production classes -- no
    # `write_target` constructor argument exists any more; the recovery writer is built inside
    # `_handle` itself, over this exact event, using this exact `deliver` callable.
    poller = OrderPoller(
        source=build_source(),
        ledger=build_ledger(),
        watermarks=build_watermarks(),
        deliver=deliver,
        recovery=RecoveryCoordinator(build_ledger()),
        fence=_HeldFence(),
        probe_target=client.service_request_probe,
    )
    outcome = asyncio.run(poller.poll_once())

    assert outcome.halted_reason is None
    assert outcome.reconciled >= 1
    assert build_ledger().get_state(event.identity) == "completed"
    assert _oe_service_request_count(correlation.value) == 1, "exactly one real accession created"

    # DH-011G2 patient-identity regression: exact native link, no fallback.
    patient_correlation = SourceCorrelation(system=PATIENT_IDENTITY_SYSTEM, value=patient_uuid)

    async def _patient_found() -> bool:
        probe = await client.patient_identity_probe(patient_correlation)
        return probe.presence is TargetPresence.FOUND

    assert asyncio.run(_patient_found())


# ==============================================================================================
# §12 -- crash-after-commit regression: the ABSENT-path fix must not weaken FOUND reconciliation.
# ==============================================================================================


def test_stale_claim_found_reconciles_without_a_second_write_through_the_real_composed_poller() -> (
    None
):
    event, patient_uuid = synthetic_order_event("FOUND" + secrets.token_hex(3))
    client = build_openelis_client()
    correlation = source_correlation(event.identity)

    # The target genuinely already exists (a real write performed out of band, modelling "the
    # previous attempt's own delivery call succeeded but the process died before recording it").
    seeded = asyncio.run(deliver_event(client, event, patient_uuid))
    assert seeded.record_id
    assert _oe_service_request_count(correlation.value) == 1

    ledger = build_ledger()
    assert ledger.claim(event.identity).claimed

    write_calls = {"n": 0}

    async def deliver_must_not_be_called(ev):
        write_calls["n"] += 1
        raise AssertionError("a FOUND target must never be delivered to again")

    poller = OrderPoller(
        source=build_source(),
        ledger=build_ledger(),
        watermarks=build_watermarks(),
        deliver=deliver_must_not_be_called,
        recovery=RecoveryCoordinator(build_ledger()),
        fence=_HeldFence(),
        probe_target=client.service_request_probe,
    )
    outcome = asyncio.run(poller.poll_once())

    assert write_calls["n"] == 0, "the per-event closure must never call deliver on FOUND"
    assert outcome.halted_reason is None
    assert outcome.reconciled >= 1
    assert build_ledger().get_state(event.identity) == "completed"
    assert _oe_service_request_count(correlation.value) == 1, "exactly one, never two"
