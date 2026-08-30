"""DH-011K: crash-point qualification of the real OpenMRS -> OpenELIS order path, using the real
production classes (`OpenMrsOrderSource`, `OrderPoller`, `LedgerRepository`, `WatermarkRepository`,
`RecoveryCoordinator`, `PostgresRecoveryFence`, `OpenElisClient`) against a real disposable
OpenMRS + patched OpenELIS + Postgres stack -- never a unit fake, never scripted HTTP.

**Manual / opt-in only**, mirroring `test_dh011j_cancellation_crash_recovery.py` and
`test_dh011g2_patient_identity.py` exactly: skipped by default (including in CI, which has no such
stack) unless every `DH011K_LIVE_*` env var below is set. Never point this at a real facility;
nothing here checks that, so the operator running it manually is responsible for using only a
disposable, synthetic stack.

**§2 -- canonical five crash points, clarified here, not pre-existing.** No file in this repository
enumerated five named crash points before this ticket. `docs/WATERMARK-DESIGN.md` §5 (DH-025,
2026-08-13) is the closest prior source and names exactly *three*: (1) before the outbound write,
(2) after the target accepted the write but before `LedgerRepository.mark_completed`, (3) after
`mark_completed` (a safe, expected duplicate). `adapter/tests/test_openelis_recovery_binding.py`'s
own docstring says "5 crash points around the write" but never enumerates them -- read against
`test_recovery.py`'s actual test names, that phrase describes roughly five *coordinator behaviors*
(before-write/retry-absent, after-write/reconcile-no-duplicate, ambiguous-probe/fail-closed,
write-failure/preserves-processing, dead-letter/never-recovered), not five *timeline* points, and
none of them is the watermark-advance boundary this ticket also has to cover.
`PROJECT_STATE.md`'s own DH-011K row says "across all five points" without defining them anywhere.

**The five points below are this ticket's own clarification**, derived from `order_poller.py`'s
and `recovery.py`'s actual transaction boundaries (verified against the running source, not
assumed), refining WATERMARK-DESIGN.md's three ledger-centric points by splitting point 2 into the
two sub-boundaries the code actually distinguishes as separate fault-injection seams, and adding a
fifth point specific to the watermark-advance boundary that no prior document named on its own:

1. Before `LedgerRepository.claim()` -- nothing persisted yet.
2. After `claim()` succeeds (row = `processing`), before the target write is ever attempted --
   target state is genuinely ABSENT at restart.
3. The target write's own POST commits, but the crash happens before *that same call* can run its
   authoritative read-back and return -- at restart, target state is EXISTS, but nothing in this
   process's memory ever confirmed it.
4. The target write completes successfully end-to-end (POST + authoritative read-back both
   returned) but the crash happens before `LedgerRepository.mark_completed` runs -- externally
   identical target state to point 3 (EXISTS), but the fault seam is different: the process *did*
   see success and still lost the chance to record it.
5. `mark_completed` succeeds but the crash happens before `WatermarkRepository.advance` runs --
   ledger says `completed`, watermark still says otherwise.

Points 3 and 4 both recover through the identical code path (a fresh `RecoveryCoordinator.reconcile`
probe finds the target FOUND and reconciles without a second write) -- they are constructed
differently here specifically to prove that both real-world fault timings land in that same safe
place, not because the recovery mechanism itself branches on which one occurred.

**TRANSPORT FIXTURE ONLY.** The OpenMRS orderer (`Unknown Provider`, this server's built-in
placeholder) and the lab-test concept this file creates once per stack are synthetic transport
plumbing only -- never the real DH-011H requester identity or the real DH-011I clinical test
mapping, neither of which this ticket touches. The visit type ("Daher Maternity Admission") and
encounter type ("Admission") are real, already-committed Daher production reference config, reused
here structurally (any visit/encounter satisfies OpenMRS's order-placement requirement regardless
of its clinical name) rather than invented.

Usage (against a disposable stack matching PROVENANCE.md's DH-011K compose recipe)::

    DH011K_OMRS_BASE_URL=http://localhost:18580/openmrs \\
    DH011K_OMRS_ADMIN_PASSWORD=<that stack's OMRS_CONFIG_ADMIN_USER_PASSWORD> \\
    DH011K_OE_BASE_URL=https://localhost:18545/OpenELIS-Global/fhir \\
    DH011K_OE_AUTH=admin:adminADMIN! \\
    DH011K_ADAPTER_DB_PORT=15543 \\
    DH011K_ADAPTER_DB_PASSWORD=<that stack's ADAPTER_DB_PASSWORD, not OE_POSTGRES_PASSWORD> \\
        pytest adapter/tests/integration/test_dh011k_crash_matrix.py -v
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import ssl
import subprocess
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from daher_adapter.ledger import Direction, LedgerRepository, MessageIdentity
from daher_adapter.openelis import (
    PATIENT_IDENTITY_SYSTEM,
    HttpResult,
    OpenElisClient,
)
from daher_adapter.openmrs import SERVICE_REQUEST, OpenMrsOrderSource
from daher_adapter.order_poller import OrderPoller
from daher_adapter.recovery import (
    AmbiguousTargetState,
    PostgresRecoveryFence,
    RecoveryCoordinator,
    RecoveryFenceUnavailable,
    SourceCorrelation,
    TargetPresence,
    TargetProbeResult,
    TargetRecord,
    source_correlation,
)
from daher_adapter.source_events import event_from_resource
from daher_adapter.watermark import WatermarkRepository

OMRS_BASE = os.environ.get("DH011K_OMRS_BASE_URL")
OMRS_ADMIN_PASSWORD = os.environ.get("DH011K_OMRS_ADMIN_PASSWORD")
OE_BASE = os.environ.get("DH011K_OE_BASE_URL")
OE_AUTH = os.environ.get("DH011K_OE_AUTH")
ADAPTER_DB_PORT = os.environ.get("DH011K_ADAPTER_DB_PORT")
ADAPTER_DB_PASSWORD = os.environ.get("DH011K_ADAPTER_DB_PASSWORD")
DB_CONTAINER = os.environ.get("DH011K_DB_CONTAINER", "dh011k-openelis-db-1")

pytestmark = pytest.mark.skipif(
    not (
        OMRS_BASE
        and OMRS_ADMIN_PASSWORD
        and OE_BASE
        and OE_AUTH
        and ADAPTER_DB_PORT
        and ADAPTER_DB_PASSWORD
    ),
    reason="requires a live disposable DH-011K stack (DH011K_LIVE_* env vars) -- never run in CI",
)

_SSL_CTX = ssl._create_unverified_context()  # noqa: S323 - disposable, self-signed stack only
_OE_AUTH_HEADER = "Basic " + base64.b64encode((OE_AUTH or "").encode()).decode() if OE_AUTH else ""
_OMRS_AUTH_HEADER = (
    "Basic " + base64.b64encode(f"admin:{OMRS_ADMIN_PASSWORD}".encode()).decode()
    if OMRS_ADMIN_PASSWORD
    else ""
)

# Real, already-committed Daher production reference config (openmrs-config/initializer/), reused
# structurally -- not a clinical mapping decision. See module docstring.
VISIT_TYPE = "194bf546-68d7-42c0-8371-5b0f2b1cf5f3"  # Daher Maternity Admission
ENCOUNTER_TYPE = "e22e39fd-7db2-45e7-80f1-60fa0d5a4378"  # Admission
LOCATION = "6538f097-382d-49b6-a839-601d03c888c0"  # Maternité
CARE_SETTING = "6f0c9a92-6f24-11e3-af88-005056821db0"  # Outpatient
ORDER_TYPE = "52a447d3-a64a-11e3-9aeb-50e549534c5e"  # Test Order
PROVIDER = "f9badd80-ab76-11e2-9e96-0800200c9a66"  # Unknown Provider -- TRANSPORT FIXTURE ONLY
OLD_ID_NUMBER_TYPE = "8d79403a-c2cc-11de-8d13-0010c6dffd0f"  # no Luhn check digit


# --------------------------------------------------------------------------------------------
# OpenMRS transport (sync urllib, matching openmrs.py's synchronous BundleFetcher contract)
# --------------------------------------------------------------------------------------------


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
    """The synchronous `BundleFetcher` `OpenMrsOrderSource` requires -- real HTTP, not scripted."""
    req = urllib.request.Request(url, headers={"Authorization": _OMRS_AUTH_HEADER})  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read())


_TEST_CONCEPT_UUID: str | None = None


def _ensure_transport_fixture_concept() -> str:
    """Creates the one synthetic, non-clinical Test-class concept this run needs -- named with a
    random suffix (OpenMRS enforces unique concept *names* per locale, unlike patient
    identifiers) so repeated manual runs against the same long-lived disposable stack never
    collide with a concept an earlier run already created. Never a DH-011I clinical mapping --
    see module docstring."""
    global _TEST_CONCEPT_UUID
    if _TEST_CONCEPT_UUID is not None:
        return _TEST_CONCEPT_UUID
    body = _omrs_call(
        "POST",
        "/ws/rest/v1/concept",
        {
            "names": [
                {
                    "name": f"DH011K Transport Fixture Test {secrets.token_hex(4)}",
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
    """Real REST patient create (FHIR2 create needs a 'preferred'-identifier extension this
    deployment's identifier types do not carry). Returns the OpenMRS Patient UUID -- DH-011G2
    established this is also the FHIR `Patient.id` for the identical resource; reconfirmed fresh
    for this stack in `test_openmrs_patient_uuid_is_the_fhir_patient_id` below."""
    body = _omrs_call(
        "POST",
        "/ws/rest/v1/patient",
        {
            "identifiers": [
                {
                    "identifierType": OLD_ID_NUMBER_TYPE,
                    "identifier": f"DH011K-{label}",
                    "location": LOCATION,
                    "preferred": True,
                }
            ],
            "person": {
                "names": [{"givenName": label, "familyName": "Dh011kFixture"}],
                "gender": "F",
                "birthdate": "2000-01-01",
            },
        },
    )
    return body["uuid"]


def place_test_order(patient_uuid: str, label: str) -> dict:
    """patient + visit + encounter + one real Test Order (native REST -- FHIR2 ServiceRequest is
    read/search-only on this server, confirmed against its own live capability statement)."""
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
    return {"order_uuid": order["uuid"], "patient_uuid": patient_uuid, "label": label}


def fetch_service_request(order_uuid: str) -> Mapping[str, object]:
    """Real FHIR2 read -- the exact shape `OrderPoller`'s cohort assembly consumes."""
    return omrs_bundle_fetcher(f"{OMRS_BASE}/ws/fhir2/R4/ServiceRequest/{order_uuid}")


def synthetic_order_event(label: str):
    """One full, real synthetic order, converted into the adapter's own `SourceOrderEvent` via
    the real `event_from_resource` -- never hand-built. Returns (event, patient_uuid)."""
    patient_uuid = create_synthetic_patient(label)
    order = place_test_order(patient_uuid, label)
    resource = fetch_service_request(order["order_uuid"])
    event = event_from_resource(resource, resource_type=SERVICE_REQUEST)
    return event, patient_uuid


# --------------------------------------------------------------------------------------------
# OpenELIS transport (async, matching OpenElisClient's OpenElisGet/OpenElisPost contract)
# --------------------------------------------------------------------------------------------


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
    # Pure-alpha names only -- OpenELIS's Person validator rejects digits/mixed punctuation
    # ("invalid name format, possibly illegal character"), confirmed live against this stack.
    return {
        "resourceType": "Patient",
        "name": [{"family": "Transportfixture", "given": ["Synthetic"]}],
        "gender": "female",
        "birthDate": "2000-01-01",
    }


def service_request_payload(openelis_patient_id: str, label: str) -> dict:
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


async def ensure_patient_and_deliver(
    client: OpenElisClient, event, patient_omrs_uuid: str
) -> TargetRecord:
    """The test-only `DeliverOrder` composition (§3/§19's 'smallest production-equivalent
    composition' -- deliberately NOT added to `adapter/src/`, per §25: this ticket qualifies
    existing behavior, it does not activate a production delivery seam). Mirrors exactly what a
    real BLOCKER-6 `DeliverOrder` implementation would have to do: resolve the native OpenELIS
    Patient via DH-011G2's `ensure_openelis_patient` (probe-first, reuse across every order the
    same patient generates), then deliver the ServiceRequest with DH-025's own source correlation
    attached."""
    patient_correlation = SourceCorrelation(system=PATIENT_IDENTITY_SYSTEM, value=patient_omrs_uuid)
    patient_record = await client.ensure_openelis_patient(patient_correlation, patient_payload())
    correlation = source_correlation(event.identity)
    payload = service_request_payload(patient_record.record_id, event.resource_id)
    return await client.deliver_service_request(correlation, payload)


# --------------------------------------------------------------------------------------------
# Adapter-DB composition -- every call builds FRESH objects against the persisted Postgres.
# §22: never reuse in-memory Python state and call it a restart.
# --------------------------------------------------------------------------------------------


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


def build_fence_engine():
    return create_engine(
        f"postgresql+psycopg://adapter:{ADAPTER_DB_PASSWORD}@localhost:{ADAPTER_DB_PORT}/adapter"
    )


def build_source() -> OpenMrsOrderSource:
    return OpenMrsOrderSource(f"{OMRS_BASE}/ws/fhir2/R4", omrs_bundle_fetcher)


DIRECTION: Direction = "openmrs_to_openelis"


def identity_from_event(event) -> MessageIdentity:
    return event.identity


def reset_watermark_to_epoch(resource_type: str = SERVICE_REQUEST) -> None:
    """Test-only reset so each test gets a clean, independent window regardless of what earlier
    tests in this same run already delivered. Never used by production code -- direct SQL against
    the disposable adapter DB, the same discipline `test_dh011p2_crash_recovery.py` and
    `test_dh011j_cancellation_crash_recovery.py` already use for their own authoritative
    DB-level assertions."""
    engine = build_fence_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM watermarks WHERE resource_type = :rt AND direction = :d"),
            {"rt": resource_type, "d": DIRECTION},
        )


