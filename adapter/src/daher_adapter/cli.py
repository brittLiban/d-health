"""DH-011E4/DH-011E4B: the local facility-host operator CLI for dead-letter retry.

**V1 supports exactly one destructive-adjacent action: `retry`** (`retry_after_correction`).
There is no `skip`, `discard`, or `force-complete` command, and none may be added without a
Human Decision (`AGENTS.md`, PROJECT_STATE.md's DH-011E3 §9) -- `tests/test_cli.py`'s
`test_no_destructive_commands_are_registered` inspects `build_parser()`'s actual registered
subcommands to guard this, not source-tree prose.

**No web endpoint, no new authentication subsystem.** `adapter/src/daher_adapter/app.py` is
unchanged by this ticket -- see its own module docstring. This CLI relies on the facility host's
own OS-access trust boundary, the same one already relied on for `alembic upgrade`/
`docker compose` operations on that host (`DECISIONS.md`'s ADR-033).

**Operator identity is never fabricated, and is never a caller-supplied flag (DH-011E4B).**
`retry` has **no `--actor` argument at all** -- independent review found that accepting one, even
labeled "not verified," was indistinguishable in practice from claiming attribution the adapter
cannot back up. The only source of identity this command accepts is the pair of environment
variables `DAHER_ADAPTER_ACTOR_NAME`/`DAHER_ADAPTER_ACTOR_UID`, both required, both intended to be
set only by `scripts/daher_adapter_admin.py` -- a host-side wrapper that derives them from
`pwd.getpwuid(os.getuid())` on the facility host *before* `docker compose exec` would otherwise
destroy that identity, and passes them through as `-e` flags, never typed by the human running the
command. **The precise trust claim, stated once, applies everywhere this identity is used**:
operator attribution is derived from the authenticated local OS account at the facility-host trust
boundary. A host root/Docker administrator can ultimately tamper with the system; V1 does not
claim tamper-proof attribution against a compromised host administrator. It is not cryptographic
non-repudiation, and nothing in this codebase describes it as such.

**This command never contacts OpenELIS and never runs a poll cycle** (§6/§23). It only reads
OpenMRS (the mandatory source preflight, §7) and transitions the ledger. DH-011E stays disabled:
nothing here starts a scheduler, a recurring task, or `poll_once()`.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from base64 import b64encode
from collections.abc import Mapping
from typing import Any

from daher_adapter.config import ConfigurationError, Settings
from daher_adapter.database import build_engine, build_session_factory
from daher_adapter.dead_letter import DeadLetterNotFound, authorize_dead_letter_retry
from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openmrs import OpenMrsOrderSource, OpenMrsResourceNotFound

ACTOR_NAME_ENV_VAR = "DAHER_ADAPTER_ACTOR_NAME"
ACTOR_UID_ENV_VAR = "DAHER_ADAPTER_ACTOR_UID"


class CliUsageError(RuntimeError):
    """The command as given cannot be honored -- printed to stderr, never a traceback."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daher-adapter", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    dead_letter = subparsers.add_parser(
        "dead-letter", help="Inspect and retry dead-lettered source events."
    )
    dead_letter_sub = dead_letter.add_subparsers(dest="dead_letter_command", required=True)

    dead_letter_sub.add_parser(
        "list", help="List unresolved dead letters (metadata only -- never the payload)."
    )

    retry = dead_letter_sub.add_parser(
        "retry",
        help="Authorize one dead-lettered source event to be retried, after the source "
        "preflight confirms it has not been superseded upstream.",
    )
    retry.add_argument("--resource-type", default="ServiceRequest")
    retry.add_argument("--resource-id", required=True)
    retry.add_argument("--resource-version", required=True)
    retry.add_argument(
        "--direction",
        default="openmrs_to_openelis",
        choices=["openmrs_to_openelis", "openelis_to_openmrs"],
    )
    retry.add_argument("--source-system", default="openmrs", choices=["openmrs"])
    retry.add_argument("--reason", required=True, help="Mandatory, human-readable.")
    # Deliberately no --actor argument (DH-011E4B). See this module's own docstring.
    return parser


