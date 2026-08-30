"""DH-011G2 (LIBAN DECISION: SELECT OPTION D): native OpenMRS-UUID <-> OpenELIS Patient linkage
proof using the REAL production adapter code (``OpenElisClient``,
``DaherPatientIdentityCoordinator``, ``LedgerRepository``) against a REAL disposable OpenELIS +
Postgres stack -- never a unit fake, never scripted HTTP, never a direct DB update standing in
for the native create/search path.

**Manual / opt-in only**, mirroring ``test_dh011j_cancellation_crash_recovery.py`` exactly:
skipped by default -- including in CI, which has no such stack -- unless ``DH011G2_LIVE_*`` is
set. It must NEVER be pointed at a real facility; nothing here checks that, so the operator
running it manually is responsible for using only a disposable, synthetic stack. Every name/
identifier used below is synthetic (``DH011G2Syn*``/random UUIDs) -- see this file's own
PROVENANCE.md section for the exact disposable-stack recipe this was proven against.

Usage (against a disposable stack matching PROVENANCE.md's DH-011G2 compose/build recipe)::

    DH011G2_LIVE_BASE_URL=https://localhost:18443/OpenELIS-Global/fhir \\
    DH011G2_LIVE_AUTH=admin:adminADMIN! \\
    DH011G2_ADAPTER_DB_PORT=15533 \\
    DH011G2_ADAPTER_DB_PASSWORD=<the stack's ADAPTER_DB_PASSWORD> \\
        pytest adapter/tests/integration/test_dh011g2_patient_identity.py -v
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import ssl
import urllib.error
import urllib.request
import uuid as uuid_module

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openelis import HttpResult, OpenElisClient
from daher_adapter.patient_identity import DaherPatientIdentityCoordinator
from daher_adapter.recovery import (
    AmbiguousTargetState,
    RecoveryAction,
    SourceCorrelation,
    TargetPresence,
)

BASE_URL = os.environ.get("DH011G2_LIVE_BASE_URL")
AUTH = os.environ.get("DH011G2_LIVE_AUTH")
ADAPTER_DB_PORT = os.environ.get("DH011G2_ADAPTER_DB_PORT")
ADAPTER_DB_PASSWORD = os.environ.get("DH011G2_ADAPTER_DB_PASSWORD")

pytestmark = pytest.mark.skipif(
    not (BASE_URL and AUTH and ADAPTER_DB_PORT and ADAPTER_DB_PASSWORD),
    reason="requires a live disposable DH-011G2 stack (DH011G2_LIVE_* env vars) -- never run in CI",
)

_AUTH_HEADER = "Basic " + base64.b64encode((AUTH or "").encode()).decode() if AUTH else ""
# Disposable, self-signed-cert stack only (see module docstring) -- never a real facility.
_SSL_CTX = ssl._create_unverified_context()  # noqa: S323


class SimulatedCrash(RuntimeError):
    """Test-only fault: raised after a REAL Patient create/link commit succeeds, before the
    coordinator's own ``ledger.mark_completed`` runs -- §11's exact fault seam."""


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


def _sync_put(url: str, payload) -> HttpResult:
    # PatientProvider's @Update is a PUT, not a POST -- OpenElisClient deliberately has no PUT
    # method (DH-011P2 §15: PUT stays unsupported for Daher delivery). This is a direct-HTTP
    # verification of native behavior only, never an adapter capability.
    req = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": _AUTH_HEADER},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode()
            return HttpResult(status=resp.status, body=json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return HttpResult(status=e.code, body=json.loads(body) if body else {})


async def _real_put(url: str, payload) -> HttpResult:
    return await asyncio.to_thread(_sync_put, url, payload)


def _client() -> OpenElisClient:
    return OpenElisClient(BASE_URL, _real_get, _real_post)


def _synthetic_uuid() -> str:
    """A fresh, never-reused synthetic OpenMRS-shaped Patient UUID for each test -- never a
    fixed constant, so repeated manual runs of this file never collide with a prior run's data
    left behind on the same disposable stack."""
    return str(uuid_module.uuid4())


def _payload(given: str, family: str, birth: str = "2000-01-01", gender: str = "unknown") -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"family": family, "given": [given]}],
        "gender": gender,
        "birthDate": birth,
    }


class _Fence:
    held = True


def _build_ledger() -> LedgerRepository:
    engine = create_engine(
        f"postgresql+psycopg://adapter:{ADAPTER_DB_PASSWORD}@localhost:{ADAPTER_DB_PORT}/adapter"
    )
    return LedgerRepository(sessionmaker(bind=engine, expire_on_commit=False))


# --- §8 cardinality matrix (0/1; 2+ is DB-structurally prevented, see this file's own
# PROVENANCE.md note -- not reproducible here) -----------------------------------------------


def test_search_for_a_never_created_uuid_is_zero() -> None:
    correlation = SourceCorrelation(
        system="urn:daher-health:openmrs-patient-uuid", value=_synthetic_uuid()
    )
    probe = asyncio.run(_client().patient_identity_probe(correlation))
    assert probe.presence is TargetPresence.ABSENT


def test_create_then_search_is_exactly_one() -> None:
    uuid = _synthetic_uuid()
    correlation = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=uuid)
    payload = _payload("PatientCardinality", "Synthetic")

    record = asyncio.run(_client().create_openelis_patient(correlation, payload))
    probe = asyncio.run(_client().patient_identity_probe(correlation))

    assert probe.presence is TargetPresence.FOUND
    assert probe.record.record_id == record.record_id


# --- §9 atomic create+link, rollback proof ----------------------------------------------------


def test_definite_validation_failure_creates_no_patient_and_no_orphan_link() -> None:
    """A name containing digits fails OpenELIS's pre-existing Person validator (unrelated to
    DH-011G2). The create response is a false-201 (known, pre-existing, unfixed by this ticket --
    see PROVENANCE.md), but the atomic transaction must leave zero trace: no native Patient, no
    daher_patient_identity row."""
    uuid = _synthetic_uuid()
    correlation = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=uuid)
    payload = _payload("Bad123Name", "Synthetic")

    with pytest.raises(AmbiguousTargetState):
        asyncio.run(_client().create_openelis_patient(correlation, payload))

    probe = asyncio.run(_client().patient_identity_probe(correlation))
    assert probe.presence is TargetPresence.ABSENT


def test_duplicate_uuid_different_demographics_reconciles_to_the_winner() -> None:
    """§14: the same OpenMRS UUID with completely different demographics must never create a
    second OpenELIS Patient. OpenELIS's own pre-check rejection of the second attempt is itself
    a false-201 (§24 finding, pre-existing, unrelated to split-store) -- but
    ``create_openelis_patient`` never trusts that status either way: its own read-back after the
    second POST finds exactly the first (and only) Patient, which is a legitimate SUCCESS
    reconciliation, not an ambiguity -- the same "duplicate conflict reconciles to the winner"
    shape ``deliver_service_request`` already has for ServiceRequest. The safety property this
    test actually proves is the one that matters: exactly one native Patient exists throughout,
    never a duplicate created out from under the rejection."""
    uuid = _synthetic_uuid()
    correlation = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=uuid)
    first_payload = _payload("PatientDup", "Synthetic")
    second_payload = _payload("CompletelyDifferent", "OtherPerson", birth="1985-05-05")

    first = asyncio.run(_client().create_openelis_patient(correlation, first_payload))
    second = asyncio.run(_client().create_openelis_patient(correlation, second_payload))

    assert second.record_id == first.record_id, (
        "the rejected duplicate must reconcile to the one Patient that actually exists, "
        "never fabricate or duplicate one"
    )

    probe = asyncio.run(_client().patient_identity_probe(correlation))
    assert probe.presence is TargetPresence.FOUND
    assert probe.record.record_id == first.record_id


# --- §16 newborn/twin: identical demographics, distinct source identity -----------------------


def test_twins_with_identical_demographics_get_distinct_patients() -> None:
    """Mother + two newborn twins: same family name, same birth date, no national ID/phone --
    only the OpenMRS UUID differs. Must resolve to three genuinely distinct OpenELIS Patients,
    with zero demographic matching involved."""
    mom_uuid, twin_a_uuid, twin_b_uuid = (_synthetic_uuid() for _ in range(3))
    shared_birth = "2026-01-01"

    mom_corr = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=mom_uuid)
    a_corr = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=twin_a_uuid)
    b_corr = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=twin_b_uuid)

    mom = asyncio.run(
        _client().create_openelis_patient(mom_corr, _payload("Mother", "Twins", "1995-03-01"))
    )
    twin_a = asyncio.run(
        _client().create_openelis_patient(a_corr, _payload("Twin", "Twins", shared_birth, "male"))
    )
    twin_b = asyncio.run(
        _client().create_openelis_patient(b_corr, _payload("Twin", "Twins", shared_birth, "male"))
    )

    ids = {mom.record_id, twin_a.record_id, twin_b.record_id}
    assert len(ids) == 3, "identical demographics must never collapse distinct source identities"


# --- §12/§13: concurrent same-patient and different-patient creation --------------------------


def test_concurrent_same_patient_converges_to_one_native_patient() -> None:
    """Two independent OpenElisClient instances racing ensure_openelis_patient for the SAME
    OpenMRS UUID must converge on exactly one native Patient -- DB uniqueness authoritative, not
    a pre-search race that merely happens not to collide in this run."""
    uuid = _synthetic_uuid()
    correlation = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=uuid)
    payload = _payload("RaceSame", "Synthetic")

    async def attempt(c: OpenElisClient):
        return await c.ensure_openelis_patient(correlation, payload)

    async def race():
        return await asyncio.gather(attempt(_client()), attempt(_client()))

    r1, r2 = asyncio.run(race())
    assert r1.record_id == r2.record_id

    probe = asyncio.run(_client().patient_identity_probe(correlation))
    assert probe.presence is TargetPresence.FOUND
    assert probe.record.record_id == r1.record_id


def test_concurrent_different_patients_are_fully_isolated() -> None:
    uuid_a, uuid_b = _synthetic_uuid(), _synthetic_uuid()
    corr_a = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=uuid_a)
    corr_b = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=uuid_b)

    async def race():
        return await asyncio.gather(
            _client().ensure_openelis_patient(corr_a, _payload("RaceA", "Synthetic")),
            _client().ensure_openelis_patient(corr_b, _payload("RaceB", "Synthetic")),
        )

    r_a, r_b = asyncio.run(race())
    assert r_a.record_id != r_b.record_id

    probe_a = asyncio.run(_client().patient_identity_probe(corr_a))
    probe_b = asyncio.run(_client().patient_identity_probe(corr_b))
    assert probe_a.record.record_id == r_a.record_id
    assert probe_b.record.record_id == r_b.record_id


# --- §11: crash-after-commit, before ledger completion -----------------------------------------


def test_crash_after_patient_commit_before_ledger_completion_is_recoverable() -> None:
    """A real Patient create + linkage commits; a controlled exception fires before the
    coordinator's own ``mark_completed`` runs; a *fresh* coordinator + fresh
    ``LedgerRepository`` (simulating a restart) must reconcile to the already-created Patient,
    call the writer zero times, and leave exactly one native Patient/linkage pair -- this is the
    exact scenario DH-011G's own split-store finding warned about, closed for the Daher identity
    system specifically (§24)."""
    client = _client()
    ledger = _build_ledger()
    uuid = _synthetic_uuid()
    correlation = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=uuid)
    payload = _payload("CrashProof", "Synthetic")

    identity = MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="Patient",
        resource_id=f"dh011g2-crash-{uuid}",
        resource_version="1",
    )
    assert ledger.claim(identity).claimed

    async def crashing_write_target(corr: SourceCorrelation):
        record = await client.create_openelis_patient(corr, payload)
        raise SimulatedCrash(f"process died after real commit, record={record}")

    with pytest.raises(SimulatedCrash):
        asyncio.run(
            DaherPatientIdentityCoordinator(ledger).reconcile_patient(
                identity,
                correlation,
                fence=_Fence(),
                probe_target=client.patient_identity_probe,
                write_target=crashing_write_target,
            )
        )

    assert ledger.get_state(identity) == "processing"

    # "Restart": a fresh LedgerRepository/coordinator, no in-memory state reused.
    ledger2 = _build_ledger()
    calls = {"n": 0}

    async def must_not_be_called(_: SourceCorrelation):
        calls["n"] += 1
        raise AssertionError("write_target must never be called on an already-created target")

    result = asyncio.run(
        DaherPatientIdentityCoordinator(ledger2).reconcile_patient(
            identity,
            correlation,
            fence=_Fence(),
            probe_target=client.patient_identity_probe,
            write_target=must_not_be_called,
        )
    )

    assert result.action is RecoveryAction.RECONCILED_EXISTING
    assert calls["n"] == 0
    assert ledger2.get_state(identity) == "completed"

    probe = asyncio.run(client.patient_identity_probe(correlation))
    assert probe.presence is TargetPresence.FOUND
    assert probe.record.record_id == result.target.record_id


# --- §17: demographic update does not change identity -------------------------------------------


def test_demographic_update_does_not_change_identity_or_lose_the_link() -> None:
    uuid = _synthetic_uuid()
    correlation = SourceCorrelation(system="urn:daher-health:openmrs-patient-uuid", value=uuid)
    payload = _payload("BeforeUpdate", "Synthetic")

    created = asyncio.run(_client().create_openelis_patient(correlation, payload))

    update_payload = {
        "resourceType": "Patient",
        "id": created.record_id,
        "name": [{"family": "AfterUpdate", "given": ["Renamed"]}],
        "gender": "male",
        "birthDate": "1990-05-05",
    }
    update_url = f"{BASE_URL}/Patient/{created.record_id}"
    update_result = asyncio.run(_real_put(update_url, update_payload))
    assert update_result.status == 200, "native update must succeed for a meaningful proof"

    probe = asyncio.run(_client().patient_identity_probe(correlation))
    assert probe.presence is TargetPresence.FOUND
    assert probe.record.record_id == created.record_id, "identity must survive a demographic change"
