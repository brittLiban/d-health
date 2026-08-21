"""Job queue routing and the agent poll loop (ADR-022).

The poll-loop tests are mostly about the agent **staying alive**. An agent that crashes on
hostile input has been switched off by the attacker, and a host that is not polling is also
not getting backups or updates — so "refused and kept going" is the property under test,
not merely "refused".
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest

from daher_agent.poller import AgentPoller, ExecutionResult
from daher_agent.validation import JobValidator
from daher_provisioning.jobs import Job, JobParams, JobVerb
from daher_provisioning.queue import JobQueue, JobStatus, QueueError


def a_job(job_id: str = "job-00000001", host_id: str = "host-a", **overrides) -> Job:
    payload = {
        "job_id": job_id,
        "host_id": host_id,
        "verb": JobVerb.PROVISION,
        "params": JobParams(facility_id="daher-bc"),
        "issued_at": datetime.now(UTC),
    }
    payload.update(overrides)
    return Job(**payload)


def a_validator(
    existing: tuple[str, ...] = (), *, max_age_seconds: float | None = None
) -> JobValidator:
    kwargs = {} if max_age_seconds is None else {"max_age_seconds": max_age_seconds}
    return JobValidator(host_id="host-a", facility_exists=lambda fid: fid in existing, **kwargs)


class TestQueueRouting:
    def test_a_job_goes_only_to_its_addressed_host(self):
        queue = JobQueue()
        queue.enqueue(a_job(host_id="host-a"))
        assert queue.claim("host-b") is None
        assert queue.claim("host-a").job_id == "job-00000001"

    def test_claiming_twice_yields_nothing_the_second_time(self):
        queue = JobQueue()
        queue.enqueue(a_job())
        queue.claim("host-a")
        assert queue.claim("host-a") is None

    def test_jobs_are_handed_out_in_order(self):
        queue = JobQueue()
        queue.enqueue(a_job("job-00000001"))
        queue.enqueue(a_job("job-00000002"))
        assert queue.claim("host-a").job_id == "job-00000001"
        assert queue.claim("host-a").job_id == "job-00000002"

    def test_duplicate_job_ids_are_refused(self):
        queue = JobQueue()
        queue.enqueue(a_job())
        with pytest.raises(QueueError, match="already queued"):
            queue.enqueue(a_job())

    def test_a_claimed_job_is_held_not_deleted(self):
        """A crash between claiming and finishing must leave the job visible."""
        queue = JobQueue()
        queue.enqueue(a_job())
        queue.claim("host-a")
        assert queue.get("job-00000001").status is JobStatus.CLAIMED

    def test_refused_is_distinct_from_failed(self):
        """The boundary working must never read as the deployment breaking."""
        queue = JobQueue()
        queue.enqueue(a_job())
        queue.claim("host-a")
        record = queue.finish("job-00000001", JobStatus.REFUSED, "hostile facility_id")
        assert record.status is JobStatus.REFUSED

    def test_a_job_cannot_be_finished_back_into_queued(self):
        queue = JobQueue()
        queue.enqueue(a_job())
        with pytest.raises(QueueError):
            queue.finish("job-00000001", JobStatus.QUEUED)

    def test_finishing_an_unknown_job_is_an_error(self):
        with pytest.raises(QueueError, match="unknown job"):
            JobQueue().finish("nope", JobStatus.SUCCEEDED)

    def test_pending_count_reflects_unclaimed_work(self):
        queue = JobQueue()
        queue.enqueue(a_job("job-00000001"))
        queue.enqueue(a_job("job-00000002"))
        assert queue.pending_count("host-a") == 2
        queue.claim("host-a")
        assert queue.pending_count("host-a") == 1


class TestPollerRunsLegitimateWork:
    """`poll_once` dispatches to a background thread and returns immediately (bug 18) — the
    outcome is only observable after `poller.join()`, so every test here joins before it
    asserts on `executed`/`reported`.
    """

    def test_a_valid_job_is_executed_and_reported(self):
        executed, reported = [], []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=lambda job: (executed.append(job.job_id), ExecutionResult(True, "ok"))[1],
            report=lambda job_id, status, detail: reported.append((job_id, status)),
        )
        assert poller.poll_once() == "dispatched"
        assert poller.join(timeout=2)
        assert executed == ["job-00000001"]
        assert reported == [("job-00000001", "succeeded")]

    def test_an_idle_queue_does_nothing(self):
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: None,
            execute=lambda job: pytest.fail("must not execute when idle"),
        )
        assert poller.poll_once() is None

    def test_a_failing_execution_is_reported_as_failed(self):
        reported = []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=lambda job: ExecutionResult(False, "compose exited 1"),
            report=lambda job_id, status, detail: reported.append(status),
        )
        assert poller.poll_once() == "dispatched"
        assert poller.join(timeout=2)
        assert reported == ["failed"]


class TestThePollerStaysAlive:
    """An agent that crashes on hostile input has been switched off by the attacker."""

    def test_a_hostile_job_is_refused_without_executing(self):
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: {**a_job().model_dump(mode="json"), "host_id": "host-b"},
            execute=lambda job: pytest.fail("a refused job must never reach the executor"),
        )
        assert poller.poll_once() == "refused"

    def test_a_malformed_payload_does_not_raise(self):
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: {"nonsense": True},
            execute=lambda job: pytest.fail("must not execute"),
        )
        assert poller.poll_once() == "refused"

    def test_a_payload_smuggling_a_spec_is_refused(self):
        payload = a_job().model_dump(mode="json")
        payload["params"]["image"] = "attacker/backdoor:latest"
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: payload,
            execute=lambda job: pytest.fail("must not execute"),
        )
        assert poller.poll_once() == "refused"

    def test_an_unreachable_api_does_not_raise(self):
        def unreachable():
            raise ConnectionError("no route to host")

        poller = AgentPoller(
            validator=a_validator(), fetch=unreachable, execute=lambda job: ExecutionResult(True)
        )
        assert poller.poll_once() is None

    def test_an_executor_that_blows_up_is_survived(self):
        def explode(job):
            raise OSError("docker not found")

        reported = []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=explode,
            report=lambda job_id, status, detail: reported.append((status, detail)),
        )
        assert poller.poll_once() == "dispatched"
        assert poller.join(timeout=2)
        assert reported[0][0] == "failed"

    def test_a_failing_reporter_does_not_stop_the_loop(self):
        """The exception now happens on a background thread, where an uncaught exception
        would be silently swallowed by the interpreter rather than propagating anywhere —
        so `poll_once` returning without raising proves nothing on its own. What must be
        shown is that the reporter was actually *called*, and the thread finished cleanly."""
        calls = []

        def bad_report(job_id, status, detail):
            calls.append((job_id, status))
            raise ConnectionError("report failed")

        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=lambda job: ExecutionResult(True),
            report=bad_report,
        )
        assert poller.poll_once() == "dispatched"
        assert poller.join(timeout=2)
        assert calls == [("job-00000001", "succeeded")]

    def test_the_loop_survives_a_hostile_job_and_runs_the_next_one(self):
        """The property that matters: one bad job does not disable the host."""
        payloads = [
            {**a_job("job-00000001").model_dump(mode="json"), "host_id": "host-b"},
            a_job("job-00000002").model_dump(mode="json"),
        ]
        executed = []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: payloads.pop(0) if payloads else None,
            execute=lambda job: (executed.append(job.job_id), ExecutionResult(True))[1],
        )
        assert poller.poll_once() == "refused"
        assert poller.poll_once() == "dispatched"
        assert poller.join(timeout=2)
        assert executed == ["job-00000002"]

    def test_a_refusal_is_reported_as_refused_not_failed(self):
        reported = []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: {**a_job().model_dump(mode="json"), "host_id": "host-b"},
            execute=lambda job: ExecutionResult(True),
            report=lambda job_id, status, detail: reported.append(status),
        )
        poller.poll_once()
        assert reported == ["refused"]


class TestBug18ThePollLoopKeepsPollingDuringALongJob:
    """The agent used to run `execute` inline, so while a `provision` was up to 5400s into a
    single blocking call, the loop never called `fetch` again — anything issued in that
    window was validated only once the loop resumed, by which point `JobValidator` (rightly)
    refuses it as older than the 900s replay window. Fixed by running execution on a
    background thread; these tests prove both the mechanism (the loop is not blocked) and
    the actual symptom (a job issued mid-run does not go stale).
    """

    @staticmethod
    def _slow_provision(
        started: threading.Event, release: threading.Event, finished: threading.Event | None = None
    ):
        def execute(job: Job) -> ExecutionResult:
            if job.verb is JobVerb.PROVISION:
                started.set()
                release.wait(timeout=5)
                if finished is not None:
                    finished.set()
                return ExecutionResult(True, "provisioned")
            return ExecutionResult(True, f"{job.verb.value} ok")

        return execute

    def test_a_second_job_is_fetched_while_the_first_is_still_running(self):
        """The mechanism check: `poll_once` for job A must return before A finishes, and a
        second `poll_once` call must be able to fetch and dispatch job B while A is still
        executing — not wait for A."""
        started = threading.Event()
        finished = threading.Event()
        release = threading.Event()
        payloads = iter(
            [
                a_job("job-00000001", verb=JobVerb.PROVISION).model_dump(mode="json"),
                a_job("job-00000002", verb=JobVerb.STATUS).model_dump(mode="json"),
            ]
        )
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: next(payloads, None),
            execute=self._slow_provision(started, release, finished),
        )

        assert poller.poll_once() == "dispatched"
        assert started.wait(timeout=1), "job A must have started executing"

        outcome = poller.poll_once()  # job B: fetched and dispatched while A is still blocked
        # A must genuinely still be running here — otherwise this would pass just as well on
        # the old, blocking poller, where poll_once(A) would not have returned until A's
        # execute() finished, making this assertion trivially true for the wrong reason.
        assert not finished.is_set(), "job A must still be executing when B is dispatched"
        release.set()
        assert poller.join(timeout=5)
        assert outcome == "dispatched"

    def test_a_job_issued_while_a_long_job_runs_is_not_refused_as_stale(self):
        """The regression this ticket is actually about, on real time, kept fast by shrinking
        `max_age_seconds` proportionally instead of waiting out the production 900s window.

        The mechanism is the same either way: `JobValidator.validate` reads the wall clock at
        the moment it runs. Under the old, blocking loop, job B's fetch+validate could only
        happen *after* job A's `execute()` returned — so however long A blocked for is exactly
        how much older than its `issued_at` job B was by the time anyone looked. Here A blocks
        for real time well past a (deliberately tiny) staleness window; if the loop were still
        blocked on A, B would be validated after that block and refused as stale. The fix means
        B is validated immediately, while A is still running, so it never gets old enough to
        matter — proving the actual bug (staleness), not just that `poll_once` returns fast.
        """
        # Generous on purpose: a shared CI runner can stall for hundreds of milliseconds, and
        # the happy path never waits this long (release.set() follows immediately below) — the
        # size only matters for how decisively Control C's inline-execution break clears it
        # (its `release.wait(timeout=5)` clears 2s just as surely as 0.2s).
        max_age_seconds = 2.0
        validator = a_validator(max_age_seconds=max_age_seconds)

        started = threading.Event()
        release = threading.Event()

        def slow_provision(job: Job) -> ExecutionResult:
            if job.verb is JobVerb.PROVISION:
                started.set()
                # Long enough, relative to `max_age_seconds`, to stand in for a real 5400s
                # provision blocking the old loop past the (proportionally scaled) 900s window.
                release.wait(timeout=5)
                return ExecutionResult(True, "provisioned")
            return ExecutionResult(True, "status ok")

        now = datetime.now(UTC)
        payloads = iter(
            [
                a_job("job-00000001", verb=JobVerb.PROVISION, issued_at=now).model_dump(
                    mode="json"
                ),
                a_job("job-00000002", verb=JobVerb.STATUS, issued_at=now).model_dump(mode="json"),
            ]
        )
        reported = []
        poller = AgentPoller(
            validator=validator,
            fetch=lambda: next(payloads, None),
            execute=slow_provision,
            report=lambda job_id, status, detail, infrastructure=None: reported.append(
                (job_id, status, detail)
            ),
        )

        assert poller.poll_once() == "dispatched"
        assert started.wait(timeout=1)

        # Job B is fetched and validated here, while A is still blocked inside `execute` — if
        # the loop were still synchronous, this line would not run until A's real block ended.
        outcome = poller.poll_once()
        release.set()
        assert poller.join(timeout=5)

        assert outcome == "dispatched", (
            "job B must be validated while A is still executing, well within the "
            f"{max_age_seconds}s window — under the old code this call is only reached after "
            "A's execute() returns, by which point ReplayRefused would fire and this would be "
            "'refused', not 'dispatched'"
        )
        assert ("job-00000002", "succeeded", "status ok") in reported
        assert not any("older than" in detail for _, _, detail in reported), (
            "job B must never be reported as a stale replay"
        )


class TestBug18AtMostOneMutatingJobRuns:
    """Bug 18's fix must not let two `docker compose` invocations race against the same
    project. STATUS is read-only and exempt; every other verb is not.
    """

    @staticmethod
    def _blocking_execute(started: threading.Event, release: threading.Event):
        def execute(job: Job) -> ExecutionResult:
            started.set()
            release.wait(timeout=5)
            return ExecutionResult(True, f"{job.verb.value} ok")

        return execute

    def test_a_second_mutating_job_is_refused_while_one_is_in_flight(self):
        started = threading.Event()
        release = threading.Event()
        payloads = iter(
            [
                a_job("job-00000001", verb=JobVerb.PROVISION).model_dump(mode="json"),
                a_job("job-00000002", verb=JobVerb.STOP).model_dump(mode="json"),
            ]
        )
        reported = []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: next(payloads, None),
            execute=self._blocking_execute(started, release),
            report=lambda job_id, status, detail, infrastructure=None: reported.append(
                (job_id, status, detail)
            ),
        )

        assert poller.poll_once() == "dispatched"
        assert started.wait(timeout=1)

        assert poller.poll_once() == "busy"
        release.set()
        assert poller.join(timeout=5)

        job_b_report = next(r for r in reported if r[0] == "job-00000002")
        assert job_b_report[1] == "refused"
        assert "still executing" in job_b_report[2]

    def test_status_is_never_blocked_by_an_in_flight_mutating_job(self):
        """STATUS must not merely avoid a busy refusal — it must actually finish. A weaker
        version of this test that only checked "not busy" would still pass if STATUS were
        silently queued behind the mutating job rather than run concurrently with it."""
        started = threading.Event()
        finished = threading.Event()  # set when the mutating job (A) completes
        release = threading.Event()

        def execute(job: Job) -> ExecutionResult:
            if job.verb is JobVerb.PROVISION:
                started.set()
                release.wait(timeout=5)
                finished.set()
                return ExecutionResult(True, "provisioned")
            return ExecutionResult(True, "status ok")

        payloads = iter(
            [
                a_job("job-00000001", verb=JobVerb.PROVISION).model_dump(mode="json"),
                a_job("job-00000002", verb=JobVerb.STATUS).model_dump(mode="json"),
            ]
        )
        reported = []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: next(payloads, None),
            execute=execute,
            report=lambda job_id, status, detail, infrastructure=None: reported.append(
                (job_id, status)
            ),
        )

        assert poller.poll_once() == "dispatched"
        assert started.wait(timeout=1)
        assert poller.poll_once() == "dispatched"  # STATUS: not refused as busy

        deadline = time.monotonic() + 5  # generous: a shared CI runner can stall briefly
        while ("job-00000002", "succeeded") not in reported and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ("job-00000002", "succeeded") in reported, "STATUS must actually complete"
        assert not finished.is_set(), "STATUS finished while the mutating job was still running"

        release.set()
        assert poller.join(timeout=5)

    def test_a_second_status_job_is_refused_while_one_is_in_flight(self):
        """Bounds the thread count: a flood of queued STATUS jobs cannot spawn unboundedly
        many threads."""
        started = threading.Event()
        release = threading.Event()
        payloads = iter(
            [
                a_job("job-00000001", verb=JobVerb.STATUS).model_dump(mode="json"),
                a_job("job-00000002", verb=JobVerb.STATUS).model_dump(mode="json"),
            ]
        )
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: next(payloads, None),
            execute=self._blocking_execute(started, release),
        )

        assert poller.poll_once() == "dispatched"
        assert started.wait(timeout=1)
        assert poller.poll_once() == "busy"
        release.set()
        assert poller.join(timeout=5)

    def test_the_mutating_slot_frees_once_the_job_finishes(self):
        started = threading.Event()
        release = threading.Event()
        payloads = iter(
            [
                a_job("job-00000001", verb=JobVerb.PROVISION).model_dump(mode="json"),
                a_job("job-00000002", verb=JobVerb.STOP).model_dump(mode="json"),
            ]
        )
        executed = []

        def execute(job: Job) -> ExecutionResult:
            executed.append(job.job_id)
            if job.job_id == "job-00000001":
                started.set()
                release.wait(timeout=5)
            return ExecutionResult(True, f"{job.verb.value} ok")

        poller = AgentPoller(
            validator=a_validator(), fetch=lambda: next(payloads, None), execute=execute
        )

        assert poller.poll_once() == "dispatched"
        assert started.wait(timeout=1)
        release.set()
        assert poller.join(timeout=5)  # job A finishes, slot frees

        assert poller.poll_once() == "dispatched"  # job B: slot is free again
        assert poller.join(timeout=5)
        assert executed == ["job-00000001", "job-00000002"]

    def test_a_busy_refusal_is_reported_as_refused_not_failed(self):
        """Being busy is the boundary working, the same as any other local refusal — not
        evidence the blocked job failed (DH-027C's lifecycle table relies on this: see
        `test_decommission.py::test_a_refused_provision_report_leaves_it_provisioning`)."""
        started = threading.Event()
        release = threading.Event()
        payloads = iter(
            [
                a_job("job-00000001", verb=JobVerb.PROVISION).model_dump(mode="json"),
                a_job("job-00000002", verb=JobVerb.PROVISION).model_dump(mode="json"),
            ]
        )
        reported = []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: next(payloads, None),
            execute=self._blocking_execute(started, release),
            report=lambda job_id, status, detail, infrastructure=None: reported.append(
                (job_id, status)
            ),
        )

        assert poller.poll_once() == "dispatched"
        assert started.wait(timeout=1)
        assert poller.poll_once() == "busy"
        release.set()
        assert poller.join(timeout=5)

        assert ("job-00000002", "refused") in reported
        assert ("job-00000002", "failed") not in reported


class TestPollerJoin:
    def test_join_is_true_when_nothing_has_ever_run(self):
        poller = AgentPoller(validator=a_validator(), fetch=lambda: None, execute=lambda job: None)
        assert poller.join(timeout=1) is True

    def test_join_waits_for_dispatched_work_to_finish(self):
        release = threading.Event()
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=lambda job: (release.wait(timeout=5), ExecutionResult(True))[1],
        )
        assert poller.poll_once() == "dispatched"
        release.set()
        assert poller.join(timeout=5) is True

    def test_join_times_out_if_the_job_is_still_running(self):
        release = threading.Event()
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=lambda job: (release.wait(timeout=5), ExecutionResult(True))[1],
        )
        assert poller.poll_once() == "dispatched"
        assert poller.join(timeout=0.05) is False
        release.set()
        assert poller.join(timeout=5) is True
