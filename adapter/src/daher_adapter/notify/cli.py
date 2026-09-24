"""DH-NOTIFY-001 §14 — running the dispatcher.

    python -m daher_adapter.notify.cli run        # poll until interrupted
    python -m daher_adapter.notify.cli run --once # one pass, then exit
    python -m daher_adapter.notify.cli status     # queue state; contacts no provider

**There is no destructive subcommand, and none may be added without a Human Decision**
(``AGENTS.md``). No ``purge``, no ``drop``, no ``reset``, no ``skip``, no ``force-send``. The reason
is the same one ``docs/CONNECT-AGENT.md`` gives for the agent's store, and it is specific: a
``PENDING`` row is a message DHealth owes a patient about a released clinical result, and a
``purge`` on a stuck queue deletes exactly that -- reached for at precisely the moment a stuck queue
makes somebody want to try anything. ``tests/test_patient_notification_dispatcher.py`` inspects the
registered subcommands rather than trusting this paragraph.

``status`` deliberately contacts nothing. An operator diagnosing a provider outage must be able to
read the queue without the diagnosis itself depending on the thing that is broken.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from collections.abc import Sequence

from daher_adapter.config import Settings
from daher_adapter.database import build_engine, build_session_factory
from daher_adapter.logging import configure_logging
from daher_adapter.notify.dispatcher import (
    DEFAULT_POLL_SECONDS,
    PatientNotificationDispatcher,
)
from daher_adapter.notify.repository import PatientNotificationRepository

logger = logging.getLogger("daher_adapter.notify.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dhealth-patient-notifier",
        description="Drain the DHealth patient-notification queue.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="claim and send due notifications")
    run.add_argument(
        "--once", action="store_true", help="make a single pass and exit instead of polling"
    )
    run.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help=f"seconds between passes (default: {DEFAULT_POLL_SECONDS})",
    )

    sub.add_parser("status", help="print queue state; contacts no provider")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)

    settings = Settings.from_env()
    engine = build_engine(settings)
    sessions = build_session_factory(engine)

    if args.command == "status":
        with sessions() as session:
            summary = PatientNotificationRepository().operational_summary(session)
        summary["enabled"] = settings.notifications_enabled
        summary["provider"] = (
            settings.notification_provider if settings.notifications_enabled else None
        )
        summary["receipts_configured"] = settings.notification_receipts_configured
        # DH-NOTIFY-META-001 §3: the pinned Graph API version, visible without shell access
        # to the container environment -- which is where the access token also lives, so the
        # safe way to ask which API this deployment talks to has to be this one.
        summary["graph_api_version"] = (
            settings.meta_graph_api_version if settings.notifications_enabled else None
        )
        # stdout, as data. No destination, no message id, no body -- the same safe projection the
        # operator HTTP read uses, for the same reason.
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    dispatcher = PatientNotificationDispatcher(
        sessions, transport=settings.build_notification_transport()
    )
    if not dispatcher.enabled:
        # Exit 0, not an error. "Notifications are disabled" is a deliberate deployment state (§10),
        # and a non-zero exit would make every development stack's supervisor report a failure for a
        # setting somebody chose on purpose.
        logger.info("patient_notifications_disabled")
        return 0

    if args.once:
        outcome = dispatcher.dispatch_once()
        logger.info(
            "patient_notification_pass_complete",
            extra={
                "claimed": outcome.claimed,
                "sent": outcome.sent,
                "failed": outcome.failed,
                "errored": outcome.errored,
            },
        )
        return 0

    stop = threading.Event()

    def _handle(signum, _frame) -> None:  # noqa: ANN001 - signal handler signature
        logger.info("patient_notification_dispatcher_stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    dispatcher.run(stop=stop, poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