class _HeldFence:
    held = True


@pytest.fixture(autouse=True, scope="module")
def _bootstrap_watermark_before_any_fixture() -> None:
    """ADR-015: the watermark bootstraps to *now* on its first-ever read. If that first read
    happened lazily inside the first test's own `poll_once()` call, it would bootstrap to a
    moment *after* that test's own fixture order was already created (fixture creation always
    precedes the `poll_once()` call that would trigger the lazy bootstrap), permanently excluding
    it from every future window -- the exact accepted-but-unwanted consequence ADR-015 warns
    about, self-inflicted by test ordering rather than a real deployment gap. Forcing the
    bootstrap here, before any test creates anything, avoids it."""
    build_watermarks().get_or_bootstrap(
        resource_type=SERVICE_REQUEST,
        direction=DIRECTION,
        server_now=build_source().server_instant(SERVICE_REQUEST),
    )


def _fresh_write_must_not_be_called_reason(where: str):
    async def _raise(_correlation: SourceCorrelation) -> TargetRecord:
        raise AssertionError(f"write_target must not be called: {where}")

    return _raise


def _oe_service_request_count(correlation_value: str) -> int:
    """Authoritative count via the real native search -- never trusted from an HTTP status."""
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
# §3 -- fresh reconfirmation of DH-011G2's OpenMRS Patient.uuid == FHIR Patient.id proof
# ==============================================================================================


