"""Durable storage.

The tests that matter are the **restart** ones: everything here exists because an API
restart previously lost the entire fleet. So each store is written, the connection is
closed, a fresh one is opened against the same file, and the data must still be there.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daher_provisioning.auth import PORTAL_SCOPES, Scope, issue_key
from daher_provisioning.enrolment import EnrolmentError, generate_agent_credential
from daher_provisioning.hosts import HostError
from daher_provisioning.jobs import Job, JobParams, JobVerb
from daher_provisioning.models import (
    BackupReport,
    BackupState,
    DeployTarget,
    Facility,
    FacilityState,
    FacilityType,
)
from daher_provisioning.queue import JobStatus
from daher_provisioning.repository import FacilityExists, FacilityNotFound
from daher_provisioning.store import (
    SCHEMA,
    Database,
    SqliteBackupStore,
    SqliteEnrolmentStore,
    SqliteFacilityStore,
    SqliteHostStore,
    SqliteJobQueue,
    SqliteKeyStore,
)


def a_facility(facility_id: str = "daher-bc") -> Facility:
    return Facility(
        facility_id=facility_id,
        display_name="Daher Birth Centre",
        type=FacilityType.HOSPITAL,
        deploy_target=DeployTarget(host="10.0.0.5"),
        created_at=datetime.now(UTC),
    )


def a_job(job_id: str = "job-00000001", host_id: str = "host-a") -> Job:
    return Job(
        job_id=job_id,
        host_id=host_id,
        verb=JobVerb.PROVISION,
        params=JobParams(facility_id="daher-bc"),
        issued_at=datetime.now(UTC),
    )


class TestSurvivesRestart:
    """The whole reason this module exists."""

    def test_facilities_survive(self, tmp_path):
        path = tmp_path / "state.db"
        db = Database(path)
        SqliteFacilityStore(db).add(a_facility())
        db.close()

        reopened = SqliteFacilityStore(Database(path))
        assert [f.facility_id for f in reopened.list()] == ["daher-bc"]

    def test_host_registrations_and_enrolment_survive(self, tmp_path):
        path = tmp_path / "state.db"
        db = Database(path)
        hosts = SqliteHostStore(db)
        hosts.register("host-a", DeployTarget(host="10.0.0.5"))
        hosts.mark_enrolled("host-a")
        db.close()

        reopened = SqliteHostStore(Database(path))
        assert reopened.get("host-a").enrolled is True

    def test_enrolled_agents_survive(self, tmp_path):
        path = tmp_path / "state.db"
        db = Database(path)
        enrolments = SqliteEnrolmentStore(db)
        token, _ = enrolments.mint("host-a")
        _secret, credential_hash = generate_agent_credential("host-a")
        enrolments.redeem(token, credential_hash=credential_hash)
        db.close()

        reopened = SqliteEnrolmentStore(Database(path))
        assert reopened.is_enrolled("host-a")
        assert reopened.agent("host-a").credential_hash == credential_hash

    def test_api_keys_survive(self, tmp_path):
        path = tmp_path / "state.db"
        db = Database(path)
        presentable, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        SqliteKeyStore(db, [record])
        db.close()

        reopened = SqliteKeyStore(Database(path))
        principal = reopened.authenticate(presentable)
        assert principal.key_id == "portal"
        assert principal.scopes == PORTAL_SCOPES

    def test_a_claimed_job_survives(self, tmp_path):
        """'Held, not deleted' must survive an API restart, not just an agent crash."""
        path = tmp_path / "state.db"
        db = Database(path)
        queue = SqliteJobQueue(db)
        queue.enqueue(a_job())
        queue.claim("host-a")
        db.close()

        reopened = SqliteJobQueue(Database(path))
        record = reopened.get("job-00000001")
        assert record.status is JobStatus.CLAIMED
        assert record.delivered_to == "host-a"
        assert reopened.claim("host-a") is None, "a claimed job must not be re-handed out"

    def test_a_backup_report_survives(self, tmp_path):
        """DH-027G. Same shape as the health-store restart guarantee: 'is this facility
        protected' must not reset to unknown just because the API process restarted."""
        path = tmp_path / "state.db"
        db = Database(path)
        SqliteBackupStore(db).record(
            "daher-bc",
            BackupReport(
                state=BackupState.NOT_CONFIGURED,
                checked_at=datetime.now(UTC),
                reason="no passphrase and/or off-site destination configured on this host",
            ),
        )
        db.close()

        reopened = SqliteBackupStore(Database(path))
        latest = reopened.latest("daher-bc")
        assert latest is not None
        assert latest.state is BackupState.NOT_CONFIGURED
        assert "passphrase" in latest.reason

    def test_a_second_backup_report_replaces_the_first(self, tmp_path):
        """Latest only, deliberately (same policy as the health store) — a fleet dashboard
        needs what is true now, not a history of every backup attempt."""
        path = tmp_path / "state.db"
        db = Database(path)
        store = SqliteBackupStore(db)
        store.record(
            "daher-bc",
            BackupReport(state=BackupState.FAILED, checked_at=datetime.now(UTC), reason="x"),
        )
        store.record(
            "daher-bc", BackupReport(state=BackupState.SUCCESS, checked_at=datetime.now(UTC))
        )
        db.close()

        reopened = SqliteBackupStore(Database(path))
        assert reopened.latest("daher-bc").state is BackupState.SUCCESS


