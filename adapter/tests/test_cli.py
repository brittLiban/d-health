from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.cli import (
    ACTOR_NAME_ENV_VAR,
    ACTOR_UID_ENV_VAR,
    CliUsageError,
    _cmd_list,
    _cmd_retry,
    _resolve_actor,
    build_parser,
)
from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openmrs import OpenMrsOrderSource

# Every executable operator action this CLI could ever register. If a future change adds one of
# these, this list -- not the guard test -- is what must change, and only after a Human Decision
# (AGENTS.md; PROJECT_STATE.md's DH-011E3 §9).
_FORBIDDEN_COMMAND_NAMES = {"skip", "discard", "force-complete", "force_complete", "mark-completed"}


def identity() -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id="order-uuid",
        resource_version="1",
    )


def sr(*, version: str = "1", status: str = "active") -> dict[str, Any]:
    return {
        "resourceType": "ServiceRequest",
        "id": "order-uuid",
        "meta": {"versionId": version, "lastUpdated": "2026-08-30T10:00:00.000+03:00"},
        "status": status,
    }


def source(response: Mapping[str, Any]) -> OpenMrsOrderSource:
    return OpenMrsOrderSource("http://openmrs/fhir", lambda url: response)


def _registered_subcommand_names(parser) -> set[str]:  # noqa: ANN001
    """Walks argparse's own subparser registry -- not the source tree -- exactly what §21 asks
    for. Control-tested below by registering a fake destructive command and asserting the check
    actually fires."""
    names: set[str] = set()
    if parser._subparsers is None:  # noqa: SLF001
        return names
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        for choice_name, subparser in action.choices.items():
            names.add(choice_name)
            names |= _registered_subcommand_names(subparser)
    return names


def test_no_destructive_commands_are_registered() -> None:
    parser = build_parser()

    registered = _registered_subcommand_names(parser)

    assert registered & _FORBIDDEN_COMMAND_NAMES == set()
    assert registered == {"dead-letter", "list", "retry"}


def test_guard_control_test_catches_a_fake_destructive_command() -> None:
    """Proves the guard actually inspects the registry rather than trivially passing -- register
    a fake `skip` subcommand and confirm the same check used above would catch it."""
    parser = build_parser()
    dead_letter_action = next(
        action
        for action in parser._subparsers._group_actions  # noqa: SLF001
        if "dead-letter" in action.choices
    )
    dead_letter_parser = dead_letter_action.choices["dead-letter"]
    dead_letter_sub = next(
        action
        for action in dead_letter_parser._subparsers._group_actions  # noqa: SLF001
    )
    dead_letter_sub.choices["skip"] = argparse.ArgumentParser()  # minimal fake leaf registration

    registered = _registered_subcommand_names(parser)

    assert registered & _FORBIDDEN_COMMAND_NAMES == {"skip"}


def test_retry_has_no_actor_flag() -> None:
    """DH-011E4B: operator identity must never be a caller-suppliable CLI flag -- confirmed by
    inspecting argparse's own option strings, not by trying to invoke `--actor` and hoping it
    fails."""
    parser = build_parser()
    dead_letter_action = next(
        action
        for action in parser._subparsers._group_actions  # noqa: SLF001
        if "dead-letter" in action.choices
    )
    retry_parser = dead_letter_action.choices["dead-letter"]
    retry_sub_action = next(
        action
        for action in retry_parser._subparsers._group_actions  # noqa: SLF001
    )
    retry = retry_sub_action.choices["retry"]

    option_strings = {s for action in retry._actions for s in action.option_strings}  # noqa: SLF001

    assert "--actor" not in option_strings


def test_resolve_actor_reads_both_env_vars() -> None:
    name, uid = _resolve_actor({ACTOR_NAME_ENV_VAR: "alice", ACTOR_UID_ENV_VAR: "1001"})

    assert (name, uid) == ("alice", 1001)


def test_resolve_actor_refuses_when_name_missing() -> None:
    with pytest.raises(CliUsageError, match="no operator identity"):
        _resolve_actor({ACTOR_UID_ENV_VAR: "1001"})


def test_resolve_actor_refuses_when_uid_missing() -> None:
    with pytest.raises(CliUsageError, match="no operator identity"):
        _resolve_actor({ACTOR_NAME_ENV_VAR: "alice"})


def test_resolve_actor_refuses_when_neither_is_present() -> None:
    with pytest.raises(CliUsageError, match="no operator identity"):
        _resolve_actor({})


def test_resolve_actor_refuses_a_non_integer_uid() -> None:
    with pytest.raises(CliUsageError, match="not a valid integer"):
        _resolve_actor({ACTOR_NAME_ENV_VAR: "alice", ACTOR_UID_ENV_VAR: "not-a-number"})


def test_list_output_never_contains_payload_fields(
    sessions: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(),
        payload={
            "resourceType": "ServiceRequest",
            "subject": {"reference": "Patient/1", "display": "Jane Doe"},
        },
        error=ValueError("a message that must never appear"),
        attempts=1,
    )

    exit_code = _cmd_list(repository)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Jane" not in output
    assert "Patient" not in output
    assert "must never appear" not in output
    assert "ServiceRequest" in output  # resource type is metadata, fine to show
    assert "order-uuid" in output


def test_list_reports_no_unresolved_dead_letters(
    sessions: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    repository = LedgerRepository(sessions)

    exit_code = _cmd_list(repository)

    assert exit_code == 0
    assert "No unresolved dead letters" in capsys.readouterr().out


def test_retry_current_source_authorizes(
    sessions: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload=sr(status="active"), error=ValueError("bad mapping"), attempts=1
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "dead-letter",
            "retry",
            "--resource-id",
            "order-uuid",
            "--resource-version",
            "1",
            "--reason",
            "corrected the mapping",
        ]
    )
    environ = {ACTOR_NAME_ENV_VAR: "liban", ACTOR_UID_ENV_VAR: "1001"}

    exit_code = _cmd_retry(args, repository, source(sr(status="active")), environ)

    assert exit_code == 0
    assert repository.get_state(identity()) == "processing"
    assert "AUTHORIZED" in capsys.readouterr().out


def test_retry_superseded_source_refuses(
    sessions: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload=sr(status="active"), error=ValueError("bad mapping"), attempts=1
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "dead-letter",
            "retry",
            "--resource-id",
            "order-uuid",
            "--resource-version",
            "1",
            "--reason",
            "checking anyway",
        ]
    )
    environ = {ACTOR_NAME_ENV_VAR: "liban", ACTOR_UID_ENV_VAR: "1001"}

    exit_code = _cmd_retry(args, repository, source(sr(status="completed")), environ)

    assert exit_code == 2
    assert repository.get_state(identity()) == "dead_letter"
    stderr = capsys.readouterr().err
    assert "REFUSED" in stderr
    assert "SUPERSEDED" in stderr


def test_retry_refuses_without_operator_identity(sessions: sessionmaker[Session]) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(), payload=sr(status="active"), error=ValueError("bad mapping"), attempts=1
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "dead-letter",
            "retry",
            "--resource-id",
            "order-uuid",
            "--resource-version",
            "1",
            "--reason",
            "corrected the mapping",
        ]
    )

    with pytest.raises(CliUsageError):
        _cmd_retry(args, repository, source(sr(status="active")), {})

    # Refusal happens before any ledger contact.
    assert repository.get_state(identity()) == "dead_letter"
