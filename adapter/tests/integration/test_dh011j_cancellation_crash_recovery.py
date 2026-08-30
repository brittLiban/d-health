"""DH-011J §17: crash-after-cancel-commit proof using the REAL production adapter code
(``OpenElisClient``, ``DaherCancellationCoordinator``, ``LedgerRepository``) against a REAL
disposable OpenELIS + Postgres stack -- never a unit fake, never scripted HTTP, never a direct
DB update standing in for the native cancellation.

**Manual / opt-in only**, mirroring ``test_dh011p2_crash_recovery.py`` exactly: this file
assembles real production classes into the sequence a real crash would produce, against a real
disposable Postgres (the real ``alembic upgrade head`` migrations applied for real) and the real
disposable OpenELIS webapp built from this ticket's own patch set (including
``ServiceRequestProviderCancel.java.patch`` and ``DaherCancellationTransaction.java``). Skipped by
default -- including in CI, which has no such stack -- unless ``DH011J_LIVE_*`` is set. It must
NEVER be pointed at a real facility; nothing here checks that, so the operator running it manually
is responsible for using only a disposable, synthetic stack.

Each test creates its own fresh added-test fixture live (via the real, already-proven DH-011M/P
creation path -- a new Specimen, then an added test against it) rather than depending on a
pre-existing correlation supplied by the operator: this is both more self-contained and avoids any
possibility of one test run's cancellation event colliding with ledger state a previous manual run
of this same file left behind.

Usage (against a disposable stack matching PROVENANCE.md's DH-011J compose/build recipe)::

    DH011J_LIVE_BASE_URL=https://localhost:18445/OpenELIS-Global/fhir \\
    DH011J_LIVE_AUTH=admin:adminADMIN! \\
    DH011J_ADAPTER_DB_PORT=15433 \\
    DH011J_ADAPTER_DB_PASSWORD=<the stack's ADAPTER_DB_PASSWORD, not OE_POSTGRES_PASSWORD> \\
    DH011J_PATIENT_UUID=<a synthetic Patient fhir_uuid already created on that stack> \\
        pytest adapter/tests/integration/test_dh011j_cancellation_crash_recovery.py -v
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

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from daher_adapter.cancellation import CancellationOutcome, DaherCancellationCoordinator
from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openelis import HttpResult, OpenElisClient
from daher_adapter.recovery import RecoveryStateError, SourceCorrelation

BASE_URL = os.environ.get("DH011J_LIVE_BASE_URL")
AUTH = os.environ.get("DH011J_LIVE_AUTH")
ADAPTER_DB_PORT = os.environ.get("DH011J_ADAPTER_DB_PORT")
ADAPTER_DB_PASSWORD = os.environ.get("DH011J_ADAPTER_DB_PASSWORD")
PATIENT_UUID = os.environ.get("DH011J_PATIENT_UUID")
DB_CONTAINER = os.environ.get("DH011J_DB_CONTAINER", "dh011j-dh011j-db-1")

pytestmark = pytest.mark.skipif(
    not (BASE_URL and AUTH and ADAPTER_DB_PORT and ADAPTER_DB_PASSWORD and PATIENT_UUID),
    reason="requires a live disposable DH-011J stack (DH011J_LIVE_* env vars) -- never run in CI",
)

_AUTH_HEADER = "Basic " + base64.b64encode((AUTH or "").encode()).decode() if AUTH else ""
# Disposable, self-signed-cert stack only (see module docstring) -- never a real facility.
_SSL_CTX = ssl._create_unverified_context()  # noqa: S323


class SimulatedCrash(RuntimeError):
    """Test-only fault: raised after a REAL cancellation commit/reconciliation succeeds, before
    the coordinator's own ``ledger.mark_completed`` runs -- §17's exact fault seam."""


def _sync_get(url: str) -> HttpResult:
    req = urllib.request.Request(url, headers={"Authorization": _AUTH_HEADER})  # noqa: S310
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode()
            return HttpResult(status=resp.status, body=json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return HttpResult(status=e.code, body=json.loads(body) if body else {})


def _sync_post(url: str, payload) -> HttpResult:
    req = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": _AUTH_HEADER},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode()
            return HttpResult(status=resp.status, body=json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return HttpResult(status=e.code, body=json.loads(body) if body else {})


async def _real_get(url: str) -> HttpResult:
    return await asyncio.to_thread(_sync_get, url)


async def _real_post(url: str, payload) -> HttpResult:
    return await asyncio.to_thread(_sync_post, url, payload)


def _create_added_test_correlation() -> str:
    """One throwaway new order (an isolated Specimen), then one added test against it -- the
    real, already-proven DH-011M/P creation path, over real HTTP. Returns the added test's own
    ``daher_analysis_correlation`` value, freshly created and not yet cancelled. Not itself part
    of what this file proves."""
    order_event = secrets.token_hex(32)
    order_payload = {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "identifier": [{"system": "urn:daher-health:adapter:source-event", "value": order_event}],
        "subject": {"reference": f"Patient/{PATIENT_UUID}"},
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "777-3", "display": "PLT(Whole Blood)"}
            ]
        },
    }
    order_result = asyncio.run(_real_post(f"{BASE_URL}/ServiceRequest", order_payload))
    assert order_result.status == 201, order_result.body
    specimen = order_result.body["specimen"][0]["reference"].removeprefix("Specimen/")

    added_test_event = secrets.token_hex(32)
    added_test_payload = {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "identifier": [
            {"system": "urn:daher-health:adapter:source-event", "value": added_test_event}
        ],
        "subject": {"reference": f"Patient/{PATIENT_UUID}"},
        "specimen": [{"reference": f"Specimen/{specimen}"}],
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "4544-3", "display": "HCT(Whole Blood)"}
            ]
        },
    }
    added_test_result = asyncio.run(_real_post(f"{BASE_URL}/ServiceRequest", added_test_payload))
    assert added_test_result.status == 201, added_test_result.body
    return added_test_event


def _analysis_status(correlation_value: str) -> str:
    """Authoritative status read directly from clinlims -- never inferred from an HTTP response.
    DB_CONTAINER is an operator-supplied env var naming a disposable local container, never
    external input; docker/psql are fixed, hardcoded arguments."""
    if not correlation_value.isalnum():
        # Not attacker input (this file only ever generates hex via secrets.token_hex), but the
        # f-string SQL below only tolerates that alphanumeric shape -- refuse rather than
        # quote-escape a value shaped like something else.
        raise ValueError("correlation_value must be alphanumeric")
    # correlation_value is alphanumeric-checked above, not attacker input.
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


class _Fence:
    held = True


def _build_ledger() -> LedgerRepository:
    engine = create_engine(
        f"postgresql+psycopg://adapter:{ADAPTER_DB_PASSWORD}@localhost:{ADAPTER_DB_PORT}/adapter"
    )
    return LedgerRepository(sessionmaker(bind=engine, expire_on_commit=False))


def test_crash_after_cancellation_commit_before_ledger_completion() -> None:
    """§17. Real $cancel + real reconciliation truly cancels the target Analysis (confirmed at
    the DB level, not by HTTP status); a controlled exception fires before the coordinator's own
    ``mark_completed`` runs; a *fresh* coordinator + fresh ``LedgerRepository`` (simulating a
    restart) must reconcile to the already-cancelled target, call the writer zero times, and
    leave the Analysis's status unchanged (still cancelled, not re-cancelled)."""
    client = OpenElisClient(BASE_URL, _real_get, _real_post)
    ledger = _build_ledger()
    correlation_value = _create_added_test_correlation()
    target = SourceCorrelation(
        system="urn:daher-health:adapter:source-event", value=correlation_value
    )

    assert _analysis_status(correlation_value) == "Not Tested"

    cancel_event_identity = MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=f"dh011j-cancel-crash-{correlation_value[:16]}",
        resource_version="1",
    )

    assert ledger.claim(cancel_event_identity).claimed

    async def crashing_write_target(corr: SourceCorrelation):
        record = await client.cancel_service_request(corr)
        raise SimulatedCrash(f"process died after real cancel commit, state={record.state}")

    with pytest.raises(SimulatedCrash):
        asyncio.run(
            DaherCancellationCoordinator(ledger).reconcile_cancellation(
                cancel_event_identity,
                target,
                fence=_Fence(),
                probe_target=client.cancellation_probe,
                write_target=crashing_write_target,
            )
        )

    assert _analysis_status(correlation_value) == "Test Canceled"
    assert ledger.get_state(cancel_event_identity) == "processing"

    # "Restart": a fresh LedgerRepository/coordinator, no in-memory state reused.
    ledger2 = _build_ledger()
    coordinator2 = DaherCancellationCoordinator(ledger2)
    calls = {"n": 0}

    async def must_not_be_called(_: SourceCorrelation):
        calls["n"] += 1
        raise AssertionError("write_target must never be called on an already-cancelled target")

    result = asyncio.run(
        coordinator2.reconcile_cancellation(
            cancel_event_identity,
            target,
            fence=_Fence(),
            probe_target=client.cancellation_probe,
            write_target=must_not_be_called,
        )
    )

    assert result.outcome is CancellationOutcome.CONFIRMED_CANCELLED
    assert calls["n"] == 0
    assert _analysis_status(correlation_value) == "Test Canceled"
    assert ledger2.get_state(cancel_event_identity) == "completed"


