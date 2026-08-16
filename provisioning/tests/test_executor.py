"""The compose executor (ADR-022).

This is the last seam where a parameter reaches a command boundary, so the tests assert the
**exact argv** rather than "it worked". A test that only checks the return value would pass
identically whether the command was a safe argv list or a shell string with the facility id
interpolated into it.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daher_agent.executor import CommandResult, ComposeExecutor
from daher_provisioning.jobs import Job, JobParams, JobVerb


def a_job(verb: JobVerb = JobVerb.START, facility_id: str = "daher-bc") -> Job:
    return Job(
        job_id="job-00000001",
        host_id="host-a",
        verb=verb,
        params=JobParams(facility_id=facility_id),
        issued_at=datetime.now(UTC),
    )


class Recorder:
    """Captures the argv instead of running it."""

    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self._returncode = returncode

    def __call__(self, argv, *, cwd, timeout) -> CommandResult:
        self.calls.append(argv)
        self.kwargs.append({"cwd": cwd, "timeout": timeout})
        return CommandResult(self._returncode, "ok", "")


@pytest.fixture
def wired(tmp_path):
    compose = tmp_path / "stack" / "docker-compose.yml"
    compose.parent.mkdir(parents=True)
    compose.write_text("services: {}\n", encoding="utf-8")
    recorder = Recorder()
    executor = ComposeExecutor(
        compose_file=compose,
        facility_root=tmp_path / "facilities",
        base_domain="example.org",
        runner=recorder,
    )
    return executor, recorder


class TestTheCommandIsNeverAShell:
    def test_argv_is_a_list_not_a_string(self, wired):
        executor, recorder = wired
        executor.execute(a_job())
        assert isinstance(recorder.calls[0], list), (
            "a string command would reintroduce shell interpretation"
        )

    def test_every_argument_is_a_separate_element(self, wired):
        executor, recorder = wired
        executor.execute(a_job())
        for element in recorder.calls[0]:
            assert " " not in element or element.startswith("/"), (
                f"argument {element!r} contains a space, suggesting concatenation"
            )

    def test_the_real_runner_uses_shell_false(self):
        """Read the source: the one subprocess call must pass shell=False explicitly."""
        source = Path("/app/src/daher_agent/executor.py")
        if not source.is_file():  # running outside the container
            source = Path(__file__).resolve().parents[1] / "src/daher_agent/executor.py"
        text = source.read_text(encoding="utf-8")
        assert "shell=False" in text
        assert "shell=True" not in text


class TestTheComposeFileIsFixed:
    def test_the_compose_path_comes_from_configuration(self, wired):
        executor, recorder = wired
        executor.execute(a_job())
        argv = recorder.calls[0]
        assert str(executor.compose_file) in argv

    def test_no_job_field_can_name_a_compose_file(self):
        """If this fails, an issuer can choose the container spec — i.e. root on the host."""
        for model in (Job, JobParams):
            for field in model.model_fields:
                assert "compose" not in field.lower()
                assert "file" not in field.lower()

    def test_a_missing_compose_file_is_a_clean_failure(self, tmp_path):
        executor = ComposeExecutor(
            compose_file=tmp_path / "absent.yml",
            facility_root=tmp_path / "f",
            base_domain="example.org",
            runner=Recorder(),
        )
        result = executor.execute(a_job())
        assert not result.succeeded
        assert "not present on host" in result.detail


class TestTheFacilityIdIsValidatedAgainHere:
    def test_a_hostile_id_never_reaches_argv(self, wired):
        """Third validation, at the point where being wrong actually costs something."""
        executor, recorder = wired
        hostile = JobParams.model_construct(
            facility_id="daher; rm -rf /", facility_type="hospital", base_domain="localhost"
        )
        job = Job.model_construct(
            job_id="job-00000001",
            host_id="host-a",
            verb=JobVerb.START,
            params=hostile,
            issued_at=datetime.now(UTC),
        )
        result = executor.execute(job)
        assert not result.succeeded
        assert recorder.calls == [], "a hostile id must never reach the command"

    def test_the_project_name_is_derived_not_supplied(self, wired):
        executor, recorder = wired
        executor.execute(a_job(facility_id="daher-bc"))
        assert "daher-daher-bc" in recorder.calls[0]


class TestVerbMapping:
    def test_start_brings_the_stack_up(self, wired):
        executor, recorder = wired
        executor.execute(a_job(JobVerb.START))
        assert recorder.calls[0][-2:] == ["up", "--detach"]

    def test_stop_uses_stop_never_down(self, wired):
        """`down` removes containers and networks, and is one flag from removing volumes."""
        executor, recorder = wired
        executor.execute(a_job(JobVerb.STOP))
        argv = recorder.calls[0]
        assert argv[-1] == "stop"
        assert "down" not in argv
        assert "--volumes" not in argv and "-v" not in argv

    def test_no_verb_can_remove_volumes(self, wired):
        """Decommission is soft (ADR-021); nothing here may destroy clinical data."""
        executor, recorder = wired
        for verb in JobVerb:
            recorder.calls.clear()
            executor.execute(a_job(verb))
            for argv in recorder.calls:
                assert "--volumes" not in argv
                assert "-v" not in argv
                assert "rm" not in argv or "--rm" in argv  # `run --rm` is fine

    def test_status_asks_for_machine_readable_output(self, wired):
        executor, recorder = wired
        executor.execute(a_job(JobVerb.STATUS))
        assert recorder.calls[0][-3:] == ["ps", "--format", "json"]


class TestProvisioning:
    def test_provision_writes_an_env_file_and_starts(self, wired):
        executor, recorder = wired
        result = executor.execute(a_job(JobVerb.PROVISION))
        assert result.succeeded
        assert executor.env_path("daher-bc").is_file()
        assert recorder.calls[0][-2:] == ["up", "--detach"]

    def test_the_env_file_is_not_world_readable(self, wired):
        import stat

        executor, _ = wired
        executor.execute(a_job(JobVerb.PROVISION))
        mode = stat.S_IMODE(executor.env_path("daher-bc").stat().st_mode)
        assert mode == 0o600

    def test_secrets_are_generated_locally_not_carried_by_the_job(self, wired):
        """The job has no secret field; the API has never held one (ADR-022)."""
        executor, _ = wired
        executor.execute(a_job(JobVerb.PROVISION))
        contents = executor.env_path("daher-bc").read_text(encoding="utf-8")
        assert "OMRS_ADMIN_PASSWORD=" in contents
        for field in JobParams.model_fields:
            assert "password" not in field.lower()
            assert "secret" not in field.lower()

    def test_provisioning_twice_refuses_rather_than_overwriting(self, wired):
        """Same guarantee as the agent's check, one layer lower where the file actually is."""
        executor, _ = wired
        assert executor.execute(a_job(JobVerb.PROVISION)).succeeded
        second = executor.execute(a_job(JobVerb.PROVISION))
        assert not second.succeeded
        assert "not how rotation works" in second.detail


class TestFailuresAreReturnedNotRaised:
    def test_a_nonzero_exit_is_reported(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "f",
            base_domain="example.org",
            runner=Recorder(returncode=1),
        )
        result = executor.execute(a_job())
        assert not result.succeeded
        assert "exit 1" in result.detail

    def test_a_timeout_does_not_stop_the_agent(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")

        def hang(argv, *, cwd, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "f",
            base_domain="example.org",
            runner=hang,
        )
        result = executor.execute(a_job())
        assert not result.succeeded
        assert "timed out" in result.detail

    def test_a_timeout_is_always_set(self, wired):
        executor, recorder = wired
        executor.execute(a_job())
        assert recorder.kwargs[0]["timeout"] > 0
