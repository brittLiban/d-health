"""Job queue routing and the agent poll loop (ADR-022).

The poll-loop tests are mostly about the agent **staying alive**. An agent that crashes on
hostile input has been switched off by the attacker, and a host that is not polling is also
not getting backups or updates — so "refused and kept going" is the property under test,
not merely "refused".
"""

from __future__ import annotations

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


def a_validator(existing: tuple[str, ...] = ()) -> JobValidator:
    return JobValidator(host_id="host-a", facility_exists=lambda fid: fid in existing)


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
    def test_a_valid_job_is_executed_and_reported(self):
        executed, reported = [], []
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=lambda job: (executed.append(job.job_id), ExecutionResult(True, "ok"))[1],
            report=lambda job_id, status, detail: reported.append((job_id, status)),
        )
        assert poller.poll_once() == "succeeded"
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
        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=lambda job: ExecutionResult(False, "compose exited 1"),
        )
        assert poller.poll_once() == "failed"


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
        assert poller.poll_once() == "failed"
        assert reported[0][0] == "failed"

    def test_a_failing_reporter_does_not_stop_the_loop(self):
        def bad_report(job_id, status, detail):
            raise ConnectionError("report failed")

        poller = AgentPoller(
            validator=a_validator(),
            fetch=lambda: a_job().model_dump(mode="json"),
            execute=lambda job: ExecutionResult(True),
            report=bad_report,
        )
        assert poller.poll_once() == "succeeded"

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
        assert poller.poll_once() == "succeeded"
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
