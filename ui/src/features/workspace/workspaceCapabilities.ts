/**
 * DH-MVP-POLISH-001 — what the signed-in person may do *here*, in the
 * organization they currently have active.
 *
 * ── Frontend capability checks are usability, never security ──
 *
 * Nothing in this file authorizes anything. The server decides, on every
 * request, from the caller's token and the organization in the URL. These
 * derivations exist so a person is not offered a workflow their membership
 * cannot complete — an offered-then-refused action is a worse experience than
 * one that was never offered, and it teaches people to distrust the interface.
 * Hiding a control is a convenience for the operator; removing a server check
 * would be a vulnerability. The two are not substitutes.
 *
 * ── Every capability maps to a gate the backend actually enforces ──
 *
 * These are not invented permissions. Each one names a check that exists in
 * `adapter/src/daher_adapter/exchange/api.py`:
 *
 *   canViewPatients    `_require_clinician`            CLINICIAN at the provider
 *   canCreateOrders    `_require_clinician`            CLINICIAN at the provider
 *   canReviewResults   `_require_clinician`            CLINICIAN at the provider
 *   canRunVisits       `_require_clinician`            CLINICIAN at the provider
 *   canWorkLabOrders   `_require_lab_tech`             LAB_TECH at the lab
 *   canReleaseResults  release endpoints' own gate     LAB_RESULT_RELEASER at the lab
 *   canAdministerTeam  `_require_org_admin`            ORG_ADMIN at this organization
 *
 * The first four resolve from the same grant today, because CLINICIAN is the
 * single authority the backend checks for patient binding, order creation,
 * doctor review and the visit lifecycle alike. They are kept as four names
 * rather than collapsed into one so each call site states which authority it
 * believes it needs, and so a future split of that role is a change here rather
 * than across the pages.
 *
 * ADR-044 is the reason entry and release are separate: a LAB_TECH who does not
 * also hold LAB_RESULT_RELEASER can record values and can never release them.
 * The interface must reflect that, not paper over it.
 */

import type { ExchangeMembershipSummary } from '../../api/exchangeTypes'

export interface WorkspaceCapabilities {
  canViewPatients: boolean
  canCreateOrders: boolean
  canReviewResults: boolean
  /**
   * DHEALTH-CLINIC-VISIT-001 — may run consultations here: register arrivals, take a waiting
   * patient, write notes, finish or cancel a visit.
   *
   * Resolves from the same CLINICIAN grant as the three above, and is a fourth name rather than a
   * reuse of `canViewPatients` for the reason this file already gives: each call site states which
   * authority it believes it needs. It matters more here than elsewhere, because *who may register
   * an arrival* is an open product question (D-1 in `docs/NATIVE-OPERATING-PRODUCT.md` §28) — when a
   * front-desk role exists, this capability is where that answer lands, and nothing else has to
   * move.
   */
  canRunVisits: boolean
  canWorkLabOrders: boolean
  canReleaseResults: boolean
  /**
   * DHEALTH-NATIVE-ACCESS-001 — may operate the Équipe et accès surface here.
   *
   * Unlike the five above, this one is not clinical. It is listed beside them
   * because it is derived the same way, from the same active membership, and
   * because the server gate behind it (`_require_org_admin`) is checked on every
   * request regardless of what this returns.
   */
  canAdministerTeam: boolean
}

export const NO_CAPABILITIES: WorkspaceCapabilities = {
  canViewPatients: false,
  canCreateOrders: false,
  canReviewResults: false,
  canRunVisits: false,
  canWorkLabOrders: false,
  canReleaseResults: false,
  canAdministerTeam: false,
}

/**
 * A membership is only usable if the organization is active, the membership
 * itself is active, and it carries at least one role that unlocks a workflow
 * this application actually renders.
 *
 * ── ORG_ADMIN became usable in DHEALTH-NATIVE-ACCESS-001 ──
 *
 * It was deliberately excluded before, and the reason given was that
 * "administration is a later ticket's surface, and listing such an organization
 * here would present a workspace with nothing in it". Both halves were true and
 * the second no longer is: Équipe et accès is a real screen, so an ORG_ADMIN who
 * holds no clinical or laboratory role now has something to do here.
 *
 * This is not a widening of authority. An administrator held ORG_ADMIN before
 * this change and the server honoured it before this change — what they could not
 * do was reach any screen at all, which is why revoking access needed a database
 * administrator. That was DHEALTH-NATIVE-SEC-001's P1 in its user-facing form.
 */
export function isUsableMembership(membership: ExchangeMembershipSummary): boolean {
  if (membership.organization_status !== 'ACTIVE') return false
  if (membership.membership_status !== 'ACTIVE') return false

  // Valid at either organization type, and checked first because it is the one
  // role whose usefulness does not depend on which kind of facility this is.
  if (membership.roles.includes('ORG_ADMIN')) {
    return (
      membership.organization_type === 'PROVIDER' || membership.organization_type === 'LAB'
    )
  }

  if (membership.organization_type === 'PROVIDER') {
    return membership.roles.includes('CLINICIAN')
  }
  if (membership.organization_type === 'LAB') {
    return (
      membership.roles.includes('LAB_TECH') ||
      membership.roles.includes('LAB_RESULT_RELEASER')
    )
  }
  // An organization type this build does not know how to render. Not an error,
  // and not something to guess at: it simply offers no workflow here.
  return false
}

export function usableMemberships(
  memberships: readonly ExchangeMembershipSummary[],
): ExchangeMembershipSummary[] {
  return memberships.filter(isUsableMembership)
}

/**
 * Derived from the *active* membership alone. Roles held at some other
 * organization grant nothing here: a clinician at Clinique A who is also a
 * technician at Laboratoire B must not see laboratory work while Clinique A is
 * the active organization. That would misrepresent whose authority is in use.
 */
export function capabilitiesFor(
  membership: ExchangeMembershipSummary | null,
): WorkspaceCapabilities {
  if (membership === null || !isUsableMembership(membership)) return NO_CAPABILITIES

  const isProvider = membership.organization_type === 'PROVIDER'
  const isLab = membership.organization_type === 'LAB'
  const clinician = isProvider && membership.roles.includes('CLINICIAN')

  return {
    canViewPatients: clinician,
    canCreateOrders: clinician,
    canReviewResults: clinician,
    canRunVisits: clinician,
    canWorkLabOrders: isLab && membership.roles.includes('LAB_TECH'),
    canReleaseResults: isLab && membership.roles.includes('LAB_RESULT_RELEASER'),
    // Not gated on organization type: ORG_ADMIN is valid at a provider and at a
    // laboratory alike (`ALLOWED_ROLES_BY_ORG_TYPE` lists it for both), and the
    // staff a facility administers are the same kind of record either way.
    canAdministerTeam: membership.roles.includes('ORG_ADMIN'),
  }
}
