"""DH-CONNECT-AGENT-001 §71 — ``dhealth-connect-agent run|status``. Two commands, both safe.

The shape follows ``daher_adapter/cli.py``'s precedent exactly, and for the same reason it was
adopted there: **no destructive subcommand exists, and none may be added without a Human Decision**
(``AGENTS.md``). There is no ``purge``, no ``drop``, no ``reset``, no ``skip`` and no
``force-deliver``. ``tests/test_connect_agent.py`` inspects the registered subcommands rather than
trusting this paragraph, because prose does not fail a build.

The reason is specific rather than cautious. This agent's store holds undelivered clinical messages
that exist nowhere else on the partner's premises -- a ``purge`` command on a stuck queue is a
command that deletes a laboratory order somebody is waiting for, and it would be run at exactly the
moment a stuck queue makes someone want to try anything. §51 and §56 both say the same thing: queued
work is never deleted, and unresolved is a better state than invented.

``status`` reads and prints. It contacts nothing, needs no credential to be valid, and never prints
the token (see ``AgentSettings.redacted``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Sequence

from daher_adapter.connect.contract import CONTRACT_VERSION
from daher_adapter.connect_agent import AGENT_VERSION
from daher_adapter.connect_agent.adapter import ConnectorAdapter, SyntheticLabAdapter
from daher_adapter.connect_agent.client import ConnectRuntimeClient, TokenSource
from daher_adapter.connect_agent.config import AgentConfigurationError, AgentSettings
from daher_adapter.connect_agent.runner import AgentRunner
from daher_adapter.connect_agent.store import AgentStore, AgentStoreTooNew

logger = logging.getLogger("daher_adapter.connect_agent.cli")

#: Every subcommand this program will ever have unless a human decides otherwise. The guard test
#: asserts equality against this set, so adding one here is a deliberate, reviewable act.
COMMANDS = ("run", "status")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dhealth-connect-agent",
        description=(
            "DHealth Connect agent. Runs on a partner's own hardware, delivers DHealth orders "
            "into their system and their events back. No destructive command exists."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the delivery loop until interrupted")
    run.add_argument(
        "--once",
        action="store_true",
        help="run a single cycle and exit, for operator verification after installation",
    )

    sub.add_parser("status", help="print local queue state and configuration; contacts nothing")
    return parser


def main(argv: Sequence[str] | None = None, *, adapter: ConnectorAdapter | None = None) -> int:
    """Entry point. ``adapter`` is injectable so tests drive the real CLI without a real LIS.

    The default is ``SyntheticLabAdapter`` because it is the only implementation that exists --
    stated in the startup banner rather than left implicit, since an operator seeing "agent started"
    with no further qualification would reasonably conclude their LIS was connected (§82).
    """
    args = build_parser().parse_args(argv)
    try:
        settings = AgentSettings.from_env()
    except AgentConfigurationError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2

    try:
        store = AgentStore(settings.store_path)
    except AgentStoreTooNew as error:
        print(f"refusing to start: {error}", file=sys.stderr)
        return 2

    try:
        if args.command == "status":
            return _status(settings, store)
        return _run(settings, store, adapter=adapter or SyntheticLabAdapter(), once=args.once)
    finally:
        store.close()


def _status(settings: AgentSettings, store: AgentStore) -> int:
    """Local state only. No network call, so it works precisely when things are worst."""
    print(
        json.dumps(
            {
                "agent_version": AGENT_VERSION,
                "contract_version": CONTRACT_VERSION,
                "runtime_instance_id": store.runtime_instance_id(),
                "schema_version": store.schema_version,
                "queue_depth": store.queue_depth(),
                "undeliverable": store.undeliverable_count(),
                "configuration": settings.redacted(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _run(
    settings: AgentSettings, store: AgentStore, *, adapter: ConnectorAdapter, once: bool
) -> int:
    client = ConnectRuntimeClient(base_url=settings.server_url, tokens=TokenSource(settings.token))
    runner = AgentRunner(
        store=store,
        client=client,
        adapter=adapter,
        organization_id=settings.organization_id,
        installation_id=settings.installation_id,
        source_system=settings.source_system,
    )
    logger.info(
        "connect_agent_started",
        extra={
            "agent_version": AGENT_VERSION,
            "runtime_instance_id": runner.runtime_instance_id,
            "adapter": type(adapter).__name__,
        },
    )
    if isinstance(adapter, SyntheticLabAdapter):
        # §82. An operator must not be able to read "agent started" as "our LIS is connected".
        print(
            "WARNING: running with the synthetic adapter. No partner system is connected; "
            "this configuration is for qualification and verification only.",
            file=sys.stderr,
        )

    try:
        while True:
            report = runner.run_once()
            logger.info(
                "connect_agent_cycle",
                extra={
                    "health": report.heartbeat_health,
                    "claimed": report.claimed,
                    "handled": report.handled,
                    "submitted": report.submitted,
                    "failed": report.failed,
                    "errors": len(report.errors),
                },
            )
            if once:
                return 0
            time.sleep(settings.poll_seconds)
    except KeyboardInterrupt:
        # An operator stopping the agent is not a failure, and nothing is lost: every message is
        # either acknowledged or still durably held on one side or the other.
        logger.info("connect_agent_stopped")
        return 0
    finally:
        client.close()


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
