"""DH-011E4B: `scripts/daher_adapter_admin.py` is not part of `daher_adapter` (it runs on the
facility host, outside any container, and ships nothing beyond the Python standard library) so it
is loaded directly from its file path rather than via `PYTHONPATH` -- deliberately not mirroring
the image's `PYTHONPATH=/app/src`, which this script is never copied into.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "daher_adapter_admin.py"
_SPEC = importlib.util.spec_from_file_location("daher_adapter_admin", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
daher_adapter_admin = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(daher_adapter_admin)


# Every string a shell would treat specially, if one were ever involved -- it must not be.
HOSTILE_REASONS = [
    'has "double quotes" in it',
    "has 'single quotes' in it",
    "semicolon; rm -rf /",
    "newline\ncontinues here",
    "command substitution $(whoami)",
    "backticks `whoami` here",
    "pipe | cat /etc/passwd",
    "ampersand && echo pwned",
    "redirect > /tmp/pwned",
]


def test_build_exec_argv_is_fixed_docker_compose_exec_prefix() -> None:
    argv = daher_adapter_admin.build_exec_argv("alice", 1001, ["dead-letter", "list"])

    assert argv == [
        "docker",
        "compose",
        "exec",
        "-e",
        "DAHER_ADAPTER_ACTOR_NAME=alice",
        "-e",
        "DAHER_ADAPTER_ACTOR_UID=1001",
        "adapter",
        "python",
        "-m",
        "daher_adapter.cli",
        "dead-letter",
        "list",
    ]


def test_build_exec_argv_never_calls_shell() -> None:
    """Proven at the call site, not just asserted: patches subprocess.run and inspects the exact
    kwargs `main()` uses, confirming `shell` is never set to True (the default, `False`, is what
    keeps every argv element literal -- see the module's own docstring)."""
    with patch.object(daher_adapter_admin, "derive_host_actor", return_value=("alice", 1001)):
        with patch.object(daher_adapter_admin.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            daher_adapter_admin.main(["dead-letter", "list"])

    args, kwargs = mock_run.call_args
    assert kwargs.get("shell", False) is False
    assert isinstance(args[0], list), "the command must be a list, never a pre-joined string"


def test_actor_env_vars_carry_the_derived_identity_unmodified() -> None:
    argv = daher_adapter_admin.build_exec_argv("alice", 1001, ["dead-letter", "list"])

    assert "DAHER_ADAPTER_ACTOR_NAME=alice" in argv
    assert "DAHER_ADAPTER_ACTOR_UID=1001" in argv


def test_hostile_reason_strings_pass_through_as_literal_argv_elements() -> None:
    for reason in HOSTILE_REASONS:
        argv = daher_adapter_admin.build_exec_argv(
            "alice", 1001, ["dead-letter", "retry", "--resource-id", "x", "--reason", reason]
        )
        # The exact string appears as its OWN argv element -- never split, never re-quoted, never
        # partially consumed by anything that would imply a shell parsed it.
        assert reason in argv
        assert argv.count(reason) == 1


def test_hostile_reason_never_reaches_a_shell(monkeypatch) -> None:  # noqa: ANN001
    """End-to-end version of the previous test: run through `main()` with a mocked
    `subprocess.run` and confirm every hostile string is still one, unmodified, unexecuted list
    element -- not concatenated into anything a shell could later interpret."""
    captured: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN201
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=argv, returncode=0)

    with patch.object(daher_adapter_admin, "derive_host_actor", return_value=("alice", 1001)):
        with patch.object(daher_adapter_admin.subprocess, "run", side_effect=fake_run):
            for reason in HOSTILE_REASONS:
                daher_adapter_admin.main(
                    ["dead-letter", "retry", "--resource-id", "x", "--reason", reason]
                )
                assert reason in captured["argv"]
                assert captured["kwargs"].get("shell", False) is False


def test_derive_host_actor_uses_real_os_identity(monkeypatch) -> None:  # noqa: ANN001
    """Not mocked here -- confirms `derive_host_actor` actually calls into `os`/`pwd`, so a
    future refactor cannot silently swap in a caller-suppliable value without this test noticing
    the real functions stopped being used."""
    import os
    import pwd

    name, uid = daher_adapter_admin.derive_host_actor()

    assert uid == os.getuid()
    assert name == pwd.getpwuid(os.getuid()).pw_name


def test_main_returns_the_subprocess_exit_code() -> None:
    with patch.object(daher_adapter_admin, "derive_host_actor", return_value=("alice", 1001)):
        with patch.object(daher_adapter_admin.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=2)
            code = daher_adapter_admin.main(["dead-letter", "retry"])

    assert code == 2