def test_repeat_cancellation_of_the_same_event_is_idempotent() -> None:
    """§16/§7 question 7: replaying the exact same cancellation event (a genuine adapter retry,
    same MessageIdentity) after it already completed must reconcile without a second native
    write -- proven here against the real DB, not inferred from the unit-scripted coordinator
    tests alone."""
    client = OpenElisClient(BASE_URL, _real_get, _real_post)
    ledger = _build_ledger()
    correlation_value = _create_added_test_correlation()
    target = SourceCorrelation(
        system="urn:daher-health:adapter:source-event", value=correlation_value
    )
    identity = MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=f"dh011j-cancel-repeat-{correlation_value[:16]}",
        resource_version="1",
    )

    ledger.claim(identity)
    first = asyncio.run(
        DaherCancellationCoordinator(ledger).reconcile_cancellation(
            identity,
            target,
            fence=_Fence(),
            probe_target=client.cancellation_probe,
            write_target=client.cancel_service_request,
        )
    )
    assert first.outcome is CancellationOutcome.CONFIRMED_CANCELLED
    status_after_first = _analysis_status(correlation_value)

    calls = {"n": 0}

    async def must_not_write(_: SourceCorrelation):
        calls["n"] += 1
        raise AssertionError("a replayed, already-completed cancellation must not re-probe/write")

    with pytest.raises(RecoveryStateError, match="normal duplicate"):
        asyncio.run(
            DaherCancellationCoordinator(ledger).reconcile_cancellation(
                identity,
                target,
                fence=_Fence(),
                probe_target=must_not_write,
                write_target=must_not_write,
            )
        )

    assert calls["n"] == 0
    assert _analysis_status(correlation_value) == status_after_first == "Test Canceled"