class TestNoClinicalCredentialIsStored:
    """Facility secrets live on the facility host, never here (ADR-022)."""

    def test_no_column_is_named_for_a_secret(self):
        lowered = SCHEMA.lower()
        for forbidden in ("password", "passphrase", "secret", "credential_value", "env"):
            assert f"{forbidden} text" not in lowered, (
                f"a column named for {forbidden!r} would put facility credentials in the "
                f"control plane's database, which ADR-022 exists to prevent"
            )

    def test_only_hashes_are_stored(self):
        assert "credential_hash" in SCHEMA
        assert "token_hash" in SCHEMA
        assert "key_hash" in SCHEMA

    def test_an_issued_key_secret_is_not_recoverable(self, tmp_path):
        db = Database(tmp_path / "state.db")
        presentable, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        SqliteKeyStore(db, [record])
        secret = presentable.split("_", 2)[2]
        dumped = "".join(r["key_hash"] for r in db.query("SELECT key_hash FROM api_keys"))
        assert secret not in dumped


class TestFacilityStore:
    def test_duplicate_is_refused_with_the_reason(self, tmp_path):
        store = SqliteFacilityStore(Database(tmp_path / "s.db"))
        store.add(a_facility())
        with pytest.raises(FacilityExists, match="patient database"):
            store.add(a_facility())

    def test_unknown_is_an_error(self, tmp_path):
        with pytest.raises(FacilityNotFound):
            SqliteFacilityStore(Database(tmp_path / "s.db")).get("nope")

    def test_state_change_persists(self, tmp_path):
        path = tmp_path / "s.db"
        db = Database(path)
        store = SqliteFacilityStore(db)
        store.add(a_facility())
        store.set_state("daher-bc", FacilityState.DECOMMISSIONED)
        db.close()
        assert SqliteFacilityStore(Database(path)).get("daher-bc").state is (
            FacilityState.DECOMMISSIONED
        )


