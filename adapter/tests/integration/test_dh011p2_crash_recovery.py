"""DH-011P2 sections 7-11: crash-recovery proof using the REAL production adapter code
(``OpenElisClient``, ``RecoveryCoordinator``, ``LedgerRepository``) against a REAL disposable
OpenELIS + Postgres stack -- never a unit fake, never scripted HTTP, never a direct DB insert
standing in for recovery, never calling ``service_request_probe`` alone.

**Manual / opt-in only.** There is no production entrypoint that assembles these components
into a running delivery loop yet (DH-011E's poller is real but deliberately unwired; ``app.py``
is a health-check-only FastAPI app -- see PROJECT_STATE.md). This file IS that assembly, for
proof purposes only: a test-only integration harness, not a change to ``src/daher_adapter/``,
and running it does not activate anything. It requires a live disposable OpenELIS + Postgres
stack (see ``openelis-config/patches/order-correlation/PROVENANCE.md``'s DH-011P2 section for
the exact compose/build recipe) and is therefore skipped by default -- including in CI, which
has no such stack -- unless ``DH011P2_LIVE_BASE_URL`` is set. It must NEVER be pointed at a real
facility; nothing here checks that, so the operator running it manually is responsible for
using only a disposable, synthetic stack.

Usage (against a disposable stack matching the compose file this ticket documents)::

    DH011P2_LIVE_BASE_URL=https://localhost:18445/OpenELIS-Global/fhir \\
    DH011P2_LIVE_AUTH=admin:adminADMIN! \\
    DH011P2_ADAPTER_DB_PORT=15433 \\
    DH011P2_ADAPTER_DB_PASSWORD=<the disposable stack's OE_POSTGRES_PASSWORD> \\
    DH011P2_PATIENT_UUID=<a synthetic Patient fhir_uuid already created on that stack> \\
        pytest adapter/tests/integration/test_dh011p2_crash_recovery.py -v
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from daher_adapter.config import Settings
from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openelis import HttpResult, OpenElisClient, deliver_service_request_if_activated
from daher_adapter.recovery import AmbiguousTargetState, RecoveryAction, RecoveryCoordinator

BASE_URL = os.environ.get("DH011P2_LIVE_BASE_URL")
AUTH = os.environ.get("DH011P2_LIVE_AUTH")
ADAPTER_DB_PORT = os.environ.get("DH011P2_ADAPTER_DB_PORT")
ADAPTER_DB_PASSWORD = os.environ.get("DH011P2_ADAPTER_DB_PASSWORD")
PATIENT_UUID = os.environ.get("DH011P2_PATIENT_UUID")
DB_CONTAINER = os.environ.get("DH011P2_DB_CONTAINER", "dh011p2-patched-db-1")

pytestmark = pytest.mark.skipif(
    not (BASE_URL and AUTH and ADAPTER_DB_PORT and ADAPTER_DB_PASSWORD and PATIENT_UUID),
    reason="requires a live disposable DH-011P2 stack (DH011P2_LIVE_* env vars) -- never run in CI",
)

_AUTH_HEADER = "Basic " + base64.b64encode((AUTH or "").encode()).decode() if AUTH else ""
# Disposable, self-signed-cert stack only (see module docstring) -- never a real facility.
_SSL_CTX = ssl._create_unverified_context()  # noqa: S323


class SimulatedCrash(RuntimeError):
    """Test-only fault: raised after a REAL target commit/read-back succeeds, before the
    coordinator's own ``ledger.mark_completed`` runs -- section 9's exact fault seam."""


def _sync_get(url: str) -> HttpResult:
    # url is always {BASE_URL}/... built from operator-supplied env vars, never external input.
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


def _db_counts() -> dict[str, int]:
    """Authoritative counts read directly from clinlims -- never inferred from an HTTP
    response or a log line. DB_CONTAINER is an operator-supplied env var naming a disposable
    local container, never external input; docker/psql are fixed, hardcoded arguments."""
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
            "-F",
            ",",
            "-c",
            "SELECT (SELECT count(*) FROM clinlims.analysis), "
            "(SELECT count(*) FROM clinlims.daher_analysis_correlation), "
            "(SELECT count(*) FROM clinlims.sample_item)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    a, c, s = out.stdout.strip().split(",")
    return {"analysis": int(a), "correlation": int(c), "sample_item": int(s)}


def _create_specimen() -> str:
    """One throwaway new order via the real, already-patched Option P path, purely to obtain
    an isolated Specimen to add a test against -- not itself part of what this file proves."""
    import secrets

    payload = {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "identifier": [
            {"system": "urn:daher-health:adapter:source-event", "value": secrets.token_hex(32)}
        ],
        "subject": {"reference": f"Patient/{PATIENT_UUID}"},
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "777-3", "display": "PLT(Whole Blood)"}
            ]
        },
    }
    result = asyncio.run(_real_post(f"{BASE_URL}/ServiceRequest", payload))
    assert result.status == 201, result.body
    return result.body["specimen"][0]["reference"].removeprefix("Specimen/")


class _Fence:
    held = True


def _build_settings() -> Settings:
    return Settings(
        timezone="Africa/Djibouti",
        db_host="localhost",
        db_port=int(ADAPTER_DB_PORT),
        db_name="adapter",
        db_user="adapter",
        db_password=ADAPTER_DB_PASSWORD,
        audit_path=None,
        retry_max_attempts=5,
        retry_multiplier_seconds=1.0,
        retry_max_seconds=30.0,
        openelis_site_code="DJ01",
    )


def _build_ledger() -> LedgerRepository:
    engine = create_engine(
        f"postgresql+psycopg://adapter:{ADAPTER_DB_PASSWORD}@localhost:{ADAPTER_DB_PORT}/adapter"
    )
    return LedgerRepository(sessionmaker(bind=engine, expire_on_commit=False))


def test_crash_after_target_commit_before_ledger_completion() -> None:
    """§7-9-10. Real POST+reconcile commits a real Analysis and correlation row; a controlled
    exception fires before RecoveryCoordinator.reconcile's own mark_completed runs; a *fresh*
    RecoveryCoordinator + LedgerRepository (simulating restart) must reconcile to the existing
    write, call the writer zero times, and leave Analysis/correlation counts unchanged."""
    settings = _build_settings()
    client = OpenElisClient(BASE_URL, _real_get, _real_post)
    ledger = _build_ledger()
    specimen = _create_specimen()

    identity = MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=f"dh011p2-crash-{specimen[:8]}",
        resource_version="1",
    )
    payload = {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "subject": {"reference": f"Patient/{PATIENT_UUID}"},
        "specimen": [{"reference": f"Specimen/{specimen}"}],
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "4544-3", "display": "HCT(Whole Blood)"}
            ]
        },
    }

    pre = _db_counts()
    assert ledger.claim(identity).claimed

    async def crashing_write_target(corr):
        record = await deliver_service_request_if_activated(client, settings, corr, payload)
        raise SimulatedCrash(f"process died after real commit, target={record.record_id}")

    with pytest.raises(SimulatedCrash):
        asyncio.run(
            RecoveryCoordinator(ledger).reconcile(
                identity,
                fence=_Fence(),
                probe_target=client.service_request_probe,
                write_target=crashing_write_target,
            )
        )

    post_target = _db_counts()
    assert post_target["analysis"] == pre["analysis"] + 1
    assert post_target["correlation"] == pre["correlation"] + 1
    assert post_target["sample_item"] == pre["sample_item"]
    assert ledger.get_state(identity) == "processing"

    # "Restart": a fresh LedgerRepository/RecoveryCoordinator, no in-memory state reused.
    ledger2 = _build_ledger()
    coordinator2 = RecoveryCoordinator(ledger2)
    calls = {"n": 0}

    async def must_not_be_called(corr):
        calls["n"] += 1
        raise AssertionError("write_target must never be called on a FOUND recovery")

    result = asyncio.run(
        coordinator2.reconcile(
            identity,
            fence=_Fence(),
            probe_target=client.service_request_probe,
            write_target=must_not_be_called,
        )
    )

    assert result.action is RecoveryAction.RECONCILED_EXISTING
    assert calls["n"] == 0
    assert _db_counts() == post_target
    assert ledger2.get_state(identity) == "completed"


def test_timeout_after_commit_then_recovery_makes_no_second_mutation() -> None:
    """§11. A POST callable that really reaches the server (a real commit happens) but then
    raises to the caller (simulating a lost response) makes `deliver_service_request` raise
    `AmbiguousTargetState` without its own reconciliation attempt -- real, already-covered
    behavior, not a defect. The *next* attempt's unconditional probe must find the real write
    and complete the ledger without ever calling the writer again."""
    settings = _build_settings()
    client = OpenElisClient(BASE_URL, _real_get, _real_post)
    ledger = _build_ledger()
    specimen = _create_specimen()

    identity = MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=f"dh011p2-timeout-{specimen[:8]}",
        resource_version="1",
    )
    payload = {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "subject": {"reference": f"Patient/{PATIENT_UUID}"},
        "specimen": [{"reference": f"Specimen/{specimen}"}],
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "787-2", "display": "MCV(Whole Blood)"}
            ]
        },
    }

    pre = _db_counts()
    assert ledger.claim(identity).claimed

    class LossyClient(OpenElisClient):
        async def _post_with_loss(self, url, body):
            await _real_post(url, body)  # really lands and commits server-side
            raise TimeoutError("simulated: connection lost after send")

    lossy = LossyClient(BASE_URL, _real_get, None)
    lossy._post = lossy._post_with_loss

    async def lossy_write_target(corr):
        return await deliver_service_request_if_activated(lossy, settings, corr, payload)

    with pytest.raises(AmbiguousTargetState):
        asyncio.run(
            RecoveryCoordinator(ledger).reconcile(
                identity,
                fence=_Fence(),
                probe_target=lossy.service_request_probe,
                write_target=lossy_write_target,
            )
        )

    mid = _db_counts()
    assert mid["analysis"] == pre["analysis"] + 1  # the real write happened despite the timeout
    assert ledger.get_state(identity) == "processing"

    calls = {"n": 0}

    async def must_not_be_called(corr):
        calls["n"] += 1
        raise AssertionError("no blind retry POST")

    result = asyncio.run(
        RecoveryCoordinator(ledger).reconcile(
            identity,
            fence=_Fence(),
            probe_target=client.service_request_probe,
            write_target=must_not_be_called,
        )
    )
    assert result.action is RecoveryAction.RECONCILED_EXISTING
    assert calls["n"] == 0
    post = _db_counts()
    assert post["analysis"] == pre["analysis"] + 1  # still exactly one -- no double write
    assert post["correlation"] == pre["correlation"] + 1
    assert ledger.get_state(identity) == "completed"