def _resolve_actor(environ: Mapping[str, str]) -> tuple[str, int]:
    """The only identity source `retry` accepts: both env vars set by
    `scripts/daher_adapter_admin.py`, never a CLI flag. Refuses clearly, rather than falling back
    to anything caller-supplied, if either is absent or `actor_uid` does not parse as an integer.
    """
    name = environ.get(ACTOR_NAME_ENV_VAR, "").strip()
    raw_uid = environ.get(ACTOR_UID_ENV_VAR, "").strip()
    if not name or not raw_uid:
        raise CliUsageError(
            f"no operator identity available: {ACTOR_NAME_ENV_VAR}/{ACTOR_UID_ENV_VAR} must both "
            f"be set. Run this command through scripts/daher_adapter_admin.py on the facility "
            f"host, which derives them from the host's own OS account -- this command does not "
            f"accept a typed identity."
        )
    try:
        uid = int(raw_uid)
    except ValueError as error:
        raise CliUsageError(
            f"{ACTOR_UID_ENV_VAR}={raw_uid!r} is not a valid integer UID"
        ) from error
    return name, uid


def _omrs_fetch(settings: Settings) -> Any:
    if not settings.omrs_base_url:
        raise ConfigurationError(
            "ADAPTER_OMRS_BASE_URL is required for the dead-letter retry preflight"
        )
    auth_header = None
    if settings.omrs_auth:
        auth_header = "Basic " + b64encode(settings.omrs_auth.encode()).decode()

    def fetch(url: str) -> Mapping[str, Any]:
        headers = {"Authorization": auth_header} if auth_header else {}
        request = urllib.request.Request(url, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise OpenMrsResourceNotFound(f"{url}: 404") from error
            raise

    return fetch


def _cmd_list(ledger: LedgerRepository) -> int:
    summaries = ledger.list_dead_letters()
    if not summaries:
        print("No unresolved dead letters.")
        return 0
    print(
        f"{'RESOURCE_TYPE':<16}{'RESOURCE_ID':<24}{'VERSION':<10}{'DIRECTION':<22}"
        f"{'ERROR_TYPE':<20}{'ATTEMPT':<9}CREATED_AT"
    )
    for summary in summaries:
        print(
            f"{summary.resource_type:<16}{summary.resource_id:<24}{summary.resource_version:<10}"
            f"{summary.direction:<22}{summary.error_type:<20}{summary.sequence:<9}"
            f"{summary.created_at.isoformat()}"
        )
    return 0


def _cmd_retry(
    args: argparse.Namespace,
    ledger: LedgerRepository,
    source: OpenMrsOrderSource,
    environ: Mapping[str, str],
) -> int:
    actor_name, actor_uid = _resolve_actor(environ)
    identity = MessageIdentity(
        source_system=args.source_system,
        direction=args.direction,
        resource_type=args.resource_type,
        resource_id=args.resource_id,
        resource_version=args.resource_version,
    )

    try:
        outcome = authorize_dead_letter_retry(
            identity,
            ledger=ledger,
            source=source,
            actor=actor_name,
            actor_uid=actor_uid,
            actor_source="host_os_wrapper",
            reason=args.reason,
        )
    except DeadLetterNotFound as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if outcome.authorized:
        print(f"AUTHORIZED: retry will be attempted on the next poll cycle. {outcome.detail}")
        return 0
    print(
        f"REFUSED ({outcome.presence.value.upper()}): {outcome.detail}\n"
        f"The event remains dead-lettered; the target was not contacted and the watermark did "
        f"not move. Manual clinical review may be required.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    env = environ if environ is not None else __import__("os").environ
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env(env)
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    engine = build_engine(settings)
    ledger = LedgerRepository(build_session_factory(engine))

    if args.command == "dead-letter" and args.dead_letter_command == "list":
        return _cmd_list(ledger)
    if args.command == "dead-letter" and args.dead_letter_command == "retry":
        try:
            source = OpenMrsOrderSource(settings.omrs_base_url or "", _omrs_fetch(settings))
            return _cmd_retry(args, ledger, source, env)
        except (CliUsageError, ConfigurationError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    parser.error("unknown command")  # pragma: no cover - argparse already enforces this
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
