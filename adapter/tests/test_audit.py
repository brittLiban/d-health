import json
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from daher_adapter.audit import AuditEvent, AuditWriter


def event() -> AuditEvent:
    return AuditEvent(
        direction="openmrs_to_openelis",
        event_type="ServiceRequest",
        source_system="openmrs",
        source_record_id="service-request-1",
        target_system="openelis",
        target_record_id="task-1",
        patient_reference="patient-1",
        outcome="processed",
    )


def test_audit_is_append_only_jsonl_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "audit.jsonl"
    writer = AuditWriter(path)

    writer.append(event())
    writer.append(event().model_copy(update={"outcome": "duplicate"}))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["outcome"] for line in lines] == ["processed", "duplicate"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_audit_shape_rejects_clinical_payload_fields() -> None:
    values = event().model_dump()
    values["clinical_value"] = "must-not-be-logged"

    with pytest.raises(ValidationError, match="clinical_value"):
        AuditEvent.model_validate(values)
