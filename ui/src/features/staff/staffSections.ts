/**
 * DH-MVP-POLISH-001 — the staff navigation, derived from real authority.
 *
 * One application. The person signs in once; the organization they have active
 * and the roles they hold *there* decide which sections exist. There is no
 * "DHealth Clinic" to switch out of and no "DHealth Lab" to switch into.
 *
 * ── Only sections that do something are rendered ──
 *
 * Nothing here is listed and disabled, and nothing is marked "Bientôt". A
 * navigation item that cannot be used is a promise the product has not kept,
 * and a queue of them trains people to skim past the navigation entirely. If a
 * membership does not unlock a section, that section is absent.
 *
 * This is why `Réglages` is not in this file. Facility setup and administration
 * are a later ticket's surface (DH-MVP-ADMIN-001); adding the item now would
 * mean shipping exactly the dead entry this rule forbids. It arrives when it
 * does something.
 *
 * ── Absence is not protection ──
 *
 * Hiding a section keeps someone from being offered work their membership
 * cannot complete. It is not an access control. The server checks every request
 * against the caller's own token and the organization in the URL, and would
 * refuse a hand-typed request to a hidden surface just the same.
 */

import type { ExchangeMembershipSummary } from '../../api/exchangeTypes'
import type { WorkspaceCapabilities } from '../workspace/workspaceCapabilities'

export type StaffSectionId = 'today' | 'patients' | 'orders' | 'results'

export interface StaffSection {
  id: StaffSectionId
  label: string
}

/**
 * Deliberately not "Accueil": the first screen answers "what needs me today",
 * which is a working question, not a front door. "Ordres" rather than "Ordres de
 * laboratoire" because in a laboratory the qualifier is noise, and the one word
 * is true on both sides of the exchange.
 */
const SECTION_LABELS: Record<StaffSectionId, string> = {
  today: "Aujourd'hui",
  patients: 'Patients',
  orders: 'Ordres',
  results: 'Résultats',
}

export function sectionLabel(id: StaffSectionId): string {
  return SECTION_LABELS[id]
}

/**
 * The sections an active membership actually unlocks, in navigation order.
 *
 * A provider clinician gets all four. In a laboratory, `Ordres` is the work
 * queue and needs LAB_TECH; `Résultats` is inspection and release and needs
 * LAB_RESULT_RELEASER — ADR-044 keeps entry and release separate authorities, so
 * a technician who cannot release simply has no `Résultats` section. There is no
 * laboratory `Patients` section: a laboratory works from orders, and the
 * patient-directory authority (`_require_clinician`) is a provider's.
 */
export function availableSections(
  activeOrganization: ExchangeMembershipSummary | null,
  capabilities: WorkspaceCapabilities,
): StaffSection[] {
  if (activeOrganization === null) return []

  const ids: StaffSectionId[] = ['today']

  if (capabilities.canViewPatients) ids.push('patients')
  if (capabilities.canCreateOrders || capabilities.canWorkLabOrders) ids.push('orders')
  if (capabilities.canReviewResults || capabilities.canReleaseResults) ids.push('results')

  return ids.map((id) => ({ id, label: SECTION_LABELS[id] }))
}

/**
 * Keeps the requested section only if it still exists.
 *
 * This is what stops a stale section surviving an organization switch. A
 * clinician standing in `Patients` who switches to a laboratory has no
 * `Patients` section any more; landing them on the first available section is
 * correct, where leaving the old id active would render a provider page against
 * a laboratory organization.
 */
export function resolveSection(
  requested: StaffSectionId | null,
  available: readonly StaffSection[],
): StaffSectionId | null {
  if (available.length === 0) return null
  if (requested && available.some((section) => section.id === requested)) return requested
  return available[0].id
}

// ── DH-MVP-ONBOARD-001: platform scope ───────────────────────────────────────────────────────

/**
 * The network administration sections, shown only in platform scope.
 *
 * Deliberately a separate list rather than extra entries in `availableSections`:
 * the staff sections derive from an active membership, and platform authority is
 * not a membership. Merging them would have required an organization to exist for
 * a platform administrator who legitimately has none.
 *
 * `Configuration réseau` is not here for the same reason `Réglages` is absent
 * from the staff nav — there is no screen behind it yet. Route configuration
 * lives inside a facility's own detail page, where the relationship it is about
 * is on screen.
 */
export type PlatformSectionId = 'facilities' | 'integrations'

export interface PlatformSection {
  id: PlatformSectionId
  label: string
}

const PLATFORM_SECTION_LABELS: Record<PlatformSectionId, string> = {
  facilities: 'Établissements',
  integrations: 'Intégrations',
}

export function platformSectionLabel(id: PlatformSectionId): string {
  return PLATFORM_SECTION_LABELS[id]
}

/**
 * Platform sections require platform authority and nothing else. Hiding them from
 * everybody else is usability; the server refuses them regardless, which is the
 * part that makes it safe.
 */
export function availablePlatformSections(platformAdmin: boolean): PlatformSection[] {
  if (!platformAdmin) return []
  return (['facilities', 'integrations'] as const).map((id) => ({
    id,
    label: PLATFORM_SECTION_LABELS[id],
  }))
}
