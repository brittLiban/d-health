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
 * `Réglages` is still not in this file, and deliberately still is not.
 * DHEALTH-NATIVE-ACCESS-001 added `Équipe et accès` — one narrow, facility-owned
 * screen that does one thing — rather than the `Réglages` application that item
 * was standing in for. Facility setup, establishment details and everything else
 * an eventual settings surface would hold still have nothing behind them, so they
 * are still absent.
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

export type StaffSectionId =
  | 'today'
  | 'visits'
  | 'patients'
  | 'orders'
  | 'results'
  | 'billing'
  | 'team'

export interface StaffSection {
  id: StaffSectionId
  label: string
}

/**
 * Deliberately not "Accueil": the first screen answers "what needs me today",
 * which is a working question, not a front door. "Ordres" rather than "Ordres de
 * laboratoire" because in a laboratory the qualifier is noise, and the one word
 * is true on both sides of the exchange.
 *
 * `Équipe et accès` reuses the exact wording the platform-scope facility page
 * already uses for the same subject (`FacilityTeam.tsx`), so the same idea is not
 * called two different things depending on who is looking at it. "Accès" is there
 * on purpose: this screen is about authority, not a staff phone list.
 *
 * `Visites` sits directly after `Aujourd'hui` because it is the clinic's live
 * state — who is here, who is being seen — and a clinician arriving in the morning
 * wants it before the patient directory. It is deliberately not called
 * "Consultations": a visit exists from the moment somebody walks in, which is
 * before any consultation has happened, and it may end as a cancellation where no
 * consultation ever happens at all.
 *
 * `Facturation` rather than `Caisse` or `Paiements`: the section covers what the
 * facility charges, what it has invoiced and what it has been paid, and only the
 * first of those words is true of all three. It sits last — see `availableSections`
 * for why that position is load-bearing rather than aesthetic.
 */
const SECTION_LABELS: Record<StaffSectionId, string> = {
  today: "Aujourd'hui",
  visits: 'Visites',
  patients: 'Patients',
  orders: 'Ordres',
  results: 'Résultats',
  team: 'Équipe et accès',
  billing: 'Facturation',
}

export function sectionLabel(id: StaffSectionId): string {
  return SECTION_LABELS[id]
}

/**
 * The sections an active membership actually unlocks, in navigation order.
 *
 * A provider clinician gets `Aujourd'hui`, `Visites`, `Patients`, `Ordres` and
 * `Résultats`. In a laboratory, `Ordres` is the work
 * queue and needs LAB_TECH; `Résultats` is inspection and release and needs
 * LAB_RESULT_RELEASER — ADR-044 keeps entry and release separate authorities, so
 * a technician who cannot release simply has no `Résultats` section. There is no
 * laboratory `Patients` section: a laboratory works from orders, and the
 * patient-directory authority (`_require_clinician`) is a provider's.
 *
 * ── Why `today` is now conditional ──
 *
 * `Aujourd'hui` is the clinical and laboratory work relay: it answers "what needs
 * me today" by listing patients, orders and results waiting on this person. For an
 * administrator who holds only ORG_ADMIN there is nothing for it to answer, and it
 * would render an empty relay — exactly the dead navigation entry this file's own
 * rule forbids, arrived at from the other direction. So `today` requires at least
 * one clinical or laboratory capability, and an administrator with none lands
 * directly on `Équipe et accès`, which is the only thing they came here to do.
 *
 * An administrator who *also* holds a clinical role keeps `today` and every other
 * section, and gets `Équipe et accès` last: administration is not the thing they
 * open the application for.
 */
export function availableSections(
  activeOrganization: ExchangeMembershipSummary | null,
  capabilities: WorkspaceCapabilities,
): StaffSection[] {
  if (activeOrganization === null) return []

  const ids: StaffSectionId[] = []

  const hasWorkflow =
    capabilities.canViewPatients ||
    capabilities.canRunVisits ||
    capabilities.canCreateOrders ||
    capabilities.canReviewResults ||
    capabilities.canWorkLabOrders ||
    capabilities.canReleaseResults

  if (hasWorkflow) ids.push('today')
  if (capabilities.canRunVisits) ids.push('visits')
  if (capabilities.canViewPatients) ids.push('patients')
  if (capabilities.canCreateOrders || capabilities.canWorkLabOrders) ids.push('orders')
  if (capabilities.canReviewResults || capabilities.canReleaseResults) ids.push('results')
  if (capabilities.canAdministerTeam) ids.push('team')
  // DHEALTH-BILLING-001: either billing capability opens the section, and the
  // section itself decides which of its two surfaces that person can use — the
  // till needs BILLING_OPERATE, the price list needs BILLING_CONFIGURE_PRICES,
  // and ADR-066 §2 keeps them in different hands on purpose. A cashier therefore
  // sees invoices and no price editor; an administrator sees the price list and
  // no till. Neither is shown a control they would be refused.
  //
  // **Last, and after `team` specifically.** It was placed before `team` first,
  // on the reasoning that money follows the work that produced it. That ordering
  // silently changed where an ORG_ADMIN-only account *lands*: `resolveSection`
  // takes the first available section, so an administrator would have opened the
  // application on `Facturation` instead of `Équipe et accès`. DHEALTH-NATIVE-ACCESS-001
  // decided that landing explicitly — "lands directly on Équipe et accès, which is
  // the only thing they came here to do" — and its browser suite asserts it right
  // after sign-in. A tidier reading of one navigation list is not a reason to
  // change a recorded product decision in passing, so billing goes last.
  if (capabilities.canOperateBilling || capabilities.canConfigurePrices) ids.push('billing')

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
