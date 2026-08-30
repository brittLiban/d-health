"""DH-011G2 (LIBAN DECISION: SELECT OPTION D): crash-safe reconciliation for Patient creation
under the OpenMRS-Patient-UUID identity contract.

Deliberately NOT `RecoveryCoordinator.reconcile` (same reasoning `cancellation.py`'s module
docstring already gives for its own coordinator): that method derives its own `SourceCorrelation`
internally, as `source_correlation(identity)` (a sha256 digest of the *event* identity, always
under `SOURCE_CORRELATION_SYSTEM`). That is exactly right for a ServiceRequest, whose correlation
*is* a fresh per-event digest -- but a Patient's identity is the OpenMRS Patient UUID itself, a
value that recurs across every order that patient ever generates, not something to derive from
the create event's own identity. This coordinator accepts that target correlation explicitly,
the same shape `DaherCancellationCoordinator.reconcile_cancellation` already uses for its own
"target differs from the recovering event" case.

Unlike cancellation, Patient's presence model fits `recovery.py`'s existing `TargetPresence`/
`TargetProbeResult`/`RecoveryAction`/`RecoveryResult` vocabulary exactly (FOUND/ABSENT/AMBIGUOUS,
reuse-existing-or-create-if-absent) -- no new outcome enum needed here, unlike
`CancellationOutcome`.
"""

from __future__ import annotations

from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.recovery import (
    AmbiguousTargetState,
    DeadLetterRecoveryBlocked,
    RecoveryAction,
    RecoveryFence,
    RecoveryResult,
    RecoveryStateError,
    SourceCorrelation,
    TargetPresence,
    TargetProbe,
    TargetWriter,
)


class DaherPatientIdentityCoordinator:
    """Mirrors `RecoveryCoordinator`'s crash-safety shape (durable claim -> probe -> write only
    if absent -> mark_completed), with an explicit target correlation instead of one derived
    from the event identity -- see this module's docstring for why.

    `identity` identifies the *patient-linkage event* -- its own durable ledger claim, its own
    idempotent-replay identity (e.g. keyed off the OpenMRS order/encounter that first needed this
    patient). `target` is the OpenMRS Patient UUID correlation (`PATIENT_IDENTITY_SYSTEM`) itself.
    The two are related but distinct for the same reason DH-011J's cancellation coordinator keeps
    them distinct: collapsing them would make this coordinator unable to tell "a replay of the
    same linkage event" from "a different event that happens to need the same patient."
    """

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    async def reconcile_patient(
        self,
        identity: MessageIdentity,
        target: SourceCorrelation,
        *,
        fence: RecoveryFence,
        probe_target: TargetProbe,
        write_target: TargetWriter,
    ) -> RecoveryResult:
        if not fence.held:
            raise RecoveryStateError("exclusive startup recovery fence is not held")

        state = self._ledger.get_state(identity)
        if state is None:
            raise RecoveryStateError("patient-linkage event has no durable claim to recover")
        if state == "dead_letter":
            raise DeadLetterRecoveryBlocked(
                "dead-lettered patient-linkage event requires operator review"
            )
        if state == "completed":
            raise RecoveryStateError(
                "completed patient-linkage event is a normal duplicate, not a recovery candidate"
            )

        probe = await probe_target(target)
        if probe.presence is TargetPresence.AMBIGUOUS:
            raise AmbiguousTargetState(
                "patient identity lookup could not prove presence or absence"
            )

        if probe.presence is TargetPresence.FOUND:
            record = probe.record
            if record is None:  # Defensive; TargetProbeResult validates this at construction.
                raise RecoveryStateError("found target probe omitted its record")
            action = RecoveryAction.RECONCILED_EXISTING
        else:
            # write_target (OpenElisClient.create_openelis_patient) never trusts its own POST's
            # status and reconciles internally -- it raises AmbiguousTargetState itself on a
            # non-ONE outcome, so nothing further to check here on that axis.
            record = await write_target(target)
            action = RecoveryAction.RETRIED_AFTER_PROVEN_ABSENCE

        self._ledger.mark_completed(
            identity, target_system=record.system, target_record_id=record.record_id
        )
        return RecoveryResult(action=action, correlation=target, target=record)
