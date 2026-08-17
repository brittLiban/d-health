"""The poll cycle: paging, restart mid-window, and — the point of all of it — the watermark
never advancing past work whose outcome is unknown.

The delivery callable is injected everywhere here. There is no production implementation, because
BLOCKER-6 has not decided how an order reaches OpenELIS (DH-011E is built, not activated). What
these tests prove is the cycle's safety, which has to be right before any target is chosen.

Async tests use `asyncio.run` inside sync functions, matching `test_recovery.py`, so the suite
needs no extra plugin.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.instants import parse_instant
from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openmrs import OpenMrsOrderSource, OpenMrsProtocolError
from daher_adapter.order_poller import OrderPoller, PollOutcome
from daher_adapter.recovery import (
    RecoveryCoordinator,
    SourceCorrelation,
    TargetPresence,
    TargetProbeResult,
    TargetRecord,
)
from daher_adapter.source_events import CohortIncomplete
from daher_adapter.watermark import WatermarkRepository

T0 = "2026-08-11T10:24:00.000+03:00"
T1 = "2026-08-11T10:24:58.000+03:00"
T2 = "2026-08-11T10:24:59.000+03:00"
NOW = "2026-08-11T11:00:00.000+03:00"

STREAM = {"resource_type": "ServiceRequest", "direction": "openmrs_to_openelis"}


def identity_for(resource_id: str, version: str = "1") -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id=resource_id,
        resource_version=version,
    )


def sr(rid: str, updated: str, version: str = "1", status: str = "active") -> dict:
    return {
        "resourceType": "ServiceRequest",
        "id": rid,
        "meta": {"versionId": version, "lastUpdated": updated},
        "status": status,
    }


def bundle(resources, total, *, next_url=None, now=NOW) -> dict:
    body: dict = {
        "resourceType": "Bundle",
        "type": "searchset",
        "meta": {"lastUpdated": now},
        "total": total,
        "entry": [{"resource": r} for r in resources],
        "link": [{"relation": "self", "url": "http://x/self"}],
    }
    if next_url:
        body["link"].append({"relation": "next", "url": next_url})
    return body


class FakeServer:
    """Serves a scripted page sequence and records every URL requested."""

    def __init__(self, pages, *, now: str = NOW) -> None:
        self._pages = list(pages)
        self._now = now
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        if "_summary=count" in url:
            return bundle([], 0, now=self._now)
        if not self._pages:
            return bundle([], 0, now=self._now)
        return self._pages.pop(0)


class Deliveries:
    """Stands in for the undecided target leg (BLOCKER-6)."""

    def __init__(self, fail_on=()) -> None:
        self.delivered: list[str] = []
        self._fail_on = set(fail_on)

    async def __call__(self, event) -> TargetRecord:
        if event.resource_id in self._fail_on:
            raise RuntimeError("target refused the write")
        self.delivered.append(event.resource_id)
        return TargetRecord(system="test-target", record_id=f"tgt-{event.resource_id}")


class HeldFence:
    held = True


async def ambiguous(_correlation: SourceCorrelation) -> TargetProbeResult:
    return TargetProbeResult(presence=TargetPresence.AMBIGUOUS)


async def never_written(_correlation: SourceCorrelation) -> TargetRecord:  # pragma: no cover
    raise AssertionError("write_target must not be called on this path")


def build(sessions, server, deliver, *, probe=None, write=None, bootstrap=T0):
    ledger = LedgerRepository(sessions)
    watermarks = WatermarkRepository(sessions)
    watermarks.get_or_bootstrap(**STREAM, server_now=parse_instant(bootstrap))
    poller = OrderPoller(
        source=OpenMrsOrderSource("http://openmrs/fhir", server),
        ledger=ledger,
        watermarks=watermarks,
        deliver=deliver,
        recovery=RecoveryCoordinator(ledger),
        fence=HeldFence(),
        probe_target=probe or ambiguous,
        write_target=write or never_written,
    )
    return poller, ledger, watermarks


def run(poller) -> PollOutcome:
    return asyncio.run(poller.poll_once())


@pytest.fixture
def db(sessions: sessionmaker[Session]):
    return sessions


class TestTheHappyCycle:
    def test_a_clean_cohort_is_delivered_in_order_and_advances(self, db):
        server = FakeServer([bundle([sr("b", T2), sr("a", T1)], 2)])
        deliver = Deliveries()
        poller, _, watermarks = build(db, server, deliver)

        outcome = run(poller)

        assert deliver.delivered == ["a", "b"], "delivery follows OUR ordering, not the server's"
        assert outcome.delivered == 2
        assert outcome.advanced_to.raw == NOW
        assert watermarks.current(**STREAM).raw == NOW

    def test_the_window_is_bounded_at_both_ends(self, db):
        """Verified against the real server before being relied on: an impossible ge/le range
        returns 0, which proves both bounds are applied rather than one being ignored."""
        server = FakeServer([bundle([], 0)])
        poller, _, _ = build(db, server, Deliveries())
        run(poller)
        window_url = next(u for u in server.urls if "_summary=count" not in u)
        assert "_lastUpdated=ge" in window_url
        assert "_lastUpdated=le" in window_url

    def test_an_empty_window_advances_to_the_frozen_upper_bound(self, db):
        server = FakeServer([bundle([], 0)])
        poller, _, watermarks = build(db, server, Deliveries())
        outcome = run(poller)
        assert outcome.delivered == 0
        assert watermarks.current(**STREAM).raw == NOW

    def test_a_cancellation_is_delivered_not_filtered(self, db):
        """§6: a discontinuation arrives as a new resource with `status: unknown`. A rule like
        'only handle active orders' would drop every cancellation on the floor."""
        cancel = sr("cancel", T1, status="unknown")
        cancel["replaces"] = [{"reference": "ServiceRequest/original"}]
        server = FakeServer([bundle([cancel], 1)])
        deliver = Deliveries()
        poller, _, _ = build(db, server, deliver)
        run(poller)
        assert deliver.delivered == ["cancel"]


class TestPagination:
    def test_every_page_is_followed(self, db):
        server = FakeServer(
            [
                bundle([sr("a", T1)], 2, next_url="http://openmrs/fhir/page2"),
                bundle([sr("b", T2)], 2),
            ]
        )
        deliver = Deliveries()
        poller, _, _ = build(db, server, deliver)
        outcome = run(poller)
        assert deliver.delivered == ["a", "b"]
        assert outcome.reported_total == 2

    def test_a_truncated_page_set_refuses_to_advance(self, db):
        """`total` says 5, one arrived, and this server does not order results — a record could
        be missing with no trace anywhere. Refuse rather than advance."""
        server = FakeServer([bundle([sr("a", T1)], 5)])
        poller, _, watermarks = build(db, server, Deliveries())
        with pytest.raises(CohortIncomplete):
            run(poller)
        assert watermarks.current(**STREAM).raw == T0, "the watermark must not have moved"


class TestDuplicatesAndRestart:
    def test_a_second_cycle_over_the_same_window_delivers_nothing_twice(self, db):
        """The inclusive lower bound re-presents the boundary second every cycle, by design."""
        deliver = Deliveries()
        poller, ledger, watermarks = build(db, FakeServer([bundle([sr("a", T1)], 1)]), deliver)
        run(poller)

        replay = OrderPoller(
            source=OpenMrsOrderSource(
                "http://openmrs/fhir", FakeServer([bundle([sr("a", T1)], 1)])
            ),
            ledger=ledger,
            watermarks=watermarks,
            deliver=deliver,
            recovery=RecoveryCoordinator(ledger),
            fence=HeldFence(),
            probe_target=ambiguous,
            write_target=never_written,
        )
        outcome = run(replay)

        assert deliver.delivered == ["a"], "exactly one delivery across two cycles"
        assert outcome.already_completed == 1

    def test_restart_mid_window_resumes_from_the_persisted_position(self, db):
        poller, _, watermarks = build(db, FakeServer([bundle([sr("a", T1)], 1)]), Deliveries())
        run(poller)
        persisted = watermarks.current(**STREAM).raw
        # A brand-new repository instance stands in for a process restart.
        assert WatermarkRepository(db).current(**STREAM).raw == persisted
        assert persisted == NOW


class TestTheWatermarkNeverPassesAnUnknownOutcome:
    def test_a_stale_claim_with_an_ambiguous_target_halts_the_cycle(self, db):
        """DH-025's core rule. 'Written but not recorded' and 'never written' both restart as
        `processing`; guessing either way risks a duplicate order or a lost one."""
        ledger = LedgerRepository(db)
        ledger.claim(identity_for("stale"))  # left `processing`, as a crashed cycle would

        server = FakeServer([bundle([sr("stale", T1), sr("later", T2)], 2)])
        deliver = Deliveries()
        poller, _, watermarks = build(db, server, deliver)

        outcome = run(poller)

        assert outcome.halted_reason == "AmbiguousTargetState"
        assert outcome.halted_at_resource == "stale"
        assert deliver.delivered == [], "nothing past the unknown may be delivered"
        assert watermarks.current(**STREAM).raw == T0, "and the watermark stays put"

    def test_a_dead_lettered_resource_halts_the_cycle(self, db):
        ledger = LedgerRepository(db)
        identity = identity_for("poison")
        ledger.claim(identity)
        ledger.move_to_dead_letter(
            identity, payload={"id": "poison"}, error=RuntimeError("boom"), attempts=3
        )

        server = FakeServer([bundle([sr("poison", T1), sr("good", T2)], 2)])
        deliver = Deliveries()
        poller, _, watermarks = build(db, server, deliver)
        outcome = run(poller)

        assert outcome.halted_reason == "DeadLetterRecoveryBlocked"
        assert deliver.delivered == []
        assert watermarks.current(**STREAM).raw == T0

    def test_a_delivery_failure_dead_letters_and_stops_before_the_rest(self, db):
        server = FakeServer([bundle([sr("ok", T1), sr("bad", T2)], 2)])
        deliver = Deliveries(fail_on={"bad"})
        poller, ledger, watermarks = build(db, server, deliver)

        outcome = run(poller)

        assert deliver.delivered == ["ok"]
        assert outcome.dead_lettered == ("bad",)
        assert ledger.get_state(identity_for("bad")) == "dead_letter"
        # Partial progress is kept, but only to the last contiguous success.
        assert outcome.advanced_to.raw == T1
        assert watermarks.current(**STREAM).raw == T1

    def test_a_failure_on_the_first_event_advances_nothing(self, db):
        server = FakeServer([bundle([sr("bad", T1), sr("later", T2)], 2)])
        poller, _, watermarks = build(db, server, Deliveries(fail_on={"bad"}))
        run(poller)
        assert watermarks.current(**STREAM).raw == T0

    def test_proven_absence_lets_recovery_retry_exactly_once(self, db):
        """The only branch DH-025 permits a retry on: the target proved zero matches."""
        ledger = LedgerRepository(db)
        ledger.claim(identity_for("stale"))

        async def absent(_correlation):
            return TargetProbeResult(presence=TargetPresence.ABSENT)

        written: list[str] = []

        async def write(correlation):
            written.append(correlation.value)
            return TargetRecord(system="test-target", record_id="recovered")

        server = FakeServer([bundle([sr("stale", T1)], 1)])
        poller, _, watermarks = build(db, server, Deliveries(), probe=absent, write=write)
        outcome = run(poller)

        assert outcome.reconciled == 1
        assert len(written) == 1, "exactly one retry, never a blind replay"
        assert watermarks.current(**STREAM).raw == NOW

    def test_an_existing_target_write_completes_without_writing_again(self, db):
        ledger = LedgerRepository(db)
        ledger.claim(identity_for("stale"))

        async def found(_correlation):
            return TargetProbeResult(
                presence=TargetPresence.FOUND,
                record=TargetRecord(system="test-target", record_id="already-there"),
            )

        server = FakeServer([bundle([sr("stale", T1)], 1)])
        poller, _, _ = build(db, server, Deliveries(), probe=found, write=never_written)
        outcome = run(poller)

        assert outcome.reconciled == 1
        assert ledger.get_state(identity_for("stale")) == "completed"


class TestMalformedServerResponses:
    def test_an_operation_outcome_is_not_read_as_an_empty_window(self, db):
        """'No results' and 'the query was rejected' are opposite facts. Conflating them would
        advance the watermark over a window that was never actually searched."""

        def rejecting(url):
            if "_summary=count" in url:
                return bundle([], 0)
            return {"resourceType": "OperationOutcome", "issue": [{"severity": "error"}]}

        poller, _, watermarks = build(db, rejecting, Deliveries())
        with pytest.raises(OpenMrsProtocolError):
            run(poller)
        assert watermarks.current(**STREAM).raw == T0

    def test_a_bundle_without_a_total_is_refused(self, db):
        def no_total(url):
            if "_summary=count" in url:
                return bundle([], 0)
            body = bundle([sr("a", T1)], 0)
            del body["total"]
            return body

        poller, _, watermarks = build(db, no_total, Deliveries())
        with pytest.raises(OpenMrsProtocolError):
            run(poller)
        assert watermarks.current(**STREAM).raw == T0

    def test_a_malformed_server_clock_is_refused_before_anything_is_fetched(self, db):
        def bad_clock(url):
            return bundle([], 0, now="not-a-timestamp")

        poller, _, watermarks = build(db, bad_clock, Deliveries())
        with pytest.raises(OpenMrsProtocolError):
            run(poller)
        assert watermarks.current(**STREAM).raw == T0