class TestHostStore:
    def test_occupied_host_is_refused(self, tmp_path):
        hosts = SqliteHostStore(Database(tmp_path / "s.db"))
        hosts.register("host-a", DeployTarget(host="10.0.0.5"))
        hosts.mark_enrolled("host-a")
        hosts.claim("host-a", "facility-a")
        with pytest.raises(HostError, match="existing patient database"):
            hosts.claim("host-a", "facility-b")

    def test_unenrolled_host_cannot_be_claimed(self, tmp_path):
        hosts = SqliteHostStore(Database(tmp_path / "s.db"))
        hosts.register("host-a", DeployTarget(host="10.0.0.5"))
        with pytest.raises(HostError, match="has not enrolled"):
            hosts.claim("host-a", "facility-a")

    def test_unknown_host_refusal_explains_the_allowlist(self, tmp_path):
        with pytest.raises(HostError, match="never taken from the request"):
            SqliteHostStore(Database(tmp_path / "s.db")).get("attacker-chosen")

    def test_double_registration_is_refused(self, tmp_path):
        hosts = SqliteHostStore(Database(tmp_path / "s.db"))
        hosts.register("host-a", DeployTarget(host="10.0.0.5"))
        with pytest.raises(HostError, match="already registered"):
            hosts.register("host-a", DeployTarget(host="10.0.0.6"))


class TestEnrolmentStore:
    def test_single_use_survives_persistence(self, tmp_path):
        path = tmp_path / "s.db"
        db = Database(path)
        enrolments = SqliteEnrolmentStore(db)
        token, _ = enrolments.mint("host-a")
        _s, credential_hash = generate_agent_credential("host-a")
        enrolments.redeem(token, credential_hash=credential_hash)
        db.close()

        # A replay after a restart must still be refused — the spent flag is durable.
        reopened = SqliteEnrolmentStore(Database(path))
        with pytest.raises(EnrolmentError, match="already redeemed"):
            reopened.redeem(token, credential_hash=credential_hash)

    def test_a_secret_where_a_hash_belongs_is_refused(self, tmp_path):
        enrolments = SqliteEnrolmentStore(Database(tmp_path / "s.db"))
        token, _ = enrolments.mint("host-a")
        secret, _ = generate_agent_credential("host-a")
        with pytest.raises(EnrolmentError, match="never reach the provisioning host"):
            enrolments.redeem(token, credential_hash=secret)

    def test_minting_for_an_enrolled_host_is_refused(self, tmp_path):
        enrolments = SqliteEnrolmentStore(Database(tmp_path / "s.db"))
        token, _ = enrolments.mint("host-a")
        _s, credential_hash = generate_agent_credential("host-a")
        enrolments.redeem(token, credential_hash=credential_hash)
        with pytest.raises(EnrolmentError, match="revoke it deliberately first"):
            enrolments.mint("host-a")


class TestKeyStore:
    def test_revocation_persists(self, tmp_path):
        path = tmp_path / "s.db"
        db = Database(path)
        presentable, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        store = SqliteKeyStore(db, [record])
        store.revoke("portal")
        db.close()

        from daher_provisioning.auth import AuthenticationError

        with pytest.raises(AuthenticationError, match="revoked"):
            SqliteKeyStore(Database(path)).authenticate(presentable)

    def test_an_unknown_key_is_refused(self, tmp_path):
        from daher_provisioning.auth import AuthenticationError

        with pytest.raises(AuthenticationError):
            SqliteKeyStore(Database(tmp_path / "s.db")).authenticate("dhp_nope_secret")

    def test_scopes_round_trip(self, tmp_path):
        db = Database(tmp_path / "s.db")
        presentable, record = issue_key("admin", actor="admin", scopes=frozenset(Scope))
        store = SqliteKeyStore(db, [record])
        assert store.authenticate(presentable).scopes == frozenset(Scope)


