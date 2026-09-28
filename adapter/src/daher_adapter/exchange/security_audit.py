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
    # DH-CLINIC-006: platform-admin configuration of a provider's DHealth lab routing. Separate
    # from `lab_order_assigned` above -- that records a clinician's order-level lab assignment;
    # these record the platform-level relationship that makes such an assignment possible at all.
    "provider_lab_route_created",
    "provider_lab_route_updated",
    # DH-LAB-003/ADR-053: controlled, LabOffering-scoped Native Lab result-structure template
    # configuration. `lab_result_template_created` fires once per genuinely new template version
    # (never on an exact-content replay -- see `LabResultTemplateRepository.create_template`'s own
    # docstring). `lab_result_template_activated` fires on a real active-template switch, matching
    # `lab_offering_status_changed`/`lab_price_changed`'s own convention of auditing the
    # activation/deactivation event, not merely the create.
    "lab_result_template_created",
    "lab_result_template_activated",
    # DH-MVP-ONBOARD-001: facility enrollment and technical-integration discovery. The last of
    # these is a refusal rather than a write -- a platform admin tried to activate production
    # routing to a facility that cannot take the work -- and it is recorded because "who tried to
    # switch this on, and when" is exactly what an operator needs when a partner asks why their
    # orders are not flowing.
    "facility_enrollment_created",
    "facility_participation_mode_changed",
    "facility_onboarding_status_changed",
    "facility_technical_discovery_updated",
    "facility_interface_capability_recorded",
    "facility_onboarding_blocked",
    "facility_onboarding_unblocked",
    "provider_lab_route_activation_refused",
    # DH-CONNECT-CONTRACT-001: connector registration, lifecycle and qualification evidence.
    # `connector_qualification_recorded` is deliberately ONE event type for both outcomes -- the
    # `outcome` column already distinguishes a pass ("created") from a failure ("refused"), and a
    # second type would be a synonym that costs a migration to say the same thing twice.
    # `connector_configuration_changed` exists because a fingerprint change is what silently
    # invalidates a qualification, so the moment it happened must be recoverable.
    "connector_registered",
    "connector_suspended",
    "connector_restored",
    "connector_retired",
    "connector_configuration_changed",
    "connector_qualification_recorded",
    # DH-CONNECT-AGENT-001: the runtime's security events, and only those. `connector_heartbeat` is
    # deliberately absent -- §64: heartbeat is operational telemetry, not a security mutation, and a
    # table holding one row per agent poll would bury the events that matter under its own noise.
    # Runtime history lives in `connector_runtime_instances`, which is queryable and has no PHI.
    # `connector_runtime_connected` fires once per genuinely new runtime instance, not per beat.
    # `connector_message_refused` is a refusal, following `provider_lab_route_activation_refused`'s
    # precedent: the caller was authenticated, so this is not an authorization denial gated on a
    # database write -- it is a record that a credentialed party asked for something it may not
    # have.
    "connector_service_identity_set",
    "connector_runtime_connected",
    "connector_message_refused",
    # DH-NOTIFY-001 §21: the two operator acts worth auditing, and deliberately only those.
    #
    # `patient_notification_requested` records that a named clinician enrolled this order for
    # patient messaging and affirmed the patient's agreement -- the consent fact, bound to a real
    # `ExchangeIdentity` actor. `patient_notification_retry_requested` records a deliberate
    # re-attempt of a failed message.
    #
    # **Absent on purpose:** every automatic transition. A release-triggered obligation, a
    # dispatcher hand-over, a provider acceptance and a delivery receipt are all operational
    # telemetry, not security mutations, and one audit row per attempt would bury the two rows above
    # under their own noise -- the same reasoning §64 applied to connector heartbeats. That history
    # lives in `patient_notifications`/`patient_notification_attempts`, which are queryable and hold
    # no clinical content.
    #
    # **Also absent on purpose:** the destination number, the rendered message and the secure URL.
    # These events carry only the notification's own id as `resource_id`; the transport tables are
    # where an operator looks for a destination, and the raw link is never persisted anywhere at
    # all. Putting a phone number into a generic audit detail would spread a patient identifier
    # across a table whose retention and access are designed for security events, not contact data.
    "patient_notification_requested",
    "patient_notification_retry_requested",
    # DHEALTH-NATIVE-ACCESS-001: the staff access lifecycle, closing DHEALTH-NATIVE-SEC-001's F1.
    # These are `membership_created`/`role_assigned`'s missing counterparts -- their absence is
    # exactly what made that P1 possible, since `set_inactive`/`remove` had no caller and therefore
    # nothing to audit.
    #
    # `membership_deactivated` is deliberately not "membership_revoked" or "membership_deleted".
    # The row survives and only its status changes: every review, release, amendment and order that
    # names this membership stays attributable, and the `RESTRICT` foreign keys pointing at
    # `exchange_organization_memberships` make deletion impossible anyway. `membership_reactivated`
    # is its own type rather than a second `membership_created` with `outcome='updated'`, because
    # "let back in" and "added" are different security facts and neither should have to be inferred
    # from an outcome column.
    #
    # **Absent on purpose:** any refused access mutation. A cross-organization attempt, a
    # last-administrator lockout and a forbidden-role attempt are all authorization *refusals*, not
    # state mutations, and this table's contract is that a row commits in the same transaction as a
    # real change (see this module's docstring). Refusal telemetry stays in `exchange/audit.py`'s
    # best-effort file writer, where a failed write can never turn a refusal into an allow. This is
    # the same boundary `_audit_refused` already draws for every other 403 in `exchange/api.py`.
    "membership_deactivated",
    "membership_reactivated",
    "role_removed",
    # DHEALTH-CLINIC-VISIT-001: the clinic visit lifecycle. Each of these is a clinician-performed
    # state mutation on one visit, which is the only test this table applies.
    #
    # `visit_started` rather than `visit_taken`: the audit question is when the visit entered
    # IN_PROGRESS, and "taken" describes the clinician's gesture rather than the state change.
    #
    # `visit_note_appended` names the visit and the author. It never carries the note body, and no
    # column here could hold it: a clinical free-text statement in a security audit table would be
    # clinical data stored for a non-clinical purpose, which `AGENTS.md` forbids outright. "A note
    # revision was written to this visit, by this identity, at this time" is the security fact.
    #
    # **Absent on purpose:** `visit_viewed`. Reading a visit is not a mutation, and this module's
    # docstring is explicit that this table is not an access log -- adding it would make every
    # worklist refresh write rows and bury the handful that matter. Also absent: every refusal. An
    # attempt to complete another clinician's visit, or to touch a visit in another organization, is
    # an authorization refusal recorded by `exchange/audit.py`'s best-effort file writer, for the
    # same reason the staff-access block above gives.
    "visit_created",
    "visit_started",
    "visit_completed",
    "visit_cancelled",
    "visit_note_appended",
    "visit_reason_amended",
    # DHEALTH-BILLING-001/ADR-066: the four financial mutations, and deliberately only four.
    #
    # Money is the one domain where "who did this, to whom, for how much, when" is the whole
    # question, so every mutation is here. `payment_recorded` in particular is the row a facility
    # will actually go looking for when the till does not balance.
    #
    # **Amounts are not in these rows.** The audit table has no numeric column and this ticket does
    # not add one: the authoritative amount lives on the `Invoice`/`Payment` row itself, which is
    # immutable and already carries its own actor and timestamp. Duplicating it here would create a
    # second, divergeable record of the same fact -- ADR-064's mistake, in a domain that can least
    # afford it.
    #
    # **Absent on purpose:** `invoice_voided` (voiding is not built -- §26's policy is undecided),
    # any reversal or correction event (same reason), and any read event. A cashier opening an
    # invoice is not a mutation, and this table is not an access log; whether sensitive *reads*
    # should be audited is D-8 and is deliberately not answered here.
    "clinic_service_created",
    "clinic_service_price_changed",
    "invoice_created",
    "payment_recorded",
]
SecurityOutcome = Literal["created", "returned_existing", "updated", "unchanged", "refused"]
SecurityResourceType = Literal[
    "catalog_entry",
    "lab_directory_profile",
    "lab_offering",
    "lab_price",
    "lab_order",
    "patient_access_grant",
    "patient_reference",
    "patient",
    "provider_lab_route",
    "lab_result_template",
    "facility_enrollment",
    "facility_interface_capability",
    "facility_enrollment_blocker",
    # DH-CONNECT-CONTRACT-001. Qualification rows hang off an installation, so every connector
    # event names the installation -- audit stays installation-centric, never per-capability.
    "connector_installation",
    # DH-NOTIFY-001. Attempt rows hang off a notification, so audit stays notification-centric,
    # never per-attempt -- matching the connector convention directly above.
    "patient_notification",
    # DHEALTH-CLINIC-VISIT-001. Note revisions hang off a visit, so audit stays visit-centric --
    # the same convention again. Deliberately no "visit_note": a per-revision resource type would
    # invite naming the revision's content next to it.
    "visit",
    # DHEALTH-BILLING-001. A payment names the *invoice* it was recorded against, not itself:
    # payments hang off an invoice exactly as note revisions hang off a visit, so audit stays
    # invoice-centric -- the same convention as every block above.
    "invoice",
    "clinic_service",
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
