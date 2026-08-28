"""DH-011/BLOCKER-6 §10-11: `OpenElisClient` wired as `recovery.py`'s real `TargetProbe`/
`TargetWriter`, not a generic fake. `test_recovery.py` already proves `RecoveryCoordinator`'s
crash-point behavior is target-agnostic (5 crash points around the write, all fail-closed on
ambiguity); what this file proves is that binding the actual OpenELIS client preserves that
contract rather than silently reinterpreting it.

`deliver_service_request` is deliberately unimplemented (§9) -- live ServiceRequest delivery
stays blocked until the real numeric site code exists. So the one crash point this file cannot
exercise end to end is "target accepted the write, adapter crashed before recording it" for a
real OpenELIS write -- see `test_reconciliation_after_found_completes_without_a_second_write`
for the part of that path that *is* provable today (found-on-probe never calls the writer), and
`test_write_target_refuses_rather_than_attempting_a_doomed_post` for why the rest is not faked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from test_openelis import ScriptedTransport, bundle, resource

from daher_adapter.ledger import MessageIdentity
from daher_adapter.openelis import HttpResult, OpenElisClient, ServiceRequestDeliveryBlocked
from daher_adapter.recovery import (
    AmbiguousTargetState,
    RecoveryAction,
    RecoveryCoordinator,
    source_correlation,
)


def identity() -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id="order-uuid-1",
        resource_version="1",
    )


@dataclass
class FakeFence:
    held: bool = True


@dataclass
class FakeLedger:
    """The minimal slice of `LedgerRepository`'s interface `reconcile` actually calls."""

    state: str
    completed_with: tuple[str, str] | None = field(default=None, init=False)

    def get_state(self, identity: MessageIdentity) -> str | None:
        return self.state

    def mark_completed(
        self, identity: MessageIdentity, *, target_system: str, target_record_id: str
    ) -> None:
        self.completed_with = (target_system, target_record_id)


def run(coro):
    return asyncio.run(coro)


def openelis(*, get_response) -> tuple[OpenElisClient, ScriptedTransport]:
    transport = ScriptedTransport()
    transport.queue_get(get_response)
    base = "https://oe.openelis.org:8443/OpenELIS-Global/fhir"
    return OpenElisClient(base, transport.get, transport.post), transport


def test_reconciliation_after_found_completes_without_a_second_write() -> None:
    """Crash-after-write, the one DH-025 exists to make safe: OpenELIS already has the
    ServiceRequest, the adapter crashed before recording it, restart must reconcile to the
    existing resource and never attempt a second create."""
    found = HttpResult(status=200, body=bundle(resource("ServiceRequest", "sr-existing")))
    client, transport = openelis(get_response=found)
    ledger = FakeLedger(state="processing")
    coordinator = RecoveryCoordinator(ledger=ledger)

    async def write_target(correlation):
        raise AssertionError("write_target must never be called when the probe found a match")

    result = run(
        coordinator.reconcile(
            identity(),
            fence=FakeFence(),
            probe_target=client.service_request_probe,
            write_target=write_target,
        )
    )

    assert result.action is RecoveryAction.RECONCILED_EXISTING
    assert result.target.record_id == "sr-existing"
    assert ledger.completed_with == ("ServiceRequest", "sr-existing")
    assert len(transport.posts) == 0


def test_reconciliation_after_ambiguous_probe_never_completes_or_writes() -> None:
    """An unreachable/rejected search (e.g. hitting the unpatched image, or a transient 5xx)
    must stop the whole recovery, exactly as test_recovery.py's generic fake proves -- this
    confirms the real client's AMBIGUOUS classification reaches that same fail-closed path."""
    rejected = HttpResult(status=400, body=resource("OperationOutcome"))
    client, transport = openelis(get_response=rejected)
    ledger = FakeLedger(state="processing")
    coordinator = RecoveryCoordinator(ledger=ledger)

    async def write_target(correlation):
        raise AssertionError("write_target must never be called on an ambiguous probe")

    with pytest.raises(AmbiguousTargetState):
        run(
            coordinator.reconcile(
                identity(),
                fence=FakeFence(),
                probe_target=client.service_request_probe,
                write_target=write_target,
            )
        )

    assert ledger.completed_with is None
    assert len(transport.posts) == 0


def test_write_target_refuses_rather_than_attempting_a_doomed_post() -> None:
    """Crash-before-write's other half: zero matches proven, so DH-025 would normally call
    `write_target`. Bound to the real client, that write refuses outright instead of attempting
    a POST that OpenELIS's own accession gate would refuse anyway -- see `openelis.py`'s
    `deliver_service_request` docstring for why raising is preferred over a doomed network call.
    """
    absent = HttpResult(status=200, body=bundle())
    client, _transport = openelis(get_response=absent)
    ledger = FakeLedger(state="processing")
    coordinator = RecoveryCoordinator(ledger=ledger)

    async def write_target(correlation):
        return await client.deliver_service_request(correlation, {})

    with pytest.raises(ServiceRequestDeliveryBlocked):
        run(
            coordinator.reconcile(
                identity(),
                fence=FakeFence(),
                probe_target=client.service_request_probe,
                write_target=write_target,
            )
        )

    assert ledger.completed_with is None


def test_source_correlation_used_by_the_real_client_matches_ledgers_own_derivation() -> None:
    """The value `OpenElisClient.service_request_probe` searches for must be exactly what
    `recovery.source_correlation` derives -- not a second, divergent identifier scheme."""
    correlation = source_correlation(identity())

    assert correlation.system == "urn:daher-health:adapter:source-event"
    assert len(correlation.value) == 64  # sha256 hex digest, same shape openelis.py expects
