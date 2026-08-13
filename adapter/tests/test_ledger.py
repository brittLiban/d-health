import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.ledger import LedgerRepository, LedgerStateError, MessageIdentity
from daher_adapter.models import DeadLetter, ProcessedMessage


def identity() -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id="order-uuid",
        resource_version="1",
    )


def test_identity_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        MessageIdentity(
            source_system="openmrs",
            direction="unknown",  # type: ignore[arg-type]
            resource_type="ServiceRequest",
            resource_id="order-uuid",
            resource_version="1",
        )


def test_claim_is_unique_and_duplicate_is_a_no_op(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)

    first = repository.claim(identity())
    duplicate = repository.claim(identity())

    assert first.claimed is True
    assert duplicate == type(duplicate)(claimed=False, state="processing")
    with sessions() as session:
        assert len(session.scalars(select(ProcessedMessage)).all()) == 1


def test_completed_message_cannot_be_completed_twice(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.mark_completed(identity(), target_system="openelis", target_record_id="task-1")

    with pytest.raises(LedgerStateError, match="completed"):
        repository.mark_completed(identity(), target_system="openelis", target_record_id="task-2")


def test_exhausted_message_keeps_payload_in_dead_letter_not_audit(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    payload = {"resourceType": "ServiceRequest", "subject": {"reference": "Patient/1"}}

    repository.move_to_dead_letter(
        identity(), payload=payload, error=TimeoutError("OpenELIS unavailable"), attempts=5
    )

    with sessions() as session:
        message = session.scalar(select(ProcessedMessage))
        dead_letter = session.scalar(select(DeadLetter))
        assert message is not None and message.state == "dead_letter"
        assert dead_letter is not None and dead_letter.payload == payload
        assert dead_letter.error_type == "TimeoutError"