def test_concurrent_duplicate_cancellation_via_coordinator_is_recoverable() -> None:
    """DH-011J FINALIZE §6: two independent cancellation events (distinct MessageIdentity, as a
    genuine duplicate delivery would produce -- not a replay of the same event, see the test
    above for that case) racing the same real target through the real
    DaherCancellationCoordinator/OpenElisClient/LedgerRepository, concurrently.

    Required invariant, proven here rather than assumed from the endpoint-level race
    (PROVENANCE.md's DH-011J §18/DH-011J2 §8-11, HTTP only): the target reaches exactly one
    logical cancellation, neither coordinator call raises past `reconcile_cancellation` (a losing
    racer's `$cancel` POST can 500 -- OpenElisClient.cancel_service_request already never trusts
    that status and always reconciles via a fresh probe, so the coordinator layer above it must
    never see that 500 propagate as an unrecoverable failure), and BOTH ledger rows reach
    `completed` -- never stuck `processing`, never dead-lettered solely because another actor won
    the race.
    """
    client = OpenElisClient(BASE_URL, _real_get, _real_post)
    ledger = _build_ledger()
    correlation_value = _create_added_test_correlation()
    target = SourceCorrelation(
        system="urn:daher-health:adapter:source-event", value=correlation_value
    )

    identity_1 = MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=f"dh011j-race1-{correlation_value[:16]}",
        resource_version="1",
    )
    identity_2 = MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=f"dh011j-race2-{correlation_value[:16]}",
        resource_version="1",
    )

    assert ledger.claim(identity_1).claimed
    assert ledger.claim(identity_2).claimed

    coordinator_1 = DaherCancellationCoordinator(_build_ledger())
    coordinator_2 = DaherCancellationCoordinator(_build_ledger())

    async def run_both():
        return await asyncio.gather(
            coordinator_1.reconcile_cancellation(
                identity_1,
                target,
                fence=_Fence(),
                probe_target=client.cancellation_probe,
                write_target=client.cancel_service_request,
            ),
            coordinator_2.reconcile_cancellation(
                identity_2,
                target,
                fence=_Fence(),
                probe_target=client.cancellation_probe,
                write_target=client.cancel_service_request,
            ),
            return_exceptions=True,
        )

    results = asyncio.run(run_both())

    for result in results:
        assert not isinstance(result, BaseException), (
            f"a concurrent duplicate cancellation must reconcile, never raise: {result!r}"
        )

    assert all(result.outcome is CancellationOutcome.CONFIRMED_CANCELLED for result in results), (
        f"both racers must confirm cancelled: {results!r}"
    )

    # Authoritative: the ledger, never trusted from in-memory return values alone.
    ledger_check = _build_ledger()
    assert ledger_check.get_state(identity_1) == "completed"
    assert ledger_check.get_state(identity_2) == "completed"
    assert _analysis_status(correlation_value) == "Test Canceled"
