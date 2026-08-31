#!/usr/bin/env python3
"""DH-011E4B: the host-side wrapper that gives `daher_adapter.cli` a trustworthy operator
identity.

**Why this exists.** `docker compose exec` runs the containerized CLI as the fixed `adapter`
service account (`Dockerfile`'s `USER adapter`, UID 10001) regardless of which real human ran the
`docker compose exec` command -- the host operator's own OS identity is otherwise destroyed at
that boundary. This script runs *before* that boundary, on the facility host itself, derives the
invoking operator's real OS account name and numeric UID via `pwd.getpwuid(os.getuid())`, and
passes both into the container as environment variables the CLI reads
(`DAHER_ADAPTER_ACTOR_NAME`/`DAHER_ADAPTER_ACTOR_UID`) -- never as a typed `--actor` flag, which
`daher_adapter.cli`'s `retry` subcommand does not even accept.

**The trust claim, stated precisely, once:** operator attribution is derived from the
authenticated local OS account at the facility-host trust boundary. A host root/Docker
administrator can ultimately tamper with the system; V1 does not claim tamper-proof attribution
against a compromised host administrator. It is not cryptographic non-repudiation.

**Safety properties, by construction, not by convention:**

- Every invocation of `docker`/`compose`/the CLI module path is a fixed, hardcoded list element --
  never built from environment input, never templated from a config file this script reads.
- The operator's own arguments (`--reason`, `--resource-id`, anything else) are forwarded as
  individual `list[str]` elements straight through to `subprocess.run`, which execs `docker`
  directly. **No shell is ever invoked** (`shell=False`, the `subprocess` default, is never
  overridden) -- a reason containing quotes, `;`, a newline, `$(...)`, or backticks reaches
  `daher_adapter.cli`'s own `argparse` as a literal string value, never as something a shell could
  interpret. `tests/test_daher_adapter_admin.py` proves this directly against a set of hostile
  strings, asserting the built argv contains them byte-for-byte and that no shell metacharacter
  ever needs escaping because none is ever parsed.
- No secrets are read, held, or printed by this script. No clinical payload is ever passed through
  it (the CLI's own `list`/`retry` output is metadata-only, unchanged by this wrapper).
- No remote endpoint: `docker compose exec` targets the local Docker daemon only.
- No direct target write: this script's only job is to exec the existing CLI, which itself only
  ever transitions the ledger (see `cli.py`'s and `dead_letter.py`'s own module docstrings).

Usage (run on the facility host, in the directory containing `docker-compose.yml`)::

    ./daher_adapter_admin.py dead-letter list
    ./daher_adapter_admin.py dead-letter retry --resource-id <id> --resource-version <v> \\
        --reason "corrected the OpenELIS site code and re-authorized"
"""

from __future__ import annotations

import os
import pwd
import subprocess  # noqa: S404 - list-argv only, shell=False always, see module docstring
import sys

ACTOR_NAME_ENV_VAR = "DAHER_ADAPTER_ACTOR_NAME"
ACTOR_UID_ENV_VAR = "DAHER_ADAPTER_ACTOR_UID"

# Fixed, not configurable from any environment variable or file this script reads -- "fixed local
# Compose/service target" per this ticket's own requirement.
_COMPOSE_SERVICE = "adapter"
_CLI_MODULE = "daher_adapter.cli"


def derive_host_actor() -> tuple[str, int]:
    """The host OS account actually invoking this script -- never a value the caller can override
    on the command line; there is no flag for it."""
    uid = os.getuid()
    name = pwd.getpwuid(uid).pw_name
    return name, uid


def build_exec_argv(actor_name: str, actor_uid: int, cli_args: list[str]) -> list[str]:
    """Pure, directly testable: no subprocess, no I/O. Every element is either a fixed literal or
    an unmodified pass-through of `actor_name`/`actor_uid`/`cli_args` -- nothing is ever
    concatenated into a single string that a shell could later re-parse."""
    return [
        "docker",
        "compose",
        "exec",
        "-e",
        f"{ACTOR_NAME_ENV_VAR}={actor_name}",
        "-e",
        f"{ACTOR_UID_ENV_VAR}={actor_uid}",
        _COMPOSE_SERVICE,
        "python",
        "-m",
        _CLI_MODULE,
        *cli_args,
    ]


def main(argv: list[str] | None = None) -> int:
    cli_args = list(argv if argv is not None else sys.argv[1:])
    actor_name, actor_uid = derive_host_actor()
    exec_argv = build_exec_argv(actor_name, actor_uid, cli_args)
    # shell=False (the default, never overridden): `docker` is executed directly, so every
    # element of exec_argv -- including anything from cli_args -- reaches it as literal argv,
    # never as text a shell could interpret.
    result = subprocess.run(exec_argv, check=False)  # noqa: S603
    return result.returncode


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