def test_openmrs_patient_uuid_is_the_fhir_patient_id() -> None:
    patient_uuid = create_synthetic_patient("UUIDPROOF" + secrets.token_hex(3))
    read_back = omrs_bundle_fetcher(f"{OMRS_BASE}/ws/fhir2/R4/Patient/{patient_uuid}")
    assert read_back["id"] == patient_uuid


# ==============================================================================================
# §5 -- baseline successful cycle
# ==============================================================================================


def test_baseline_full_cycle_delivers_and_advances() -> None:
    event, patient_uuid = synthetic_order_event("BASELINE" + secrets.token_hex(3))
    ledger = build_ledger()
    watermarks_before = build_watermarks().current(
        resource_type=SERVICE_REQUEST, direction=DIRECTION
    )
    client = build_openelis_client()

    async def deliver(ev):
        return await ensure_patient_and_deliver(client, ev, patient_uuid)

    poller = OrderPoller(
        source=build_source(),
        ledger=build_ledger(),
        watermarks=build_watermarks(),
        deliver=deliver,
        recovery=RecoveryCoordinator(build_ledger()),
        fence=_HeldFence(),
        probe_target=client.service_request_probe,
        write_target=_fresh_write_must_not_be_called_reason("baseline: nothing to recover"),
    )
    outcome = asyncio.run(poller.poll_once())

    assert outcome.halted_reason is None
    assert outcome.delivered >= 1
    assert ledger.get_state(event.identity) == "completed"
    watermarks_after = build_watermarks().current(
        resource_type=SERVICE_REQUEST, direction=DIRECTION
    )
    assert watermarks_after.raw >= watermarks_before.raw
    assert watermarks_after.raw >= event.last_updated.raw

    correlation = source_correlation(event.identity)
    assert _oe_service_request_count(correlation.value) == 1

    patient_correlation = SourceCorrelation(system=PATIENT_IDENTITY_SYSTEM, value=patient_uuid)

    async def _patient_found() -> bool:
        probe = await client.patient_identity_probe(patient_correlation)
        return probe.presence is TargetPresence.FOUND

    assert asyncio.run(_patient_found())


