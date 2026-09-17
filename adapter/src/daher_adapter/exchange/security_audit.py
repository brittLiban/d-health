"""DH-EX-002R: the authoritative, transactional audit record for Exchange security mutations.

`ExchangeSecurityAuditRepository.record` never opens or commits its own transaction -- it only
`session.add()`s a row onto a `Session` the caller already owns and is going to commit. This is
deliberate: the whole point of this table is that its row and the mutation it describes commit or
roll back together, in one database transaction, never as two separate writes with a gap between
them. `exchange/security_audit.py`'s only caller is `exchange/api.py`'s mutation handlers, which
own that transaction boundary explicitly (see `run_in_transaction` there).

This is a different mechanism from `exchange/audit.py`'s `ExchangeAuditWriter`, on purpose: that
file-based writer remains for authorization *refusals*, which are not state mutations and must
never make access denial depend on a database write succeeding. This table is for mutations only.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy.orm import Session

from daher_adapter.exchange.models import ExchangeSecurityAuditEvent

SecurityEventType = Literal[
    "organization_created",
    "membership_created",
    "role_assigned",
    # DH-EX-003 catalog/directory/offering/price mutation events.
    "catalog_entry_created",
    "lab_directory_profile_created",
    "lab_directory_profile_updated",
    "lab_offering_created",
    "lab_offering_status_changed",
    "lab_price_changed",
    # DH-EX-004 provider lab-order creation.
    "lab_order_created",
    # DH-EX-005 patient access grant lifecycle.
    "patient_access_issued",
    "patient_access_revoked",
    # DH-EX-006 selected-lab assigned-order workflow.
    "lab_order_accepted",
    "lab_order_rejected",
    "lab_order_processing_started",
    # DH-EX-007 lab result recording, order completion, and doctor review. All three reference a
    # LabOrder id (`resource_type="lab_order"`) -- audit stays order-centric, never per-item.
    # `lab_result_recorded` is RETIRED as of DH-EX-012C -- kept in this CHECK constraint only
    # because real historical rows already carry it (narrowing the constraint would require
    # proving no such row exists, an unnecessary destructive-adjacent step for a purely additive
    # ticket); no code path writes a new one. `lab_observation_recorded`/
    # `lab_result_entry_completed` below are its replacements.
    "lab_result_recorded",
    "lab_order_completed",
    "doctor_review_recorded",
    # DH-EX-009 provider-scoped patient-reference binding. Deliberately not "patient_created" --
    # Exchange binds a source-owned identity, it does not create a longitudinal patient record.
    "patient_reference_bound",
    # DH-EX-010 provider/facility-assigned lab routing (ADR-043 launch option (a)). Deliberately
    # not "lab_selected" -- that name is DH-EX-005's patient price-consent workflow; this is a
    # distinct clinical routing decision with no price/consent step, and reuses the existing
    # "lab_order" resource_type since it mutates the LabOrder's own selection, same as
    # lab_order_accepted/rejected/processing_started above.
    "lab_order_assigned",
    # DH-EX-011 order-level sample/specimen collection workflow milestone. Reuses "lab_order" as
    # resource_type -- this event records a fact about the LabOrder's workflow, not a new kind of
    # resource, matching lab_order_assigned/accepted/rejected/processing_started's own convention.
    "lab_sample_collection_recorded",
    # DH-EX-012/ADR-044: explicit order-level result release. Reuses "lab_order" as resource_type
    # for the identical reason -- a fact about the LabOrder's workflow, not a new resource kind.
    # `lab_order_completed` is still written in the same transaction for the actual COMPLETED
    # transition (moved here from result entry -- see PROJECT_STATE.md's DH-EX-012 entry for the
    # actor semantic change this causes: the actor is now the releaser, not the entering tech).
    "lab_results_released",
    # DH-EX-012C/ADR-047: the multi-observation result domain. `lab_observation_recorded` fires
    # once per recorded `LabObservation` -- both the granular multi-observation endpoint and the
    # backward-compatible single-observation convenience path write it. `lab_result_entry_completed`
    # fires once per ordered item whose result entry is explicitly marked complete (never on an
    # idempotent replay -- see `LabResultRepository.complete_entry`). Both reuse
    # `resource_type="lab_order"`, matching every other per-item-but-order-scoped event above --
    # audit stays order-centric, never per-item/per-observation, unchanged from DH-EX-007's own
    # convention.
    "lab_observation_recorded",
    "lab_result_entry_completed",
    # DH-EX-013/ADR-045 (pre-release correction only): `lab_observation_corrected` fires once per
    # correction -- one immutable new `LabObservation` row superseding exactly one prior effective
    # one. Reuses `resource_type="lab_order"`, same convention as every event above; the mutation's
    # own audit atomicity requirement (correction + supersession + this event, one transaction) is
    # exactly `lab_result_entry_completed`'s own established discipline, not a new pattern.
    "lab_observation_corrected",
    # DH-EX-014B/ADR-048: post-release amendment. All three reuse `resource_type="lab_order"`,
    # identical convention to every event above -- audit stays order-centric, never per-amendment-
    # episode as its own resource kind. `lab_result_amendment_started` fires once per amendment
    # creation; `lab_observation_amended` fires once per amendment-scoped correction (the
    # `lab_observation_corrected` sibling for post-release, not pre-release, revisions);
    # `lab_result_amendment_released` fires once per successful (non-replay) amendment release,
    # alongside the new `LabResultReleaseVersion`/manifest/`DoctorReview` creation it authorizes --
    # no separate `doctor_review_recorded`-shaped event for the new PENDING row, mirroring how the
    # very first PENDING row at order-creation time is never separately audited either (implicit in
    # `lab_order_created`); creating a fresh unreviewed obligation is not itself a review being
    # recorded.
    "lab_result_amendment_started",
    "lab_observation_amended",
    "lab_result_amendment_released",
    # DH-IDENTITY-001: native provider-scoped Patient directory registration.
    "patient_created",
]
SecurityOutcome = Literal["created", "returned_existing", "updated", "unchanged"]
SecurityResourceType = Literal[
    "catalog_entry",
    "lab_directory_profile",
    "lab_offering",
    "lab_price",
    "lab_order",
    "patient_access_grant",
    "patient_reference",
    "patient",
]


class ExchangeSecurityAuditRepository:
    def record(
        self,
        session: Session,
        *,
        event_type: SecurityEventType,
        actor_identity_id: uuid.UUID,
        outcome: SecurityOutcome,
        organization_id: uuid.UUID | None = None,
        subject_identity_id: uuid.UUID | None = None,
        membership_id: uuid.UUID | None = None,
        role: str | None = None,
        resource_type: SecurityResourceType | None = None,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        # DH-EX-003: `organization_id` is optional -- a platform-level catalog-entry creation has
        # no single owning organization, unlike every DH-EX-002 event type (which must still pass
        # one; nothing here relaxes that at the caller level, only the column's own nullability).
        session.add(
            ExchangeSecurityAuditEvent(
                event_type=event_type,
                actor_identity_id=actor_identity_id,
                organization_id=organization_id,
                subject_identity_id=subject_identity_id,
                membership_id=membership_id,
                role=role,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
            )
        )
