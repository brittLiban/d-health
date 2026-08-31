import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.ledger import LedgerRepository, LedgerStateError, MessageIdentity
from daher_adapter.models import DeadLetter, DeadLetterResolution, ProcessedMessage


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


def test_concurrent_claims_have_exactly_one_owner(
    sessions: sessionmaker[Session],
) -> None:
    def claim() -> bool:
        return LedgerRepository(sessions).claim(identity()).claimed

    with ThreadPoolExecutor(max_workers=2) as workers:
        claimed = list(workers.map(lambda _: claim(), range(2)))

    assert sorted(claimed) == [False, True]
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


def _dead_letter_id(sessions: sessionmaker[Session]) -> uuid.UUID:
    with sessions() as session:
        row = session.scalar(select(DeadLetter).order_by(DeadLetter.sequence.desc()))
        assert row is not None
        return row.id


def test_authorize_dead_letter_retry_requires_dead_letter_state(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())  # state is "processing", never dead-lettered

    with pytest.raises(LedgerStateError, match="processing"):
        repository.authorize_dead_letter_retry(
            identity(),
            dead_letter_id=uuid.uuid4(),
            action="retry_after_correction",
            reason="corrected the mapping",
            actor="liban",
            actor_uid=1001,
            actor_source="host_os_wrapper",
            resulting_state="processing",
        )


def test_authorize_dead_letter_retry_requires_reason_and_actor(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload={}, error=ValueError("bad mapping"), attempts=1
    )
    dead_letter_id = _dead_letter_id(sessions)

    with pytest.raises(ValueError, match="reason"):
        repository.authorize_dead_letter_retry(
            identity(),
            dead_letter_id=dead_letter_id,
            action="retry_after_correction",
            reason="   ",
            actor="liban",
            actor_uid=1001,
            actor_source="host_os_wrapper",
            resulting_state="processing",
        )
    with pytest.raises(ValueError, match="actor"):
        repository.authorize_dead_letter_retry(
            identity(),
            dead_letter_id=dead_letter_id,
            action="retry_after_correction",
            reason="corrected the mapping",
            actor="",
            actor_uid=1001,
            actor_source="host_os_wrapper",
            resulting_state="processing",
        )
    with pytest.raises(ValueError, match="actor_uid"):
        repository.authorize_dead_letter_retry(
            identity(),
            dead_letter_id=dead_letter_id,
            action="retry_after_correction",
            reason="corrected the mapping",
            actor="liban",
            actor_uid=-1,
            actor_source="host_os_wrapper",
            resulting_state="processing",
        )


def test_authorize_dead_letter_retry_flips_state_and_writes_append_only_resolution(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    payload = {"resourceType": "ServiceRequest", "subject": {"reference": "Patient/1"}}
    repository.move_to_dead_letter(
        identity(), payload=payload, error=ValueError("bad mapping"), attempts=1
    )
    dead_letter_id = _dead_letter_id(sessions)

    repository.authorize_dead_letter_retry(
        identity(),
        dead_letter_id=dead_letter_id,
        action="retry_after_correction",
        reason="corrected the test-mapping config",
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        resulting_state="processing",
    )

    assert repository.get_state(identity()) == "processing"
    with sessions() as session:
        # The original error evidence is untouched.
        dead_letter = session.scalar(select(DeadLetter))
        assert dead_letter is not None
        assert dead_letter.payload == payload
        assert dead_letter.error_type == "ValueError"
        assert dead_letter.resolved_at is None  # legacy column, still unused

        resolution = session.scalar(select(DeadLetterResolution))
        assert resolution is not None
        assert resolution.dead_letter_id == dead_letter_id
        assert resolution.actor == "liban"
        assert resolution.actor_uid == 1001
        assert resolution.actor_source == "host_os_wrapper"
        assert resolution.reason == "corrected the test-mapping config"
        assert resolution.resulting_state == "processing"


def test_authorize_dead_letter_retry_can_refuse_without_flipping_state(
    sessions: sessionmaker[Session],
) -> None:
    """The source preflight found SUPERSEDED/AMBIGUOUS/UNREACHABLE -- the refusal itself is
    still recorded (auditable), but the ledger never moves."""
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload={}, error=ValueError("bad mapping"), attempts=1
    )
    dead_letter_id = _dead_letter_id(sessions)

    repository.authorize_dead_letter_retry(
        identity(),
        dead_letter_id=dead_letter_id,
        action="retry_after_correction",
        reason="clinician cancelled the order upstream",
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        resulting_state="refused_superseded",
    )

    assert repository.get_state(identity()) == "dead_letter"
    with sessions() as session:
        resolution = session.scalar(select(DeadLetterResolution))
        assert resolution is not None
        assert resolution.resulting_state == "refused_superseded"
        assert resolution.actor_source == "host_os_wrapper"