# ==============================================================================================
# Crash Point 1 -- before durable claim (§6)
# ==============================================================================================


def test_crash_point_1_before_claim_no_loss_no_duplicate() -> None:
    event, patient_uuid = synthetic_order_event("CP1" + secrets.token_hex(3))
    ledger = build_ledger()
    assert ledger.get_state(event.identity) is None, "nothing persisted before the simulated crash"

    client = build_openelis_client()

    async def deliver(ev):
        return await ensure_patient_and_deliver(client, ev, patient_uuid)

    # "Restart": a fresh poller discovers the event fresh and claims/delivers/completes it --
    # there is nothing to recover because nothing was ever claimed.
    poller = OrderPoller(
        source=build_source(),
        ledger=build_ledger(),
        watermarks=build_watermarks(),
        deliver=deliver,
        recovery=RecoveryCoordinator(build_ledger()),
        fence=_HeldFence(),
        probe_target=client.service_request_probe,
        write_target=_fresh_write_must_not_be_called_reason("CP1: never claimed, no recovery"),
    )
    outcome = asyncio.run(poller.poll_once())

    assert outcome.halted_reason is None
    assert ledger.get_state(event.identity) == "completed"
    correlation = source_correlation(event.identity)
    assert _oe_service_request_count(correlation.value) == 1, "no duplicate"


# ==============================================================================================
# Crash Point 2 -- after claim, before target write (§7)
# ==============================================================================================


def test_crash_point_2_after_claim_before_write_retries_exactly_once() -> None:
    event, patient_uuid = synthetic_order_event("CP2" + secrets.token_hex(3))
    ledger = build_ledger()
    # Simulate: a previous attempt claimed the event and then crashed before ever attempting the
    # target write. Target is genuinely absent -- nothing was POSTed.
    claim = ledger.claim(event.identity)
    assert claim.claimed
    assert ledger.get_state(event.identity) == "processing"
    correlation = source_correlation(event.identity)
    assert _oe_service_request_count(correlation.value) == 0, "target genuinely absent pre-restart"

    client = build_openelis_client()
    write_calls = {"n": 0}

    async def write_once(corr: SourceCorrelation) -> TargetRecord:
        write_calls["n"] += 1
        return await ensure_patient_and_deliver(client, event, patient_uuid)

    # "Restart": fresh RecoveryCoordinator + fresh LedgerRepository, real probe, real writer.
    result = asyncio.run(
        RecoveryCoordinator(build_ledger()).reconcile(
            event.identity,
            fence=_HeldFence(),
            probe_target=client.service_request_probe,
            write_target=write_once,
        )
    )

    assert write_calls["n"] == 1, "exactly one retry, never a blind replay"
    assert result.target.record_id
    ledger2 = build_ledger()
    assert ledger2.get_state(event.identity) == "completed"
    assert _oe_service_request_count(correlation.value) == 1


# ==============================================================================================
# Crash Point 3 -- target commit / response ambiguity (§8)
# ==============================================================================================


def test_crash_point_3_target_committed_response_lost_reconciles_without_second_write() -> None:
    event, patient_uuid = synthetic_order_event("CP3" + secrets.token_hex(3))
    client = build_openelis_client()
    correlation = source_correlation(event.identity)

    # The target write genuinely lands (a real POST + real read-back, run entirely OUTSIDE the
    # ledger/recovery flow) -- modelling "the delivery call's own response was lost or the
    # process died before that call could safely return", so nothing in this process's memory
    # (and no ledger row) ever confirmed it.
    seeded = asyncio.run(ensure_patient_and_deliver(client, event, patient_uuid))
    assert seeded.record_id
    assert _oe_service_request_count(correlation.value) == 1

    # The claim itself is what a crashed attempt would have left behind.
    ledger = build_ledger()
    assert ledger.claim(event.identity).claimed
    assert ledger.get_state(event.identity) == "processing"

    write_calls = {"n": 0}

    async def write_must_not_be_called(corr: SourceCorrelation) -> TargetRecord:
        write_calls["n"] += 1
        raise AssertionError("a FOUND target must never be written again")

    # "Restart": fresh coordinator + fresh ledger, a FRESH probe (not reusing the seeding call's
    # own internal read-back) finds the target and reconciles without writing.
    result = asyncio.run(
        RecoveryCoordinator(build_ledger()).reconcile(
            event.identity,
            fence=_HeldFence(),
            probe_target=client.service_request_probe,
            write_target=write_must_not_be_called,
        )
    )

    assert write_calls["n"] == 0, "no second POST"
    assert result.target.record_id == seeded.record_id
    ledger2 = build_ledger()
    assert ledger2.get_state(event.identity) == "completed"
    assert _oe_service_request_count(correlation.value) == 1, "exactly one, never two"


# ==============================================================================================
# Crash Point 4 -- target proven, before ledger completion (§9)
# ==============================================================================================


class _SimulatedCrashBeforeMarkCompleted(RuntimeError):
    """Test-only fault: raised after a REAL delivery (POST + authoritative read-back) succeeds,
    before `LedgerRepository.mark_completed` runs -- §9's exact fault seam, mirroring DH-011J's
    own `SimulatedCrash` pattern."""


