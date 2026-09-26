"""DHEALTH-NATIVE-ACCESS-001: the three audit event types staff-access revocation needs.

**No table is created, altered or dropped.** This migration widens exactly one CHECK constraint,
because DHEALTH-NATIVE-SEC-001's P1 finding (F1) was never a missing table -- the membership and
role-assignment rows, their status column and their unique constraints have all existed since
``0005_exchange_org_auth``. What was missing was a *caller*: no endpoint and no screen could reach
``set_inactive`` or ``remove``, so authority could be granted through the product and never
withdrawn through it. Closing that gap needs three new audit event types and nothing else from the
schema.

The three:

- ``membership_deactivated`` -- an organization's administrator withdrew a person's access to *that
  organization*. Deliberately not ``membership_revoked``/``membership_deleted``: the row survives,
  its status changes, and every historical clinical attribution hanging off it (reviews, releases,
  amendments, orders) stays intact and attributable. The ``RESTRICT`` foreign keys pointing at
  ``exchange_organization_memberships`` already make deletion impossible in practice; this event
  name states the intent rather than leaving it to be inferred from the constraint.
- ``membership_reactivated`` -- the same row returning to ``ACTIVE``. A distinct type rather than a
  second ``membership_created`` with ``outcome='updated'``, because "this person was let back in"
  and "this person was added" are different security facts and an auditor must not have to infer
  which one happened from an outcome column.
- ``role_removed`` -- ``role_assigned``'s missing counterpart, and the one that closes the P1
  literally: removing ``CLINICIAN`` or ``LAB_RESULT_RELEASER`` is what makes a still-valid token
  stop authorizing clinical work on the very next request.

``resource_type`` is **not** widened. All three events name an organization, a subject identity and
a membership through the columns DH-EX-002 already defined for exactly this purpose; inventing a
``membership`` resource type would be a synonym for the ``membership_id`` column sitting beside it.
Same reasoning 0029 recorded for the connector events, applied in the other direction.

``role`` is not widened either, and that is load-bearing rather than incidental:
``ck_exchange_security_audit_events_role`` and ``ck_exchange_org_role_assignments_role`` both
enumerate the four real organization roles, so ``PLATFORM_ADMIN`` cannot be written to either table
even if an application-layer check were removed. Platform authority is a Keycloak realm role, has
never been an ``OrganizationRole``, and this migration deliberately keeps it that way -- the
database is the second layer behind the service-level allowed-role check, not a substitute for it.

Downgrade narrows the constraint back and deliberately does **not** delete the rows that would then
violate it, exactly as 0032 established: an audit row records that authority was withdrawn from a
human being, and a schema downgrade is not a reason to erase it. A deployment carrying these rows
must deal with them deliberately, and a constraint violation is the correct way to be told.

The revision id is **26 characters**, against ``alembic_version.version_num``'s ``VARCHAR(32)`` --
the boundary DH-CONNECT-CONTRACT-001 found the hard way (see ``tests/test_migration_hygiene.py``).

Revision ID: 0033_staff_access_lifecycle
Revises: 0032_patient_notifications
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033_staff_access_lifecycle"
down_revision: str | Sequence[str] | None = "0032_patient_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled out in full rather than assembled from the ORM, because a migration must describe the
# database at *this* revision -- reading today's model would make an old migration silently change
# meaning the next time a value is added. Identical discipline to 0029/0032.
_PRE_0033_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound', 'lab_order_assigned', 'lab_sample_collection_recorded', "
    "'lab_results_released', 'lab_observation_recorded', 'lab_result_entry_completed', "
    "'lab_observation_corrected', 'lab_result_amendment_started', "
    "'lab_observation_amended', 'lab_result_amendment_released', "
    "'patient_created', "
    "'provider_lab_route_created', 'provider_lab_route_updated', "
    "'lab_result_template_created', 'lab_result_template_activated', "
    "'facility_enrollment_created', 'facility_participation_mode_changed', "
    "'facility_onboarding_status_changed', 'facility_technical_discovery_updated', "
    "'facility_interface_capability_recorded', 'facility_onboarding_blocked', "
    "'facility_onboarding_unblocked', 'provider_lab_route_activation_refused', "
    "'connector_registered', 'connector_suspended', 'connector_restored', "
    "'connector_retired', 'connector_configuration_changed', "
    "'connector_qualification_recorded', "
    "'connector_service_identity_set', 'connector_runtime_connected', "
    "'connector_message_refused', "
    "'patient_notification_requested', 'patient_notification_retry_requested'"
)

# Three, and deliberately no fourth. A *refused* access mutation -- a cross-organization attempt, a
# last-administrator lockout, a forbidden role -- is an authorization refusal, not a state mutation,
# and this table's own contract (see `ExchangeSecurityAuditEvent`) is that a row here commits in the
# same transaction as a real change. Refusals stay in `exchange/audit.py`'s best-effort file writer,
# where a failed write can never turn a refusal into an allow.
_0033_NEW_EVENT_TYPES = "'membership_deactivated', 'membership_reactivated', 'role_removed'"

_EVENT_TYPE_CHECK = "ck_exchange_security_audit_events_event_type"


def upgrade() -> None:
    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0033_EVENT_TYPES}, {_0033_NEW_EVENT_TYPES})",
    )


def downgrade() -> None:
    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0033_EVENT_TYPES})",
    )
