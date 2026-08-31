"""DH-011E4: authorize a dead-letter retry, without writing to the target and without activating
DH-011E.

This module is the only place that combines the source preflight
(`openmrs.check_source_supersession`) with the ledger's state transition
(`ledger.LedgerRepository.authorize_dead_letter_retry`). It never contacts OpenELIS: the actual
probe-then-write happens later, the next time `OrderPoller.poll_once()` runs, through the
unmodified `RecoveryCoordinator`/DH-025 machinery -- this module's entire job is the source
preflight and the ledger transition, nothing else. See `order_poller.py`'s own module docstring
for why DH-011E stays disabled regardless of what this module does.
"""

from __future__ import annotations

from dataclasses import dataclass

from daher_adapter.ledger import ActorSource, LedgerRepository, MessageIdentity, ResolutionOutcome
from daher_adapter.openmrs import OpenMrsOrderSource, SourcePresence, check_source_supersession


class DeadLetterNotFound(RuntimeError):
    """No dead-lettered source event matches the given identity."""


@dataclass(frozen=True, slots=True)
class DeadLetterRetryOutcome:
    authorized: bool
    presence: SourcePresence
    # Non-PHI, safe for direct CLI display (§11/§17).
    detail: str


_REFUSAL_STATE: dict[SourcePresence, ResolutionOutcome] = {
    SourcePresence.SUPERSEDED: "refused_superseded",
    SourcePresence.AMBIGUOUS: "refused_ambiguous",
    SourcePresence.UNREACHABLE: "refused_unreachable",
}


def authorize_dead_letter_retry(
    identity: MessageIdentity,
    *,
    ledger: LedgerRepository,
    source: OpenMrsOrderSource,
    actor: str,
    actor_uid: int,
    actor_source: ActorSource,
    reason: str,
) -> DeadLetterRetryOutcome:
    """DH-011E4's entire retry-authorization algorithm -- V1 supports exactly one action,
    `retry_after_correction` (SKIP/DISCARD/FORCE-COMPLETE are Human Decision Required and not
    implemented, PROJECT_STATE.md's DH-011E3 §9).

    1. Read the current (latest) dead-letter attempt. Its stored `payload` is what the source
       preflight compares the live OpenMRS resource against (§8's exact-payload invariant); its
       `id` is what the ledger transition re-verifies is still current once it holds the row
       lock, guarding the race between this unlocked read and that locked transaction.
    2. Run the mandatory source preflight (`check_source_supersession`) -- never inside a DB
       transaction, since it is a network call.
    3. `CURRENT` authorizes the retry: the ledger method flips `dead_letter` to `processing`.
       Every other outcome still calls the same ledger method, but only to record the refusal as
       an auditable `DeadLetterResolution` row -- the ledger state never moves, no target is ever
       contacted, and the watermark is untouched either way.
    """
    latest = ledger.latest_dead_letter(identity)
    if latest is None:
        raise DeadLetterNotFound(
            f"no dead-lettered source event matches {identity.resource_type}/"
            f"{identity.resource_id}@{identity.resource_version}"
        )

    preflight = check_source_supersession(identity, latest.payload, source=source)
    resulting_state: ResolutionOutcome = (
        "processing"
        if preflight.presence is SourcePresence.CURRENT
        else _REFUSAL_STATE[preflight.presence]
    )

    ledger.authorize_dead_letter_retry(
        identity,
        dead_letter_id=latest.id,
        action="retry_after_correction",
        reason=reason,
        actor=actor,
        actor_uid=actor_uid,
        actor_source=actor_source,
        resulting_state=resulting_state,
    )
    return DeadLetterRetryOutcome(
        authorized=resulting_state == "processing",
        presence=preflight.presence,
        detail=preflight.detail,
    )