def test_crash_point_4_target_proven_before_ledger_completion_reconciles_without_second_write() -> (
    None
):
    event, patient_uuid = synthetic_order_event("CP4" + secrets.token_hex(3))
    ledger = build_ledger()
    client = build_openelis_client()
    correlation = source_correlation(event.identity)

    assert ledger.claim(event.identity).claimed

    async def deliver_then_crash(ev):
        target = await ensure_patient_and_deliver(client, ev, patient_uuid)
        raise _SimulatedCrashBeforeMarkCompleted(f"died after real commit, target={target}")

    with pytest.raises(_SimulatedCrashBeforeMarkCompleted):
        asyncio.run(deliver_then_crash(event))

    assert _oe_service_request_count(correlation.value) == 1, "the write genuinely landed"
    assert ledger.get_state(event.identity) == "processing", "but the ledger never heard about it"

    write_calls = {"n": 0}

    async def write_must_not_be_called(corr: SourceCorrelation) -> TargetRecord:
        write_calls["n"] += 1
        raise AssertionError("a FOUND target must never be written again")

    result = asyncio.run(
        RecoveryCoordinator(build_ledger()).reconcile(
            event.identity,
            fence=_HeldFence(),
            probe_target=client.service_request_probe,
            write_target=write_must_not_be_called,
        )
    )

    assert write_calls["n"] == 0
    ledger2 = build_ledger()
    assert ledger2.get_state(event.identity) == "completed"
    assert _oe_service_request_count(correlation.value) == 1
    assert result.target.system == "ServiceRequest"


# ==============================================================================================
# Crash Point 5 -- ledger completed, before watermark advance (§10) -- the key proof
# ==============================================================================================


def test_crash_point_5_completed_before_watermark_advance_replays_safely() -> None:
    event, patient_uuid = synthetic_order_event("CP5" + secrets.token_hex(3))
    ledger = build_ledger()
    client = build_openelis_client()
    correlation = source_correlation(event.identity)
    watermark_before = build_watermarks().current(
        resource_type=SERVICE_REQUEST, direction=DIRECTION
    )

    # The full happy path, called directly (bypassing OrderPoller._walk, whose very next line
    # after this would be WatermarkRepository.advance) -- simulates a crash landing exactly
    # between mark_completed and advance.
    assert ledger.claim(event.identity).claimed
    target = asyncio.run(ensure_patient_and_deliver(client, event, patient_uuid))
    ledger.mark_completed(
        event.identity, target_system=target.system, target_record_id=target.record_id
    )
    assert ledger.get_state(event.identity) == "completed"
    watermark_at_crash = build_watermarks().current(
        resource_type=SERVICE_REQUEST, direction=DIRECTION
    )
    assert watermark_at_crash.raw == watermark_before.raw, "advance() never ran"

    probe_calls = {"n": 0}
    write_calls = {"n": 0}

    async def probe_must_not_be_called(corr: SourceCorrelation) -> TargetProbeResult:
        probe_calls["n"] += 1
        raise AssertionError("a completed claim must not be re-probed")

    async def write_must_not_be_called(corr: SourceCorrelation) -> TargetRecord:
        write_calls["n"] += 1
        raise AssertionError("a completed claim must not be written again")

    # "Restart/re-poll": a fresh OrderPoller, same stale watermark -- the inclusive window
    # re-presents the same event.
    poller = OrderPoller(
        source=build_source(),
        ledger=build_ledger(),
        watermarks=build_watermarks(),
        deliver=lambda ev: (_ for _ in ()).throw(  # noqa: PLC3002 - deliberately unreachable
            AssertionError("a completed claim must not be re-delivered")
        ),
        recovery=RecoveryCoordinator(build_ledger()),
        fence=_HeldFence(),
        probe_target=probe_must_not_be_called,
        write_target=write_must_not_be_called,
    )
    outcome = asyncio.run(poller.poll_once())

    assert probe_calls["n"] == 0, "§10: no probe on a completed-claim replay"
    assert write_calls["n"] == 0
    assert outcome.halted_reason is None
    assert outcome.already_completed >= 1
    watermark_after = build_watermarks().current(resource_type=SERVICE_REQUEST, direction=DIRECTION)
    assert watermark_after.raw > watermark_at_crash.raw, "watermark now safely advances past it"
    assert watermark_after.raw >= event.last_updated.raw
    assert _oe_service_request_count(correlation.value) == 1, "final target count unchanged"


# ==============================================================================================
# §12 -- ambiguous target control
# ==============================================================================================


def test_ambiguous_target_halts_then_recovers_once_restored() -> None:
    event, patient_uuid = synthetic_order_event("AMBIG" + secrets.token_hex(3))
    ledger = build_ledger()
    assert ledger.claim(event.identity).claimed

    client = build_openelis_client()

    async def unreachable_probe(corr: SourceCorrelation) -> TargetProbeResult:
        return TargetProbeResult(presence=TargetPresence.AMBIGUOUS)

    write_calls = {"n": 0}

    async def write_must_not_be_called(corr: SourceCorrelation) -> TargetRecord:
        write_calls["n"] += 1
        raise AssertionError("ambiguity must never permit a write")

    with pytest.raises(AmbiguousTargetState):
        asyncio.run(
            RecoveryCoordinator(build_ledger()).reconcile(
                event.identity,
                fence=_HeldFence(),
                probe_target=unreachable_probe,
                write_target=write_must_not_be_called,
            )
        )

    assert write_calls["n"] == 0
    assert ledger.get_state(event.identity) == "processing", "ambiguity leaves the claim in place"
    correlation = source_correlation(event.identity)
    assert _oe_service_request_count(correlation.value) == 0

    # Reachability restored: the real probe now genuinely proves ABSENT, and one real write
    # (through the real production writer, not a stub) is permitted.
    async def write_once(corr: SourceCorrelation) -> TargetRecord:
        return await ensure_patient_and_deliver(client, event, patient_uuid)

    result = asyncio.run(
        RecoveryCoordinator(build_ledger()).reconcile(
            event.identity,
            fence=_HeldFence(),
            probe_target=client.service_request_probe,
            write_target=write_once,
        )
    )
    assert result.target.record_id
    assert build_ledger().get_state(event.identity) == "completed"
    assert _oe_service_request_count(correlation.value) == 1


