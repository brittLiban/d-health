"""Facility env rendering and writing.

The important test here is `test_renders_every_variable_the_real_compose_file_requires`:
it parses the actual `docker-compose.yml` for `${VAR:?}` guards and asserts the renderer
produces all of them. That keeps provisioning and the clinical stack in sync automatically
— when someone adds a required variable to compose, this fails rather than a facility
failing to boot after it has already been provisioned.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from daher_provisioning.facility_env import (
    FacilityEnvError,
    missing_required,
    render_env,
    write_env,
)
from daher_provisioning.models import DeployTarget, Facility, FacilityType
from daher_provisioning.secrets import generate_facility_secrets

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def a_facility(facility_id: str = "test-facility") -> Facility:
    return Facility(
        facility_id=facility_id,
        display_name="Test Birth Centre",
        type=FacilityType.HOSPITAL,
        deploy_target=DeployTarget(host="10.0.0.5"),
        created_at=datetime.now(UTC),
    )


def rendered(facility_id: str = "test-facility") -> str:
    facility = a_facility(facility_id)
    return render_env(facility, generate_facility_secrets(facility_id), base_domain="example.org")


class TestStaysInSyncWithTheClinicalStack:
    def test_compose_file_is_findable(self):
        """A sync test that cannot find its counterpart proves nothing."""
        assert COMPOSE_FILE.is_file(), f"expected the clinical stack compose at {COMPOSE_FILE}"

    def test_renders_every_variable_the_real_compose_file_requires(self):
        required = set(re.findall(r"\$\{([A-Z_0-9]+):\?", COMPOSE_FILE.read_text(encoding="utf-8")))
        assert required, "found no ${VAR:?} guards — the extraction regex is probably wrong"
        absent = missing_required(rendered(), required)
        assert not absent, (
            f"the clinical stack hard-requires {sorted(absent)} but provisioning does not "
            f"render them. A facility provisioned now would fail at `docker compose up`."
        )


class TestRendering:
    def test_sets_the_facility_id(self):
        assert "FACILITY_ID=test-facility" in rendered()

    def test_demo_content_is_off_for_provisioned_facilities(self):
        """ADR-019 — demo forms that compute and alert have no place in a deployment."""
        assert "OMRS_DEMO_CONTENT=off" in rendered()
        assert "OMRS_DEMO_PATIENTS=off" in rendered()

    def test_sso_is_fail_closed(self):
        assert "SSO_ENABLED=false" in rendered()

    def test_bed_counts_are_zero_placeholders_not_invented_numbers(self):
        text = rendered()
        assert "MATERNITY_DELIVERY_BEDS=0" in text
        assert "MATERNITY_POSTPARTUM_BEDS=0" in text

    def test_header_warns_that_facility_id_is_permanent(self):
        assert "orphans the patient database" in rendered()

    def test_header_warns_that_rewriting_is_not_rotation(self):
        assert "does NOT rotate" in rendered()

    def test_refuses_mismatched_secrets(self):
        facility = a_facility("facility-a")
        secrets = generate_facility_secrets("facility-b")
        with pytest.raises(FacilityEnvError, match="refusing to write mismatched credentials"):
            render_env(facility, secrets, base_domain="example.org")

    def test_patient_public_base_url_follows_the_facility_domain(self):
        """DHEALTH-CI-REPAIR-001. The clinical stack hard-requires this variable with no
        default, so a facility that is not told it fails at `docker compose up` *after*
        provisioning has already finished -- which is what happened.

        The value is the repository's existing contract, not a choice made by this test:
        `docker-compose.native.yml` defaults the same variable to
        `https://app.${BASE_DOMAIN}` and `deploy/native/native.env.example` documents that
        default. Asserted against the facility's own domain so a hardcoded host, or CI's
        `https://localhost`, cannot pass."""
        assert "DHEALTH_PATIENT_PUBLIC_BASE_URL=https://app.example.org" in rendered()

    def test_patient_public_base_url_is_https_because_it_carries_a_credential(self):
        """A patient link carries a live bearer credential in its fragment, and
        `Settings.from_env` refuses a non-https value (ADR-058). A renderer that emitted
        `http://` would produce a facility whose adapter refuses to start."""
        line = next(
            row
            for row in rendered().splitlines()
            if row.startswith("DHEALTH_PATIENT_PUBLIC_BASE_URL=")
        )
        value = line.split("=", 1)[1]
        assert value.startswith("https://"), value
        assert "localhost" not in value, (
            f"{value!r} looks like CI's demo value; a provisioned facility must use its own domain"
        )

    def test_requires_a_base_domain(self):
        facility = a_facility()
        with pytest.raises(FacilityEnvError, match="base_domain is required"):
            render_env(facility, generate_facility_secrets(facility.facility_id), base_domain="")

    def test_is_parseable_as_key_value_pairs(self):
        for line in rendered().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            assert "=" in line, f"unparseable env line: {line!r}"


class TestWriting:
    def test_writes_with_mode_0600(self, tmp_path):
        target = write_env(tmp_path / "facility" / ".env", rendered())
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)} — credentials must not be readable"

    def test_refuses_to_overwrite_an_existing_file(self, tmp_path):
        """Rewriting credentials does not rotate them; it breaks the running stack."""
        target = tmp_path / ".env"
        write_env(target, rendered())
        with pytest.raises(FacilityEnvError, match="not how rotation works"):
            write_env(target, rendered())

    def test_overwrite_is_possible_but_must_be_explicit(self, tmp_path):
        target = tmp_path / ".env"
        write_env(target, rendered())
        write_env(target, rendered(), overwrite=True)

    def test_creates_missing_parent_directories(self, tmp_path):
        target = write_env(tmp_path / "a" / "b" / ".env", rendered())
        assert target.is_file()

    def test_written_contents_round_trip(self, tmp_path):
        contents = rendered()
        target = write_env(tmp_path / ".env", contents)
        assert target.read_text(encoding="utf-8") == contents


class TestMissingRequired:
    def test_detects_an_absent_variable(self):
        assert missing_required("A=1\nB=2\n", {"A", "C"}) == {"C"}

    def test_ignores_comments(self):
        assert missing_required("# C=3\nA=1\n", {"C"}) == {"C"}

    def test_returns_empty_when_all_present(self):
        assert missing_required("A=1\nB=2\n", {"A", "B"}) == set()


class TestWritesAreAtomic:
    """DH-027F. The old writer opened the target with O_TRUNC, so a death mid-write left a
    torn env file — and on the rotation path that file is a live facility's credentials.
    """

    def test_a_failed_write_leaves_the_original_file_intact(self, tmp_path, monkeypatch):
        """The property that matters. Without the temp-and-rename this test fails, because
        O_TRUNC would already have emptied the target before the failure."""
        target = tmp_path / ".env"
        write_env(target, "ORIGINAL=intact\n")

        def explode(*_args, **_kwargs):
            raise OSError("disk full during rename")

        monkeypatch.setattr("daher_provisioning.facility_env.os.replace", explode)
        with pytest.raises(OSError):
            write_env(target, "REPLACEMENT=never-landed\n", overwrite=True)

        assert target.read_text(encoding="utf-8") == "ORIGINAL=intact\n", (
            "a failed write must not damage the credentials already in place"
        )

    def test_a_failed_write_leaves_no_temporary_file_behind(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        write_env(target, "ORIGINAL=intact\n")

        def explode(*_args, **_kwargs):
            raise OSError("disk full during rename")

        monkeypatch.setattr("daher_provisioning.facility_env.os.replace", explode)
        with pytest.raises(OSError):
            write_env(target, "REPLACEMENT=never-landed\n", overwrite=True)

        leftovers = [p.name for p in tmp_path.iterdir() if p.name != ".env"]
        assert leftovers == [], f"temporary files left behind: {leftovers}"

    def test_a_successful_write_leaves_no_temporary_file_behind(self, tmp_path):
        target = tmp_path / ".env"
        write_env(target, "A=1\n")
        write_env(target, "A=2\n", overwrite=True)
        assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]

    def test_the_temporary_file_is_created_in_the_targets_own_directory(self, tmp_path):
        """`os.replace` is only atomic within one filesystem, so /tmp would not do."""
        seen: list[str] = []
        real_mkstemp = tempfile.mkstemp

        def watching(*args, **kwargs):
            seen.append(str(kwargs.get("dir")))
            return real_mkstemp(*args, **kwargs)

        target = tmp_path / "facility" / ".env"
        with patch("daher_provisioning.facility_env.tempfile.mkstemp", side_effect=watching):
            write_env(target, "A=1\n")
        assert seen == [str(target.parent)]

    def test_the_replacement_is_still_mode_0600(self, tmp_path):
        """mkstemp creates 0600, and the rename must not widen it."""
        target = tmp_path / ".env"
        write_env(target, "A=1\n")
        write_env(target, "A=2\n", overwrite=True)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert target.read_text(encoding="utf-8") == "A=2\n"

    def test_contents_are_fsynced_before_the_rename(self, tmp_path):
        """Without fsync a power cut can leave the renamed file present but empty, which
        looks exactly like a successful write."""
        order: list[str] = []
        real_fsync, real_replace = os.fsync, os.replace

        def note_fsync(fd):
            order.append("fsync")
            return real_fsync(fd)

        def note_replace(a, b):
            order.append("replace")
            return real_replace(a, b)

        with (
            patch("daher_provisioning.facility_env.os.fsync", side_effect=note_fsync),
            patch("daher_provisioning.facility_env.os.replace", side_effect=note_replace),
        ):
            write_env(tmp_path / ".env", "A=1\n")

        assert "fsync" in order and "replace" in order
        assert order.index("fsync") < order.index("replace"), (
            "the contents must be durable before the rename makes them visible"
        )
