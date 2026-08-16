"""Credential rotation (ADR-024).

The valuable tests here are the refusals. "Rotate credentials" sounds like one operation;
it is not, and the obvious implementation would take a facility down or make its backups
unrecoverable. So each unrotatable credential gets a test asserting it is refused *with the
reason*, not merely skipped.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daher_agent.executor import ComposeExecutor
from daher_provisioning.facility_env import (
    ROTATABLE,
    UNROTATABLE,
    FacilityEnvError,
    rotate_env_values,
)
from daher_provisioning.jobs import Job, JobParams, JobVerb


class TestUnrotatableCredentialsAreRefused:
    @pytest.mark.parametrize("name", sorted(UNROTATABLE))
    def test_each_one_is_refused(self, name):
        with pytest.raises(FacilityEnvError, match="cannot be rotated"):
            rotate_env_values(f"{name}=old\n", {name: "new"})

    def test_database_refusal_says_what_is_actually_needed(self):
        with pytest.raises(FacilityEnvError, match="ALTER USER"):
            rotate_env_values("OMRS_DB_PASSWORD=old\n", {"OMRS_DB_PASSWORD": "new"})

    def test_admin_refusal_points_at_openmrs(self):
        with pytest.raises(FacilityEnvError, match="change it through OpenMRS"):
            rotate_env_values("OMRS_ADMIN_PASSWORD=old\n", {"OMRS_ADMIN_PASSWORD": "new"})

    def test_backup_refusal_names_the_real_consequence(self):
        """Changing it without escrow makes every prior archive unrecoverable."""
        with pytest.raises(FacilityEnvError, match="unrecoverable"):
            rotate_env_values(
                "BACKUP_ENCRYPTION_PASSPHRASE=old\n", {"BACKUP_ENCRYPTION_PASSPHRASE": "new"}
            )

    def test_the_two_sets_do_not_overlap(self):
        assert not set(ROTATABLE) & set(UNROTATABLE)


class TestRotatableValuesAreReplacedInPlace:
    def test_only_the_named_value_changes(self):
        original = "OE_KEYSTORE_PASSWORD=old\nOMRS_DB_PASSWORD=keepme\n# a comment\n"
        updated = rotate_env_values(original, {"OE_KEYSTORE_PASSWORD": "fresh"})
        assert "OE_KEYSTORE_PASSWORD=fresh" in updated
        assert "OMRS_DB_PASSWORD=keepme" in updated, "an untouched value was modified"
        assert "# a comment" in updated, "comments carry the warnings; they must survive"

    def test_an_absent_key_is_refused_rather_than_appended(self):
        """Appending would produce a file that looks rotated but is not."""
        with pytest.raises(FacilityEnvError, match="no entries for"):
            rotate_env_values("SOMETHING=1\n", {"OE_KEYSTORE_PASSWORD": "fresh"})

    def test_an_unknown_name_is_refused(self):
        with pytest.raises(FacilityEnvError, match="not in the rotatable set"):
            rotate_env_values("X=1\n", {"X": "2"})


class TestTheExecutorRotation:
    def test_rotation_rewrites_only_rotatable_values(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        calls: list[list[str]] = []

        def runner(argv, *, cwd, timeout):
            from daher_agent.executor import CommandResult

            calls.append(argv)
            return CommandResult(0, "ok", "")

        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "f",
            base_domain="example.org",
            runner=runner,
        )
        provision = Job(
            job_id="job-00000001",
            host_id="host-a",
            verb=JobVerb.PROVISION,
            params=JobParams(facility_id="daher-bc"),
            issued_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        assert executor.execute(provision).succeeded
        before = executor.env_path("daher-bc").read_text(encoding="utf-8")
        db_line = [line for line in before.splitlines() if line.startswith("OMRS_DB_PASSWORD=")][0]

        rotate = provision.model_copy(update={"verb": JobVerb.ROTATE_TLS, "job_id": "job-00000002"})
        assert executor.execute(rotate).succeeded

        after = executor.env_path("daher-bc").read_text(encoding="utf-8")
        assert db_line in after, "rotation changed a database password and would break the stack"
        for name in ("OE_KEYSTORE_PASSWORD", "OE_TRUSTSTORE_PASSWORD"):
            old = [ln for ln in before.splitlines() if ln.startswith(name)][0]
            assert old not in after, f"{name} was not actually rotated"

    def test_rotation_without_an_env_file_fails_cleanly(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        executor = ComposeExecutor(
            compose_file=compose,
            facility_root=tmp_path / "f",
            base_domain="example.org",
            runner=lambda argv, cwd, timeout: None,
        )
        job = Job(
            job_id="job-00000001",
            host_id="host-a",
            verb=JobVerb.ROTATE_TLS,
            params=JobParams(facility_id="daher-bc"),
            issued_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        result = executor.execute(job)
        assert not result.succeeded
        assert "nothing to rotate" in result.detail


class TestTheEndpoint:
    def test_it_reports_what_it_did_not_rotate(self, tmp_path):
        from daher_provisioning.app import create_app
        from daher_provisioning.audit import AuditLog
        from daher_provisioning.auth import KeyStore, Scope, issue_key
        from daher_provisioning.hosts import HostRegistry
        from daher_provisioning.models import DeployTarget

        key, record = issue_key("admin", actor="admin", scopes=frozenset(Scope))
        registry = HostRegistry()
        registry.register("host-a", DeployTarget(host="10.0.0.5"))
        registry.mark_enrolled("host-a")
        app = create_app(
            key_store=KeyStore([record]),
            audit_log=AuditLog(tmp_path / "audit.jsonl"),
            hosts=registry,
        )
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {key}"}
        client.post(
            "/facilities",
            json={"display_name": "Daher", "type": "hospital", "host_id": "host-a"},
            headers=headers,
        )
        response = client.post("/facilities/daher/rotate-credentials", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["queued"] is True
        assert "OE_KEYSTORE_PASSWORD" in body["rotated"]
        assert "OMRS_DB_PASSWORD" in body["not_rotated"], (
            "the response must state what it did NOT rotate, or it implies a full rotation"
        )

    def test_an_unknown_facility_is_404(self, tmp_path):
        from daher_provisioning.app import create_app
        from daher_provisioning.audit import AuditLog
        from daher_provisioning.auth import KeyStore, Scope, issue_key

        key, record = issue_key("admin", actor="admin", scopes=frozenset(Scope))
        app = create_app(key_store=KeyStore([record]), audit_log=AuditLog(tmp_path / "a.jsonl"))
        client = TestClient(app)
        response = client.post(
            "/facilities/nope/rotate-credentials", headers={"Authorization": f"Bearer {key}"}
        )
        assert response.status_code == 404