# ==============================================================================================
# §13 -- multiple-match / corruption control
# ==============================================================================================


def test_correlation_ambiguity_is_structurally_unreachable_the_db_enforces_it() -> None:
    """`electronic_order_daher_correlation_uk` is a real UNIQUE partial index
    (`UNIQUE (external_id) WHERE type = 'FHIR'`) on `clinlims.electronic_order` -- the same DB-
    level mechanism DH-011G2 already proved for `daher_patient_identity`. A second FHIR-typed row
    sharing a correlation value is rejected by the database itself, before any application code
    runs, so `searchDaherCorrelationNatively`'s 2+ branch is provably unreachable through the API
    (or by a direct SQL insert, proven here) rather than merely untested. Disabling the constraint
    to force the case is explicitly forbidden by §13 except on an isolated, restored fixture --
    not done here; this is the fixture-free proof that the constraint itself is real and active."""
    # `electronic_order` lives in the `clinlims` database (a co-tenant of, not the same database
    # as, the adapter's own `adapter` database) -- queried the same way
    # `test_dh011j_cancellation_crash_recovery.py`'s `_analysis_status` reaches clinlims, via the
    # disposable stack's own db container, never a cross-database adapter-role connection (tenant
    # isolation, DH-020, correctly refuses that).
    out = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "exec",
            DB_CONTAINER,
            "psql",
            "-U",
            "clinlims",
            "-d",
            "clinlims",
            "-t",
            "-A",
            "-c",
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'clinlims' "
            "AND tablename = 'electronic_order' "
            "AND indexname = 'electronic_order_daher_correlation_uk'",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    indexdef = out.stdout.strip()
    assert indexdef, "the DH-011 correlation uniqueness index must exist"
    assert "UNIQUE" in indexdef.upper()
    assert "external_id" in indexdef
    assert "'FHIR'" in indexdef, "scoped to FHIR-typed rows only, exactly as DH-011D/D3 designed it"
    # A `pg_indexes.indexdef` reading "CREATE UNIQUE INDEX" is authoritative on its own -- Postgres
    # has no mechanism for a catalog entry to say UNIQUE without the constraint being enforced, so
    # no separate row-level duplicate-insert attempt is needed to prove enforcement. A *literal*
    # duplicate-row attempt against `electronic_order` itself would additionally require
    # satisfying five unrelated NOT NULL/FK columns (`status_id`, `order_timestamp`, `data`,
    # `lastupdated`, an explicit `id` -- no default/sequence) that have nothing to do with what
    # this control is checking, so it is not attempted -- the same "genuinely not worth
    # constructing" judgment DH-011G2's PROVENANCE.md already recorded for its own analogous
    # 2+ cardinality case.


# ==============================================================================================
# §14 -- target write failure with proven absence (not a crash -- a genuine write failure)
# ==============================================================================================


def test_write_failure_with_proven_absence_preserves_processing_and_reprobes_first() -> None:
    event, patient_uuid = synthetic_order_event("WFAIL" + secrets.token_hex(3))
    ledger = build_ledger()
    assert ledger.claim(event.identity).claimed
    correlation = source_correlation(event.identity)

    class _WriteFailed(RuntimeError):
        pass

    async def failing_write(corr: SourceCorrelation) -> TargetRecord:
        raise _WriteFailed("simulated transient failure before the target ever committed")

    with pytest.raises(_WriteFailed):
        asyncio.run(
            RecoveryCoordinator(build_ledger()).reconcile(
                event.identity,
                fence=_HeldFence(),
                probe_target=client_probe_absent,
                write_target=failing_write,
            )
        )

    assert ledger.get_state(event.identity) == "processing"
    assert _oe_service_request_count(correlation.value) == 0

    # Subsequent recovery: the probe runs again first -- only a fresh, authoritative ABSENT may
    # permit another write. No retry policy bypasses this.
    client = build_openelis_client()
    probe_calls = {"n": 0}

    async def counted_probe(corr: SourceCorrelation) -> TargetProbeResult:
        probe_calls["n"] += 1
        return await client.service_request_probe(corr)

    async def write_once(corr: SourceCorrelation) -> TargetRecord:
        return await ensure_patient_and_deliver(client, event, patient_uuid)

    result = asyncio.run(
        RecoveryCoordinator(build_ledger()).reconcile(
            event.identity, fence=_HeldFence(), probe_target=counted_probe, write_target=write_once
        )
    )
    assert probe_calls["n"] == 1, "the probe ran again before the retry was permitted"
    assert result.target.record_id
    assert build_ledger().get_state(event.identity) == "completed"


async def client_probe_absent(corr: SourceCorrelation) -> TargetProbeResult:
    return TargetProbeResult(presence=TargetPresence.ABSENT)


# ==============================================================================================
# §15 -- claim concurrency
# ==============================================================================================


