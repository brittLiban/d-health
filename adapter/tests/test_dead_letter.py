from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.dead_letter import (
    DeadLetterNotFound,
    authorize_dead_letter_retry,
)
from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openmrs import OpenMrsOrderSource, OpenMrsResourceNotFound


def identity(*, version: str = "1") -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id="order-uuid",
        resource_version=version,
    )


def sr(*, version: str = "1", status: str = "active") -> dict[str, Any]:
    return {
        "resourceType": "ServiceRequest",
        "id": "order-uuid",
        "meta": {"versionId": version, "lastUpdated": "2026-08-30T10:00:00.000+03:00"},
        "status": status,
    }


class ScriptedFetch:
    def __init__(self, response: Mapping[str, Any] | Exception) -> None:
        self._response = response

    def __call__(self, url: str) -> Mapping[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def source(response: Mapping[str, Any] | Exception) -> OpenMrsOrderSource:
    return OpenMrsOrderSource("http://openmrs/fhir", ScriptedFetch(response))


def dead_letter_it(sessions: sessionmaker[Session], *, status: str = "active") -> LedgerRepository:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(),
        payload=sr(status=status),
        error=ValueError("bad mapping"),
        attempts=1,
    )
    return repository


def test_authorize_retry_raises_when_never_dead_lettered(sessions: sessionmaker[Session]) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())

    with pytest.raises(DeadLetterNotFound):
        authorize_dead_letter_retry(
            identity(),
            ledger=repository,
            source=source(sr()),
            actor="liban",
            actor_uid=1001,
            actor_source="host_os_wrapper",
            reason="corrected the mapping",
        )


def test_authorize_retry_current_source_flips_ledger_to_processing(
    sessions: sessionmaker[Session],
) -> None:
    repository = dead_letter_it(sessions, status="active")

    outcome = authorize_dead_letter_retry(
        identity(),
        ledger=repository,
        source=source(sr(version="1", status="active")),
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        reason="corrected the test-mapping config",
    )

    assert outcome.authorized is True
    assert repository.get_state(identity()) == "processing"


def test_authorize_retry_superseded_source_refuses_and_stays_dead_letter(
    sessions: sessionmaker[Session],
) -> None:
    repository = dead_letter_it(sessions, status="active")

    outcome = authorize_dead_letter_retry(
        identity(),
        ledger=repository,
        source=source(sr(version="1", status="completed")),  # discontinued upstream
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        reason="clinician cancelled it, checking anyway",
    )

    assert outcome.authorized is False
    assert repository.get_state(identity()) == "dead_letter"


def test_authorize_retry_ambiguous_source_refuses(sessions: sessionmaker[Session]) -> None:
    repository = dead_letter_it(sessions)

    outcome = authorize_dead_letter_retry(
        identity(),
        ledger=repository,
        source=source(OpenMrsResourceNotFound("404")),
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        reason="checking",
    )

    assert outcome.authorized is False
    assert repository.get_state(identity()) == "dead_letter"


def test_authorize_retry_unreachable_source_refuses(sessions: sessionmaker[Session]) -> None:
    repository = dead_letter_it(sessions)

    outcome = authorize_dead_letter_retry(
        identity(),
        ledger=repository,
        source=source(ConnectionError("refused")),
        actor="liban",
        actor_uid=1001,
        actor_source="host_os_wrapper",
        reason="checking",
    )

    assert outcome.authorized is False
    assert repository.get_state(identity()) == "dead_letter"


def test_authorize_retry_never_touches_target_or_watermark(sessions: sessionmaker[Session]) -> None:
    """§6: the operator command's own job stops at the ledger transition. There is no target
    client or watermark repository passed in at all -- this test asserts that by construction:
    `authorize_dead_letter_retry`'s signature has no such parameter to misuse."""
    import inspect

    from daher_adapter.dead_letter import authorize_dead_letter_retry as fn

    params = set(inspect.signature(fn).parameters)
    assert not params & {"deliver", "write_target", "watermarks", "probe_target"}
