/**
 * DHEALTH-ACCESS-CAPABILITIES-001/ADR-067 — what the signed-in person may do *here*, as the
 * **server** resolved it.
 *
 * ── What changed, and why it matters ──
 *
 * This file used to map role names to booleans: `roles.includes('CLINICIAN')` became
 * `canViewPatients`, and so on for nine capabilities. That was a second implementation of DHealth's
 * authorization rule, written in TypeScript, kept in step with the Python one by hand. It worked
 * for as long as authority *was* a role name.
 *
 * It stopped working the moment a facility could grant one person one extra authority. A
 * clinician granted `billing.operate` directly holds a capability no role of theirs implies —
 * the server knows, and a role-name mapping never could. So the mapping is gone.
 *
 * `/me` now returns `capabilities` per membership, resolved by
 * `adapter/src/daher_adapter/exchange/capabilities.py` — the same resolver every backend gate
 * asks. This module reads that list. It derives nothing.
 *
 * ── Frontend capability checks are usability, never security ──
 *
 * Unchanged, and worth repeating because the new model makes it easier to forget: nothing here
 * authorizes anything. The server decides on every request, from the caller's token and the
 * organization in the URL. These derivations exist so a person is not offered a workflow their
 * membership cannot complete — an offered-then-refused action is a worse experience than one that
 * was never offered, and it teaches people to distrust the interface. Hiding a control is a
 * convenience for the operator; removing a server check would be a vulnerability.
 *
 * ── No separate screen permissions ──
 *
 * §4: there is no `SCREEN_BILLING` and no `SHOW_RESULTS_TAB`. A section appears because the person
 * holds a capability that makes it useful, which is the same fact the backend enforces. Two
 * independent permission systems would drift, and the drift would be invisible until somebody was
 * shown a screen they could not use.
 */

import type { ExchangeMembershipSummary } from '../../api/exchangeTypes'

/**
 * The capability catalog, mirrored as a TypeScript union so a typo is a compile error rather than
 * a silently false permission check.
 *
 * The **values** are the server's, and they are the contract: `capabilities.py` defines them, the
 * API sends them as strings, and this type names them. A key here that the server does not send
 * simply never matches — which is why `can()` below is the only way to ask, and why nothing
 * constructs these strings dynamically.
 */
export const Capability = {
  PATIENT_DIRECTORY_READ: 'patient.directory.read',
  PATIENT_REGISTER: 'patient.register',
  VISIT_READ: 'visit.read',
  VISIT_CHECK_IN: 'visit.check_in',
  VISIT_DOCUMENT: 'visit.document',
  ORDER_MANAGE: 'order.manage',
  RESULT_REVIEW: 'result.review',
  LAB_QUEUE_WORK: 'lab.queue.work',
  LAB_RESULT_RELEASE: 'lab.result.release',
  STAFF_ACCESS_MANAGE: 'staff.access.manage',
  FACILITY_CONFIGURE: 'facility.configure',
  BILLING_OPERATE: 'billing.operate',
  BILLING_CONFIGURE_PRICES: 'billing.configure_prices',
} as const

export type CapabilityKey = (typeof Capability)[keyof typeof Capability]

/**
 * The signed-in person's authority in the active organization.
 *
 * A `Set` rather than an array, because every consumer asks *"do they hold this"* and nobody
 * iterates. `NO_CAPABILITIES` is the safe default for every state that is not a usable membership:
 * loading, signed out, no active organization, an inactive membership. The failure direction is
 * "shows nothing", never "shows everything".
 */
export type WorkspaceCapabilities = ReadonlySet<CapabilityKey>

export const NO_CAPABILITIES: WorkspaceCapabilities = new Set<CapabilityKey>()

/**
 * **The one question.** `can(capabilities, Capability.BILLING_OPERATE)`.
 *
 * §44: one abstraction, not `capabilities.includes(...)` scattered through eighty components. A
 * helper also means the *shape* of the answer can change — a future ticket adding per-resource
 * scoping — without touching every call site.
 */
export function can(capabilities: WorkspaceCapabilities, capability: CapabilityKey): boolean {
  return capabilities.has(capability)
}

/** True when the person holds **any** of these. For surfaces two different authorities can open. */
export function canAny(
  capabilities: WorkspaceCapabilities,
  ...wanted: readonly CapabilityKey[]
): boolean {
  return wanted.some((capability) => capabilities.has(capability))
}

/**
 * A membership is usable if the organization is active, the membership is active, and the server
 * gave it at least one capability.
 *
 * ── This is now a fact rather than a list ──
 *
 * It used to enumerate roles: ORG_ADMIN at either type, CLINICIAN at a provider, LAB_TECH or
 * LAB_RESULT_RELEASER at a laboratory, and later BILLING_CLERK. Every new role meant remembering
 * to add a line here, and forgetting meant somebody signed in successfully and was shown an empty
 * workspace — which happened twice: once for ORG_ADMIN (DHEALTH-NATIVE-ACCESS-001) and once for
 * BILLING_CLERK (DHEALTH-BILLING-001).
 *
 * It cannot happen again. "Has authority here" is exactly "the server sent at least one
 * capability", and a member with authority is a member with something to do.
 */
export function isUsableMembership(membership: ExchangeMembershipSummary): boolean {
  if (membership.organization_status !== 'ACTIVE') return false
  if (membership.membership_status !== 'ACTIVE') return false
  return membership.capabilities.length > 0
}

export function usableMemberships(
  memberships: readonly ExchangeMembershipSummary[],
): ExchangeMembershipSummary[] {
  return memberships.filter(isUsableMembership)
}

/**
 * The active membership's capabilities, as a set.
 *
 * Derived from the *active* membership alone. Authority at another organization grants nothing
 * here: a clinician at Clinique A who is also a cashier at Clinique B must not see `Facturation`
 * while Clinique A is active — that would misrepresent whose authority is in use, and the server
 * would refuse the first request anyway (§6/§35).
 */
export function capabilitiesFor(
  membership: ExchangeMembershipSummary | null,
): WorkspaceCapabilities {
  if (membership === null || !isUsableMembership(membership)) return NO_CAPABILITIES
  return new Set(membership.capabilities as CapabilityKey[])
}