class TestJobQueue:
    def test_jobs_go_only_to_their_host(self, tmp_path):
        queue = SqliteJobQueue(Database(tmp_path / "s.db"))
        queue.enqueue(a_job(host_id="host-a"))
        assert queue.claim("host-b") is None
        assert queue.claim("host-a").job_id == "job-00000001"

    def test_order_is_preserved(self, tmp_path):
        queue = SqliteJobQueue(Database(tmp_path / "s.db"))
        queue.enqueue(a_job("job-00000001"))
        queue.enqueue(a_job("job-00000002"))
        assert queue.claim("host-a").job_id == "job-00000001"
        assert queue.claim("host-a").job_id == "job-00000002"

    def test_duplicate_job_id_is_refused(self, tmp_path):
        from daher_provisioning.queue import QueueError

        queue = SqliteJobQueue(Database(tmp_path / "s.db"))
        queue.enqueue(a_job())
        with pytest.raises(QueueError, match="already queued"):
            queue.enqueue(a_job())

    def test_finish_records_status_and_detail(self, tmp_path):
        queue = SqliteJobQueue(Database(tmp_path / "s.db"))
        queue.enqueue(a_job())
        queue.claim("host-a")
        record = queue.finish("job-00000001", JobStatus.REFUSED, "hostile parameter")
        assert record.status is JobStatus.REFUSED
        assert record.detail == "hostile parameter"

    def test_pending_count(self, tmp_path):
        queue = SqliteJobQueue(Database(tmp_path / "s.db"))
        queue.enqueue(a_job("job-00000001"))
        queue.enqueue(a_job("job-00000002"))
        assert queue.pending_count("host-a") == 2
        queue.claim("host-a")
        assert queue.pending_count("host-a") == 1


class TestTheApiSurvivesARestart:
    """End-to-end: the whole point of this module, exercised through the real app."""

    @staticmethod
    def app_for(path, presentable_record):
        from daher_provisioning.app import create_app
        from daher_provisioning.audit import AuditLog

        db = Database(path)
        return (
            create_app(
                key_store=SqliteKeyStore(db, presentable_record),
                audit_log=AuditLog(path.parent / "audit.jsonl"),
                repository=SqliteFacilityStore(db),
                hosts=SqliteHostStore(db),
                job_queue=SqliteJobQueue(db),
                enrolment=SqliteEnrolmentStore(db),
            ),
            db,
        )

    def test_a_facility_created_before_a_restart_is_still_there_after(self, tmp_path):
        from fastapi.testclient import TestClient

        path = tmp_path / "state.db"
        presentable, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)

        app, db = self.app_for(path, [record])
        hosts = SqliteHostStore(db)
        hosts.register("host-a", DeployTarget(host="10.0.0.5"))
        hosts.mark_enrolled("host-a")

        client = TestClient(app)
        created = client.post(
            "/facilities",
            json={
                "display_name": "Daher Birth Centre",
                "type": "hospital",
                "host_id": "host-a",
            },
            headers={"Authorization": f"Bearer {presentable}"},
        )
        assert created.status_code == 201
        db.close()  # the control plane restarts

        # A completely fresh app against the same file — as after a process restart.
        app_again, _ = self.app_for(path, [])
        listed = TestClient(app_again).get(
            "/facilities", headers={"Authorization": f"Bearer {presentable}"}
        )
        assert listed.status_code == 200, "the portal credential did not survive the restart"
        assert [f["facility_id"] for f in listed.json()] == ["daher-birth-centre"]

    def test_a_host_stays_claimed_across_a_restart(self, tmp_path):
        """Otherwise a restart would let a second facility be deployed over a live one."""
        from fastapi.testclient import TestClient

        path = tmp_path / "state.db"
        presentable, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
        app, db = self.app_for(path, [record])
        hosts = SqliteHostStore(db)
        hosts.register("host-a", DeployTarget(host="10.0.0.5"))
        hosts.mark_enrolled("host-a")

        body = {"display_name": "First", "type": "hospital", "host_id": "host-a"}
        TestClient(app).post(
            "/facilities", json=body, headers={"Authorization": f"Bearer {presentable}"}
        )
        db.close()

        app_again, _ = self.app_for(path, [])
        second = TestClient(app_again).post(
            "/facilities",
            json={"display_name": "Second", "type": "lab", "host_id": "host-a"},
            headers={"Authorization": f"Bearer {presentable}"},
        )
        assert second.status_code == 409
        assert "patient database" in second.json()["detail"]