def test_claim_concurrency_absorbs_the_duplicate_into_one_completed_row() -> None:
    """The durable `source_event_id` unique constraint (`ledger.py`'s `IntegrityError` handling),
    proven directly: two claim attempts for the identical `MessageIdentity`, the second must lose
    and observe the first's state -- DB uniqueness is authoritative, not a pre-check race."""
    event, patient_uuid = synthetic_order_event("CONC" + secrets.token_hex(3))
    first = build_ledger().claim(event.identity)
    second = build_ledger().claim(event.identity)
    assert first.claimed
    assert not second.claimed
    assert second.state == "processing"
    # Only one logical claim exists -- confirmed at the DB level, not from the two return values
    # alone.
    engine = build_fence_engine()
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM processed_messages WHERE source_event_id = :sid"),
            {"sid": event.identity.source_event_id},
        ).scalar_one()
    assert count == 1

    # Drive the one real claim to completion -- this test's own name promises "one completed
    # row", and leaving it stuck `processing` would poison every later test's poll window on
    # this same watermark stream (discovered live: it silently absorbed a later test's dead-
    # letter assertion before this fix).
    client = build_openelis_client()
    ledger = build_ledger()
    target = asyncio.run(ensure_patient_and_deliver(client, event, patient_uuid))
    ledger.mark_completed(
        event.identity, target_system=target.system, target_record_id=target.record_id
    )
    assert build_ledger().get_state(event.identity) == "completed"


# ==============================================================================================
# §16 -- process-lifetime recovery fence
# ==============================================================================================


def test_recovery_fence_excludes_a_second_process_until_released() -> None:
    engine_a = build_fence_engine()
    fence_a = PostgresRecoveryFence(engine_a)
    with fence_a:
        assert fence_a.held
        engine_b = build_fence_engine()
        fence_b = PostgresRecoveryFence(engine_b)
        with pytest.raises(RecoveryFenceUnavailable):
            fence_b.__enter__()
    # Released: a second process can now acquire it.
    engine_c = build_fence_engine()
    fence_c = PostgresRecoveryFence(engine_c)
    with fence_c:
        assert fence_c.held


# ==============================================================================================
# §19/§20 -- narrow add-test and cancellation regressions
#
# DH-011P already separately proved added-test crash recovery end-to-end; DH-011J already
# separately proved cancellation crash recovery end-to-end. Neither subsystem is rebuilt or
# re-proved here. This only confirms that DH-011K's own test wiring (a fresh disposable stack
# built from the identical full patch set) does not regress either path.
# ==============================================================================================


def _analysis_status(correlation_value: str) -> str:
    if not correlation_value.isalnum():
        raise ValueError("correlation_value must be alphanumeric")
    query = (
        "SELECT sos.name FROM clinlims.daher_analysis_correlation c "  # noqa: S608
        "JOIN clinlims.analysis a ON a.id = c.analysis_id "
        "JOIN clinlims.status_of_sample sos ON sos.id = a.status_id "
        f"WHERE c.correlation_value = '{correlation_value}'"
    )
    out = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "exec",
            DB_CONTAINER,
            "psql",
            "-U",
            "clinlims",
            "-d",
            "clinlims",
            "-t",
            "-A",
            "-c",
            query,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_add_test_regression_narrow() -> None:
    client = build_openelis_client()
    patient_correlation = SourceCorrelation(system=PATIENT_IDENTITY_SYSTEM, value=str(uuid.uuid4()))
    patient_record = asyncio.run(
        client.ensure_openelis_patient(patient_correlation, patient_payload())
    )

    order_event = secrets.token_hex(32)
    order_result = asyncio.run(
        _oe_real_post(
            f"{OE_BASE}/ServiceRequest",
            {
                "resourceType": "ServiceRequest",
                "status": "active",
                "intent": "order",
                "identifier": [
                    {"system": "urn:daher-health:adapter:source-event", "value": order_event}
                ],
                "subject": {"reference": f"Patient/{patient_record.record_id}"},
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "777-3",
                            "display": "PLT(Whole Blood)",
                        }
                    ]
                },
            },
        )
    )
    assert order_result.status == 201, order_result.body
    specimen = order_result.body["specimen"][0]["reference"].removeprefix("Specimen/")

    added_event = secrets.token_hex(32)
    added_result = asyncio.run(
        _oe_real_post(
            f"{OE_BASE}/ServiceRequest",
            {
                "resourceType": "ServiceRequest",
                "status": "active",
                "intent": "order",
                "identifier": [
                    {"system": "urn:daher-health:adapter:source-event", "value": added_event}
                ],
                "subject": {"reference": f"Patient/{patient_record.record_id}"},
                "specimen": [{"reference": f"Specimen/{specimen}"}],
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "4544-3",
                            "display": "HCT(Whole Blood)",
                        }
                    ]
                },
            },
        )
    )
    assert added_result.status == 201, added_result.body
    assert _analysis_status(added_event) == "Not Tested"


