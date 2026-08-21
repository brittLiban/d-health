"""The compose executor (ADR-022).

This is the last seam where a parameter reaches a command boundary, so the tests assert the
**exact argv** rather than "it worked". A test that only checks the return value would pass
identically whether the command was a safe argv list or a shell string with the facility id
interpolated into it.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daher_agent.executor import (
    DEFAULT_BACKUP_TIMEOUT_SECONDS,
    DEFAULT_STACK_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    CommandResult,
    ComposeExecutor,
)
from daher_provisioning.jobs import Job, JobParams, JobVerb

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# Measured at DH-002: deployment start 06:36:49Z → first HTTP 200 at 07:26:08Z on 4 vCPU,
# almost entirely OCL concept imports. Recorded here because it is the number the stack
# timeout has to clear, and a constant nobody can trace back to a measurement rots.
MEASURED_COLD_BOOT_SECONDS = 49 * 60


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
        """DH-027D: STATUS now runs TWO read-only commands, not one.

        `ps` can only show containers that exist, so a service the compose file declares and
        which is entirely absent is invisible without `config`. `config` also respects active
        profiles, so an unconfigured backup service is not counted as missing.
        """
        executor, recorder = wired
        executor.execute(a_job(JobVerb.STATUS))
        issued = [" ".join(call) for call in recorder.calls]
        assert any(c.endswith("config --format json") for c in issued), issued
        assert any(c.endswith("ps --all --format json") for c in issued), issued
        # Both are read-only. Neither may mutate the stack while merely observing it.
        for call in recorder.calls:
            assert "up" not in call and "stop" not in call and "down" not in call


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


class TestTheBoundMatchesTheWorkTheVerbActuallyDoes:
    """Known bug 17: one timeout for every verb was shorter than a real first boot.

    The first genuine provisioning would have been reported `failed` while the stack was
    coming up correctly — a false negative on the one operation nobody can watch.
    """

    def test_every_verb_resolves_to_a_bound(self, wired):
        """No verb may fall through to a default chosen for `ps`. That is how 17 happened."""
        executor, _ = wired
        for verb in JobVerb:
            assert executor.timeout_for(verb) > 0, f"{verb} has no bound"

    def test_provisioning_gets_the_stack_bound(self, wired):
        executor, recorder = wired
        executor.execute(a_job(JobVerb.PROVISION))
        assert recorder.kwargs[0]["timeout"] == DEFAULT_STACK_TIMEOUT_SECONDS

    def test_start_waits_on_the_same_health_graph(self, wired):
        """`start` runs `up` too — after a host reboot that is a full cold boot."""
        executor, recorder = wired
        executor.execute(a_job(JobVerb.START))
        assert recorder.kwargs[0]["timeout"] == DEFAULT_STACK_TIMEOUT_SECONDS

    def test_rotate_tls_waits_on_the_same_health_graph(self, wired):
        """`--force-recreate` restarts every container, so the gates apply again.

        It has to be provisioned first: rotation refuses when there is no env file to
        rotate, so asserting on a bare fixture would assert on a command never run.
        """
        executor, recorder = wired
        assert executor.execute(a_job(JobVerb.PROVISION)).succeeded
        recorder.kwargs.clear()
        executor.execute(a_job(JobVerb.ROTATE_TLS))
        assert recorder.kwargs, "rotation never reached the command"
        assert recorder.kwargs[0]["timeout"] == DEFAULT_STACK_TIMEOUT_SECONDS

    def test_fast_verbs_stay_tightly_bounded(self, wired):
        """`ps` and `stop` return in seconds; a loose bound here hides a hang for an hour."""
        executor, recorder = wired
        for verb in (JobVerb.STATUS, JobVerb.STOP):
            recorder.kwargs.clear()
            executor.execute(a_job(verb))
            assert recorder.kwargs[0]["timeout"] == DEFAULT_TIMEOUT_SECONDS

    def test_backup_is_bounded_by_data_size_not_boot_time(self, wired):
        """⚠️ Unmeasured — no backup has been timed against a real clinical database."""
        executor, recorder = wired
        executor.execute(a_job(JobVerb.BACKUP_NOW))
        assert recorder.kwargs[0]["timeout"] == DEFAULT_BACKUP_TIMEOUT_SECONDS

    def test_the_timeout_is_configurable_per_host(self, tmp_path):
        """Facility hardware differs; a slower VM must be configuration, not a code change."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        recorder = Recorder()
        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "f",
            base_domain="example.org",
            stack_timeout_seconds=9000,
            runner=recorder,
        )
        executor.execute(a_job(JobVerb.PROVISION))
        assert recorder.kwargs[0]["timeout"] == 9000

    def test_a_timeout_names_the_verb_that_hit_it(self, tmp_path):
        """'timed out after 5400s' and 'after 900s' are different diagnoses."""

        def hang(argv, *, cwd, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "f",
            base_domain="example.org",
            runner=hang,
        )
        result = executor.execute(a_job(JobVerb.PROVISION))
        assert not result.succeeded
        assert "provision timed out" in result.detail
        assert str(DEFAULT_STACK_TIMEOUT_SECONDS) in result.detail


class TestTheStackBoundIsCheckedAgainstTheRealComposeFile:
    """The regression test for bug 17.

    The bound is not an opinion — it has to clear what the clinical stack actually makes
    `up` wait for. Reading the real compose file means a future gate with a longer grace
    period fails this suite rather than the first deployment on a facility host.
    """

    def test_compose_file_is_findable(self):
        """A check that cannot find its counterpart proves nothing (same trap as DH-024)."""
        assert COMPOSE_FILE.is_file(), (
            f"expected the clinical stack compose at {COMPOSE_FILE}. Mount it: "
            f'-v "$PWD/docker-compose.yml:/docker-compose.yml:ro"'
        )

    def test_the_stack_bound_clears_every_healthcheck_grace_period(self):
        periods = [
            int(value)
            for value in re.findall(
                r"start_period:\s*(\d+)s", COMPOSE_FILE.read_text(encoding="utf-8")
            )
        ]
        assert periods, "found no start_period values — the extraction regex is probably wrong"
        assert DEFAULT_STACK_TIMEOUT_SECONDS > max(periods), (
            f"the stack timeout ({DEFAULT_STACK_TIMEOUT_SECONDS}s) does not even clear the "
            f"longest healthcheck grace period in the compose file it runs ({max(periods)}s). "
            f"`up` waits on those gates, so provisioning would report failure while the stack "
            f"was still coming up correctly. This is bug 17."
        )

    def test_the_stack_bound_clears_the_measured_cold_boot(self):
        assert DEFAULT_STACK_TIMEOUT_SECONDS > MEASURED_COLD_BOOT_SECONDS, (
            f"a cold boot was measured at {MEASURED_COLD_BOOT_SECONDS}s (DH-002) and the "
            f"bound is {DEFAULT_STACK_TIMEOUT_SECONDS}s. A fresh host also pulls images "
            f"before any of that starts."
        )