def test_authorize_dead_letter_retry_refuses_a_stale_dead_letter_id(
    sessions: sessionmaker[Session],
) -> None:
    """Guards a race between an unlocked preflight read and this locked transaction: the
    dead-letter attempt examined is no longer the message's current one."""
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload={}, error=ValueError("bad mapping"), attempts=1
    )

    with pytest.raises(LedgerStateError, match="current attempt"):
        repository.authorize_dead_letter_retry(
            identity(),
            dead_letter_id=uuid.uuid4(),  # not the real (only) dead-letter row's id
            action="retry_after_correction",
            reason="corrected the mapping",
            actor="liban",
            actor_uid=1001,
            actor_source="host_os_wrapper",
            resulting_state="processing",
        )
    assert repository.get_state(identity()) == "dead_letter"


def test_repeated_dead_letter_attempts_keep_every_attempt_and_number_them_in_order(
    sessions: sessionmaker[Session],
) -> None:
    """DH-011E4: a retried event that fails again gets a second row, never overwrites the
    first -- and `latest_dead_letter`/`list_dead_letters` must report the newer one."""
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    first_payload = {"attempt": 1}
    repository.move_to_dead_letter(
        identity(), payload=first_payload, error=ValueError("first failure"), attempts=1
    )
    first_id = _dead_letter_id(sessions)

    repository.authorize_dead_letter_retry(
        identity(),
        dead_letter_id=first_id,
        action="retry_after_correction",
        reason="corrected once",
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        resulting_state="processing",
    )
    second_payload = {"attempt": 2}
    repository.move_to_dead_letter(
        identity(), payload=second_payload, error=RuntimeError("second failure"), attempts=1
    )

    with sessions() as session:
        rows = session.scalars(select(DeadLetter).order_by(DeadLetter.sequence)).all()
        assert [row.sequence for row in rows] == [1, 2]
        assert rows[0].payload == first_payload
        assert rows[0].error_type == "ValueError"
        assert rows[1].payload == second_payload
        assert rows[1].error_type == "RuntimeError"

    latest = repository.latest_dead_letter(identity())
    assert latest is not None
    assert latest.sequence == 2
    assert latest.payload == second_payload

    # A second retry, targeting the now-current (sequence 2) attempt, must succeed -- and using
    # the stale first attempt's id must still be refused even though it is a "real" row.
    with pytest.raises(LedgerStateError, match="current attempt"):
        repository.authorize_dead_letter_retry(
            identity(),
            dead_letter_id=first_id,
            action="retry_after_correction",
            reason="wrong attempt id",
            actor="liban",
            actor_uid=1001,
            actor_source="host_os_wrapper",
            resulting_state="processing",
        )
    repository.authorize_dead_letter_retry(
        identity(),
        dead_letter_id=latest.id,
        action="retry_after_correction",
        reason="corrected the real cause this time",
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        resulting_state="processing",
    )
    assert repository.get_state(identity()) == "processing"
    with sessions() as session:
        resolutions = session.scalars(select(DeadLetterResolution)).all()
        assert len(resolutions) == 2, "both the first and second retry are recorded"


def test_list_dead_letters_never_carries_payload_or_error_summary(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(),
        payload={"resourceType": "ServiceRequest", "subject": {"reference": "Patient/1"}},
        error=ValueError("a message that must never appear in list output"),
        attempts=1,
    )

    summaries = repository.list_dead_letters()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.resource_type == identity().resource_type
    assert summary.resource_id == identity().resource_id
    assert summary.error_type == "ValueError"
    assert summary.sequence == 1
    assert not hasattr(summary, "payload")
    assert not hasattr(summary, "error_summary")


def test_list_dead_letters_only_reports_the_current_attempt(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(identity(), payload={}, error=ValueError("first"), attempts=1)
    first_id = _dead_letter_id(sessions)
    repository.authorize_dead_letter_retry(
        identity(),
        dead_letter_id=first_id,
        action="retry_after_correction",
        reason="corrected",
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        resulting_state="processing",
    )
    repository.move_to_dead_letter(identity(), payload={}, error=RuntimeError("second"), attempts=1)

    summaries = repository.list_dead_letters()

    assert len(summaries) == 1
    assert summaries[0].sequence == 2
    assert summaries[0].error_type == "RuntimeError"