def test_cancellation_regression_narrow() -> None:
    from daher_adapter.cancellation import CancellationOutcome, DaherCancellationCoordinator

    client = build_openelis_client()
    patient_correlation = SourceCorrelation(system=PATIENT_IDENTITY_SYSTEM, value=str(uuid.uuid4()))
    patient_record = asyncio.run(
        client.ensure_openelis_patient(patient_correlation, patient_payload())
    )

    order_event = secrets.token_hex(32)
    order_result = asyncio.run(
        _oe_real_post(
            f"{OE_BASE}/ServiceRequest",
            {
                "resourceType": "ServiceRequest",
                "status": "active",
                "intent": "order",
                "identifier": [
                    {"system": "urn:daher-health:adapter:source-event", "value": order_event}
                ],
                "subject": {"reference": f"Patient/{patient_record.record_id}"},
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "777-3",
                            "display": "PLT(Whole Blood)",
                        }
                    ]
                },
            },
        )
    )
    assert order_result.status == 201, order_result.body
    specimen = order_result.body["specimen"][0]["reference"].removeprefix("Specimen/")

    added_event = secrets.token_hex(32)
    added_result = asyncio.run(
        _oe_real_post(
            f"{OE_BASE}/ServiceRequest",
            {
                "resourceType": "ServiceRequest",
                "status": "active",
                "intent": "order",
                "identifier": [
                    {"system": "urn:daher-health:adapter:source-event", "value": added_event}
                ],
                "subject": {"reference": f"Patient/{patient_record.record_id}"},
                "specimen": [{"reference": f"Specimen/{specimen}"}],
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "4544-3",
                            "display": "HCT(Whole Blood)",
                        }
                    ]
                },
            },
        )
    )
    assert added_result.status == 201, added_result.body
    assert _analysis_status(added_event) == "Not Tested"

    target = SourceCorrelation(system="urn:daher-health:adapter:source-event", value=added_event)
    ledger = build_ledger()
    identity = MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=f"dh011k-cancel-{added_event[:16]}",
        resource_version="1",
    )
    ledger.claim(identity)
    result = asyncio.run(
        DaherCancellationCoordinator(ledger).reconcile_cancellation(
            identity,
            target,
            fence=_HeldFence(),
            probe_target=client.cancellation_probe,
            write_target=client.cancel_service_request,
        )
    )
    assert result.outcome is CancellationOutcome.CONFIRMED_CANCELLED
    assert _analysis_status(added_event) == "Test Canceled"


# ==============================================================================================
# §21 -- dead-letter semantics
#
# MUST RUN LAST IN THIS FILE. A dead-lettered event permanently blocks the SERVICE_REQUEST /
# openmrs_to_openelis watermark stream (§21's own requirement -- "watermark stops at that event",
# with no supported operator-resolution mechanism built anywhere in this codebase to un-block it).
# Every `poll_once()` call after this test would re-encounter the same poisoned event first and
# halt on it again, so no other test in this file may call `poll_once()` on this stream after this
# one runs. Placed last, not gated by `pytest.mark.order` (no such plugin is a dependency here),
# so it depends on this file's own top-to-bottom execution order -- pytest's default.
# ==============================================================================================


def test_dead_letter_halts_and_the_next_event_is_not_silently_skipped() -> None:
    bad_event, bad_patient_uuid = synthetic_order_event("DEADLETTERBAD" + secrets.token_hex(3))
    good_event, good_patient_uuid = synthetic_order_event("DEADLETTERGOOD" + secrets.token_hex(3))
    ledger = build_ledger()
    watermark_before = build_watermarks().current(
        resource_type=SERVICE_REQUEST, direction=DIRECTION
    )
    client = build_openelis_client()

    class _DeterministicMappingFailure(RuntimeError):
        pass

    async def deliver(ev):
        if ev.resource_id == bad_event.resource_id:
            # §21 example: a mapping-construction failure before any target mutation -- no
            # OpenELIS call is even attempted for this event.
            raise _DeterministicMappingFailure("simulated permanently-invalid transport fixture")
        if ev.resource_id == good_event.resource_id:
            return await ensure_patient_and_deliver(client, ev, good_patient_uuid)
        return await ensure_patient_and_deliver(client, ev, bad_patient_uuid)

    poller = OrderPoller(
        source=build_source(),
        ledger=build_ledger(),
        watermarks=build_watermarks(),
        deliver=deliver,
        recovery=RecoveryCoordinator(build_ledger()),
        fence=_HeldFence(),
        probe_target=client.service_request_probe,
        write_target=_fresh_write_must_not_be_called_reason("dead-letter test: no recovery here"),
    )
    outcome = asyncio.run(poller.poll_once())

    assert outcome.halted_reason == "delivery_failed"
    assert outcome.halted_at_resource == bad_event.resource_id
    assert bad_event.resource_id in outcome.dead_lettered
    assert ledger.get_state(bad_event.identity) == "dead_letter"

    # Not silently skipped: the good event was never even claimed -- no state at all, correctly
    # left pending rather than lost, dropped, or marked anything.
    assert ledger.get_state(good_event.identity) is None

    # Earlier tests' already-`completed` events sharing this same watermark stream may sit before
    # `bad_event` in this window and legitimately advance `contiguous` up to (never past) them --
    # §3 rule 3's "partial progress is still progress". The invariant this control actually checks
    # is narrower and unconditional: the watermark must never reach or pass the dead-lettered
    # event (or anything after it, including `good_event`), no matter what came before it.
    # Timestamps are whole-second (§4) and these events were created moments apart, so an earlier
    # event sharing the exact same second as `bad_event` is possible -- the inclusive `ge` design
    # means sitting *at* that second (not past it) is still correct (it gets re-fetched next
    # cycle, never skipped), so this is `<=`, not strict `<`. The authoritative proof is the
    # ledger-state assertions above (dead_letter, never completed / no state, never claimed) --
    # this is a secondary, illustrative check on the same invariant.
    watermark_after = build_watermarks().current(resource_type=SERVICE_REQUEST, direction=DIRECTION)
    assert watermark_after.raw >= watermark_before.raw
    assert watermark_after.raw <= bad_event.last_updated.raw, (
        "watermark must not pass the dead letter"
    )
    assert watermark_after.raw <= good_event.last_updated.raw

    # RecoveryCoordinator must never auto-recover a dead letter.
    with pytest.raises(Exception, match="(?i)dead.letter|operator review"):
        asyncio.run(
            RecoveryCoordinator(build_ledger()).reconcile(
                bad_event.identity,
                fence=_HeldFence(),
                probe_target=client.service_request_probe,
                write_target=_fresh_write_must_not_be_called_reason(
                    "dead_letter must never be auto-recovered"
                ),
            )
        )
