"""Audit log behaviour (ADR-021).

The tests that matter are the tamper ones. A hash chain nobody has tried to break is a
claim, not a property — so each of the three realistic tampering shapes (edit an entry,
delete one, reorder) gets its own test asserting the chain reports it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daher_provisioning.audit import (
    GENESIS_HASH,
    AuditAction,
    AuditLog,
    AuditOutcome,
    ChainBroken,
    compute_hash,
)


def a_log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit" / "provisioning.jsonl")


def record_three(log: AuditLog) -> None:
    log.record(
        actor="portal",
        action=AuditAction.FACILITY_CREATE,
        outcome=AuditOutcome.SUCCESS,
        facility_id="facility-a",
    )
    log.record(actor="portal", action=AuditAction.FACILITY_LIST, outcome=AuditOutcome.SUCCESS)
    log.record(
        actor="portal",
        action=AuditAction.FACILITY_DECOMMISSION,
        outcome=AuditOutcome.FAILURE,
        facility_id="facility-a",
        message="host unreachable",
    )


class TestRecording:
    def test_first_entry_starts_the_chain(self, tmp_path):
        entry = a_log(tmp_path).record(
            actor="portal", action=AuditAction.FACILITY_LIST, outcome=AuditOutcome.SUCCESS
        )
        assert entry.sequence == 0
        assert entry.prev_hash == GENESIS_HASH

    def test_sequences_increment(self, tmp_path):
        log = a_log(tmp_path)
        record_three(log)
        assert [e.sequence for e in log.entries()] == [0, 1, 2]

    def test_each_entry_links_to_the_previous(self, tmp_path):
        log = a_log(tmp_path)
        record_three(log)
        entries = log.entries()
        for previous, current in zip(entries[:-1], entries[1:], strict=True):
            assert current.prev_hash == previous.entry_hash

    def test_failures_are_recorded_not_just_successes(self, tmp_path):
        """The action an attacker would prefer went unrecorded."""
        log = a_log(tmp_path)
        record_three(log)
        outcomes = {e.outcome for e in log.entries()}
        assert AuditOutcome.FAILURE in outcomes

    def test_denials_can_be_recorded(self, tmp_path):
        log = a_log(tmp_path)
        log.record(actor="unknown", action=AuditAction.AUTH_DENIED, outcome=AuditOutcome.DENIED)
        assert log.entries()[0].action == AuditAction.AUTH_DENIED

    def test_log_file_is_not_world_readable(self, tmp_path):
        import stat

        log = a_log(tmp_path)
        record_three(log)
        assert stat.S_IMODE(log.path.stat().st_mode) & 0o077 == 0

    def test_survives_a_missing_parent_directory(self, tmp_path):
        log = AuditLog(tmp_path / "deep" / "nested" / "audit.jsonl")
        log.record(actor="portal", action=AuditAction.FACILITY_LIST, outcome=AuditOutcome.SUCCESS)
        assert log.path.is_file()


class TestTamperDetection:
    """A hash chain nobody has tried to break is a claim, not a property."""

    def test_clean_log_verifies(self, tmp_path):
        log = a_log(tmp_path)
        record_three(log)
        assert log.verify_chain() == 3

    def test_edited_entry_is_detected(self, tmp_path):
        log = a_log(tmp_path)
        record_three(log)
        lines = log.path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        entry["facility_id"] = "somewhere-else"  # silent edit, hash left untouched
        lines[1] = json.dumps(entry)
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(ChainBroken) as caught:
            log.verify_chain()
        assert caught.value.sequence == 1
        assert "altered" in str(caught.value)

    def test_deleted_entry_is_detected(self, tmp_path):
        log = a_log(tmp_path)
        record_three(log)
        lines = log.path.read_text(encoding="utf-8").splitlines()
        del lines[1]
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(ChainBroken):
            log.verify_chain()

    def test_reordered_entries_are_detected(self, tmp_path):
        log = a_log(tmp_path)
        record_three(log)
        lines = log.path.read_text(encoding="utf-8").splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(ChainBroken):
            log.verify_chain()

    def test_truncating_the_start_is_detected(self, tmp_path):
        """Removing the beginning must not look like a shorter but valid log."""
        log = a_log(tmp_path)
        record_three(log)
        lines = log.path.read_text(encoding="utf-8").splitlines()
        log.path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")

        with pytest.raises(ChainBroken):
            log.verify_chain()

    def test_corrupt_json_is_reported_not_skipped(self, tmp_path):
        log = a_log(tmp_path)
        record_three(log)
        log.path.write_text(log.path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
        with pytest.raises(ChainBroken):
            log.verify_chain()

    def test_appending_a_forged_entry_without_the_chain_is_detected(self, tmp_path):
        log = a_log(tmp_path)
        record_three(log)
        forged = {
            "sequence": 3,
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "actor": "attacker",
            "action": "facility.list",
            "outcome": "success",
            "facility_id": None,
            "credential_set_id": None,
            "message": "",
            "source_ip": None,
            "prev_hash": GENESIS_HASH,  # wrong link
        }
        forged["entry_hash"] = compute_hash(forged)  # internally consistent, chain still wrong
        with log.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(forged) + "\n")
        with pytest.raises(ChainBroken):
            log.verify_chain()


class TestEmptyVerificationIsNotAPass:
    """Verifying zero entries proves nothing — callers must check the count."""

    def test_missing_log_verifies_zero_entries(self, tmp_path):
        assert AuditLog(tmp_path / "nothing.jsonl").verify_chain() == 0

    def test_count_distinguishes_empty_from_verified(self, tmp_path):
        log = a_log(tmp_path)
        assert log.verify_chain() == 0
        record_three(log)
        assert log.verify_chain() == 3


class TestSecretsNeverEnterTheLog:
    def test_entry_model_has_no_secret_shaped_fields(self):
        from daher_provisioning.audit import AuditEntry

        for field_name in AuditEntry.model_fields:
            lowered = field_name.lower()
            for forbidden in ("password", "secret", "token", "passphrase", "credential_value"):
                assert forbidden not in lowered, (
                    f"AuditEntry.{field_name} looks like it holds a secret. Entries reference "
                    f"credentials by id only (ADR-021)."
                )

    def test_entry_model_is_closed(self):
        from daher_provisioning.audit import AuditEntry

        assert AuditEntry.model_config.get("extra") == "forbid"

    def test_credentials_are_referenced_by_id(self, tmp_path):
        log = a_log(tmp_path)
        entry = log.record(
            actor="portal",
            action=AuditAction.CREDENTIALS_ROTATE,
            outcome=AuditOutcome.SUCCESS,
            facility_id="facility-a",
            credential_set_id="cred-set-123",
        )
        assert entry.credential_set_id == "cred-set-123"
        assert "cred-set-123" in log.path.read_text(encoding="utf-8")


class TestHashing:
    def test_hash_ignores_key_order(self):
        a = {"sequence": 0, "actor": "x", "entry_hash": "ignored"}
        b = {"actor": "x", "sequence": 0, "entry_hash": "different"}
        assert compute_hash(a) == compute_hash(b)

    def test_hash_changes_when_content_changes(self):
        a = {"sequence": 0, "actor": "x"}
        b = {"sequence": 0, "actor": "y"}
        assert compute_hash(a) != compute_hash(b)
