"""Backup status through the real HTTP API (DH-027G).

`/facilities/{id}/status` used to hard-code `backup_state=BackupState.NEVER_RUN` regardless
of anything an agent ever reported — a placeholder wired to nothing. These tests exercise the
real `create_app`, the real `/agent/jobs/{id}/report` endpoint, and the real facility-status
read path together, the same way `test_decommission.py` proves DH-027E end to end rather than
unit-testing `next_facility_state` alone.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daher_provisioning.app import create_app
from daher_provisioning.audit import AuditLog
from daher_provisioning.auth import PORTAL_SCOPES, KeyStore, issue_key
from daher_provisioning.enrolment import generate_agent_credential
from daher_provisioning.hosts import HostRegistry
from daher_provisioning.jobs import Job, JobParams, JobVerb
from daher_provisioning.models import DeployTarget
from daher_provisioning.ratelimit import TokenBucketLimiter

HOST = "host-a"
FACILITY = "clinic-one"


@pytest.fixture
def wired(tmp_path):
    key, record = issue_key("portal", actor="portal", scopes=PORTAL_SCOPES)
    log = AuditLog(tmp_path / "audit.jsonl")
    hosts = HostRegistry()
    hosts.register(HOST, DeployTarget(host="10.0.0.5"))
    hosts.mark_enrolled(HOST)
    app = create_app(
        key_store=KeyStore([record]),
        audit_log=log,
        limiter=TokenBucketLimiter(rate_per_second=200, burst=200),
        hosts=hosts,
    )
    client = TestClient(app)
    head = {"Authorization": f"Bearer {key}"}
    client.post(
        "/facilities",
        json={
            "display_name": "Clinic One",
            "type": "hospital",
            "host_id": HOST,
            "facility_id": FACILITY,
        },
        headers=head,
    )
    return client, head, log, app


def agent_headers(app):
    """Enrol this host the way the real bootstrap does — the API only ever stores the
    hash (ADR-022)."""
    token, _record = app.state.enrolments.mint(HOST)
    secret, credential_hash = generate_agent_credential(HOST)
    app.state.enrolments.redeem(token, credential_hash=credential_hash)
    return {"Authorization": f"Bearer {secret}"}


def queue_backup_now(app) -> str:
    """Nothing auto-queues BACKUP_NOW (matches STATUS's current state — DH-027D never wired
    an on-demand trigger either); tests enqueue it directly, the same way the demo harness's
    raw-enqueue hatch stands in for an operator/portal action that does not exist yet."""
    from datetime import UTC, datetime

    job = Job(
        job_id="job-backup-000001",
        host_id=HOST,
        verb=JobVerb.BACKUP_NOW,
        params=JobParams(facility_id=FACILITY),
        issued_at=datetime.now(UTC),
    )
    app.state.queue.enqueue(job)
    return job.job_id


def queue_status(app, job_id="job-status-000001") -> str:
    from datetime import UTC, datetime

    job = Job(
        job_id=job_id,
        host_id=HOST,
        verb=JobVerb.STATUS,
        params=JobParams(facility_id=FACILITY),
        issued_at=datetime.now(UTC),
    )
    app.state.queue.enqueue(job)
    return job.job_id


def status_of(client, head, facility_id=FACILITY) -> dict:
    return client.get(f"/facilities/{facility_id}/status", headers=head).json()


def report(client, agent, job_id, body):
    return client.post(f"/agent/jobs/{job_id}/report", headers=agent, json=body)


class TestBackupStateIsReportedNotFabricated:
    def test_a_freshly_created_facility_has_never_run_not_a_lie(self, wired):
        """Before any BACKUP_NOW report exists there is no evidence either way — NEVER_RUN
        is the honest 'nothing has told us yet', not a claim about configuration."""
        client, head, _, _ = wired
        assert status_of(client, head)["backup_state"] == "never_run"
        assert status_of(client, head)["backup_last_run"] is None

    def test_not_configured_is_reported_and_persisted(self, wired):
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        response = report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "backup not configured on this host",
                "backup": {
                    "state": "not_configured",
                    "checked_at": "2026-08-21T00:00:00Z",
                    "reason": "no passphrase and/or off-site destination (ADR-025)",
                },
            },
        )
        assert response.status_code == 200
        status = status_of(client, head)
        assert status["backup_state"] == "not_configured"
        assert status["backup_last_run"] is None, (
            "not_configured is not a run — it must not be presented as one"
        )

    def test_success_is_reported_and_last_run_is_set(self, wired):
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "backup ok",
                "backup": {"state": "success", "checked_at": "2026-08-21T03:00:00Z"},
            },
        )
        status = status_of(client, head)
        assert status["backup_state"] == "success"
        assert status["backup_last_run"] == "2026-08-21T03:00:00Z"

    def test_a_successful_backup_does_not_imply_a_verified_restore(self, wired):
        """ADR-025: 'scheduled' and 'verified' are separate properties. Nothing in the
        BACKUP_NOW report path may set `restore_drill_last_performed` — that field is
        populated only by an actually-performed drill, which is out of this ticket's scope
        by design (`backup/restore-drill.md` is a manual, human-verified procedure)."""
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "ok",
                "backup": {"state": "success", "checked_at": "2026-08-21T00:00:00Z"},
            },
        )
        assert status_of(client, head)["restore_drill_last_performed"] is None

    def test_failure_is_reported_as_failed_not_not_configured(self, wired):
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "failed",
                "detail": "exit 1",
                "backup": {
                    "state": "failed",
                    "checked_at": "2026-08-21T03:00:00Z",
                    "reason": "backup command exited 1",
                },
            },
        )
        assert status_of(client, head)["backup_state"] == "failed"


class TestAMalformedReportFailsClosed:
    """DH-027D's rule, applied to backup: a report the API cannot trust must never be read
    as a working backup."""

    def test_missing_backup_evidence_is_persisted_as_failed_not_ignored(self, wired):
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        response = report(client, agent, job_id, {"status": "succeeded", "detail": "ok"})
        assert response.status_code == 200, "the job report itself is still valid"
        status = status_of(client, head)
        assert status["backup_state"] == "failed", (
            "no evidence must never read as a successful backup"
        )

    def test_evidence_that_fails_schema_validation_is_persisted_as_failed(self, wired):
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "ok",
                "backup": {"state": "not-a-real-state", "checked_at": "2026-08-21T00:00:00Z"},
            },
        )
        assert status_of(client, head)["backup_state"] == "failed"

    def test_an_unknown_top_level_status_is_refused_outright(self, wired):
        """Pre-existing behaviour (unaffected by DH-027G), asserted here because the test
        matrix explicitly calls for 'malformed agent report → refused'."""
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        response = report(client, agent, job_id, {"status": "vibes-good", "detail": "?"})
        assert response.status_code == 400


class TestBackupEvidenceIsInfrastructureOnly:
    def test_a_configured_passphrase_never_appears_in_the_response(self, wired):
        """End-to-end version of `test_no_clinical_data.py`'s structural guard: even if a
        field existed, this proves the real HTTP response body carries no such value."""
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "ok",
                "backup": {"state": "success", "checked_at": "2026-08-21T00:00:00Z"},
            },
        )
        raw = status_of(client, head)
        assert "a-long-operator-supplied-passphrase" not in str(raw)
        assert "passphrase" not in "".join(raw.keys()).lower()

    def test_backup_now_never_changes_lifecycle_state(self, wired):
        """Reading/attempting a backup is not a lifecycle event (existing behaviour,
        confirmed here for the new report path specifically)."""
        client, head, _, app = wired
        before = status_of(client, head)["state"]
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "failed",
                "detail": "exit 1",
                "backup": {"state": "failed", "checked_at": "2026-08-21T00:00:00Z"},
            },
        )
        assert status_of(client, head)["state"] == before


class TestTheAuditLogRecordsBackupOutcomes:
    def test_the_backup_state_appears_in_the_audit_message(self, wired):
        client, head, log, app = wired
        agent = agent_headers(app)
        job_id = queue_backup_now(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "ok",
                "backup": {"state": "success", "checked_at": "2026-08-21T00:00:00Z"},
            },
        )
        messages = " ".join(e.message or "" for e in log.entries())
        assert "backup success" in messages


class TestStatusAlsoCarriesScheduledBackupEvidence:
    """DH-027H. `STATUS` observes the nightly scheduler's own `last-run.status` alongside
    infrastructure health — reusing the fleet's one observation seam instead of adding a
    second job verb beside it."""

    def test_a_status_report_with_backup_evidence_updates_backup_state(self, wired):
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_status(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "healthy: 1/1 services",
                "backup": {"state": "success", "checked_at": "2026-08-21T02:00:00Z"},
            },
        )
        status = status_of(client, head)
        assert status["backup_state"] == "success"
        assert status["backup_last_run"] == "2026-08-21T02:00:00Z"

    def test_a_status_report_without_backup_evidence_leaves_backup_state_untouched(self, wired):
        """Additive, not mandatory (unlike infrastructure evidence): a mixed-version agent
        that has not been upgraded to observe backup yet must not make backup look FAILED
        merely because this particular STATUS check did not carry it."""
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_status(app)
        report(client, agent, job_id, {"status": "succeeded", "detail": "healthy: 1/1 services"})
        assert status_of(client, head)["backup_state"] == "never_run"

    def test_malformed_backup_evidence_on_a_status_report_still_fails_closed(self, wired):
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_status(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "healthy: 1/1 services",
                "backup": {"state": "not-a-real-state", "checked_at": "2026-08-21T00:00:00Z"},
            },
        )
        assert status_of(client, head)["backup_state"] == "failed"

    def test_a_scheduled_success_observation_does_not_imply_a_verified_restore(self, wired):
        """The same ADR-025 separation as DH-027G's manual-path test, proven for the
        scheduled-observation path too — a successful archive does not prove it restores."""
        client, head, _, app = wired
        agent = agent_headers(app)
        job_id = queue_status(app)
        report(
            client,
            agent,
            job_id,
            {
                "status": "succeeded",
                "detail": "healthy: 1/1 services",
                "backup": {"state": "success", "checked_at": "2026-08-21T02:00:00Z"},
            },
        )
        assert status_of(client, head)["restore_drill_last_performed"] is None


class TestManualAndScheduledBackupPrecedence:
    """The out-of-order property from `test_store.py`, now proven through the real report
    endpoint end to end rather than only at the store."""

    def test_a_stale_scheduled_observation_does_not_override_a_fresher_manual_result(self, wired):
        client, head, _, app = wired
        agent = agent_headers(app)

        manual_job = queue_backup_now(app)
        report(
            client,
            agent,
            manual_job,
            {
                "status": "succeeded",
                "detail": "ok",
                "backup": {"state": "success", "checked_at": "2026-08-21T15:00:00Z"},
            },
        )

        status_job = queue_status(app, "job-status-000002")
        report(
            client,
            agent,
            status_job,
            {
                "status": "succeeded",
                "detail": "healthy: 1/1 services",
                "backup": {"state": "failed", "checked_at": "2026-08-21T02:00:00Z"},
            },
        )

        status = status_of(client, head)
        assert status["backup_state"] == "success", (
            "a stale nightly marker from before the manual run must not resurrect an older "
            "result over a fresher one"
        )
        assert status["backup_last_run"] == "2026-08-21T15:00:00Z"

    def test_a_fresher_scheduled_failure_does_supersede_an_older_manual_success(self, wired):
        """The dangerous direction: real, newer bad news must win over old good news."""
        client, head, _, app = wired
        agent = agent_headers(app)

        manual_job = queue_backup_now(app)
        report(
            client,
            agent,
            manual_job,
            {
                "status": "succeeded",
                "detail": "ok",
                "backup": {"state": "success", "checked_at": "2026-08-20T15:00:00Z"},
            },
        )

        status_job = queue_status(app, "job-status-000002")
        report(
            client,
            agent,
            status_job,
            {
                "status": "succeeded",
                "detail": "healthy: 1/1 services",
                "backup": {"state": "failed", "checked_at": "2026-08-21T02:00:00Z"},
            },
        )

        status = status_of(client, head)
        assert status["backup_state"] == "failed", (
            "a genuinely newer failure must not be masked by an older success"
        )
