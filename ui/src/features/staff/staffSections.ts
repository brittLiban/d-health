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
import {
  Capability,
  can,
  canAny,
  type WorkspaceCapabilities,
} from '../workspace/workspaceCapabilities'

export type StaffSectionId =
  | 'today'
  | 'visits'
  | 'patients'
  | 'orders'
  | 'results'
  | 'team'
  | 'billing'
  | 'prices'
  | 'facility'

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
  // DHEALTH-ACCESS-CAPABILITIES-001 §26: what the facility charges, kept out of both
  // `Équipe et accès` (which administers people) and `Facturation` (which is the till). It is
  // configuration, and it belongs with configuration even before a full `Paramètres` exists.
  prices: 'Services et tarifs',
  // DHEALTH-FACILITY-PROFILE-001/ADR-074. A new section, and ADR-071's own reasoning is why this
  // is not the two-doors-into-one-room problem it refused elsewhere: `Services et tarifs` is the
  // configuration surface for *what a facility offers and what it charges*, and the facility's own
  // record is a different subject. `facility.configure`'s French description has named all three of
  // its subjects since before either ticket existed — "Gérer le catalogue, les modèles de résultats
  // et la fiche de l'établissement" — and this is the third clause, which had no screen anywhere.
  // `docs/NATIVE-OPERATING-PRODUCT.md` §30 recorded the facility profile as exactly the remaining
  // content a settings section would hold.
  //
  // Putting a timezone under a heading reading "Services and prices" would be the mislabelling that
  // costs a receptionist more than one extra navigation entry does.
  facility: 'Établissement',
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

  // `Aujourd'hui` is the clinical and laboratory work relay: it answers "what needs
  // me today" by listing patients, orders and results waiting on this person. For
  // somebody holding only administrative or financial authority there is nothing for
  // it to answer, so it requires at least one capability it can actually report on.
  const hasWorkflow = canAny(
    capabilities,
    Capability.PATIENT_DIRECTORY_READ,
    Capability.VISIT_READ,
    // DHEALTH-FRONT-DESK-001/ADR-069. `Aujourd'hui` is where the arrival and waiting surface lives
    // (§24), and it is the *only* section a reception-only membership reaches: `Visites` needs
    // `visit.read` or `visit.document`, which that membership deliberately does not hold. Without
    // this line somebody granted exactly the three reception capabilities would sign in, see
    // `Patients` alone, and have nowhere to check anybody in -- the same empty-workspace failure
    // this file's own comment records happening twice before.
    Capability.VISIT_CHECK_IN,
    Capability.ORDER_MANAGE,
    Capability.RESULT_REVIEW,
    Capability.LAB_QUEUE_WORK,
    Capability.LAB_RESULT_RELEASE,
  )

  if (hasWorkflow) ids.push('today')
  if (canAny(capabilities, Capability.VISIT_READ, Capability.VISIT_DOCUMENT)) ids.push('visits')
  if (canAny(capabilities, Capability.PATIENT_DIRECTORY_READ, Capability.PATIENT_REGISTER)) {
    ids.push('patients')
  }
  if (canAny(capabilities, Capability.ORDER_MANAGE, Capability.LAB_QUEUE_WORK)) ids.push('orders')
  if (canAny(capabilities, Capability.RESULT_REVIEW, Capability.LAB_RESULT_RELEASE)) {
    ids.push('results')
  }
  if (can(capabilities, Capability.STAFF_ACCESS_MANAGE)) ids.push('team')

  // DHEALTH-ACCESS-CAPABILITIES-001 §27 -- the correction this ticket owes DHEALTH-BILLING-001.
  //
  // `Facturation` is the **operational** till: invoices, payments, receipts, balances. It requires
  // `billing.operate` and nothing else. Under the previous ticket it also appeared for
  // `billing.configure_prices`, so an administrator who may set a price but may not take a payment
  // landed on a surface where every control would refuse them.
  //
  // Still after `team`, for the reason that ticket's follow-up commit recorded: `resolveSection`
  // lands on the first available section, and DHEALTH-NATIVE-ACCESS-001 decided an administrator
  // opens the application on `Équipe et accès`.
  if (can(capabilities, Capability.BILLING_OPERATE)) ids.push('billing')

  // ...and price configuration gets its own entry (§26). It is configuration, not people
  // administration, so it does not belong inside `Équipe et accès`; and it is not the till, so it
  // does not belong inside `Facturation` either. Somebody holding both capabilities sees both
  // sections, which is the honest rendering of holding both jobs.
  //
  // **[Widened 2026-10-01, DHEALTH-LAB-OFFERINGS-001.** `facility.configure` now reaches it too,
  // because the section is `Services et tarifs` and a laboratory's *services* -- which diagnostic
  // work it accepts through Hawva -- are `facility.configure`'s subject, not the price-setter's.
  // The capability's own French description already said so before this ticket existed: *"Gérer le
  // catalogue, les modèles de résultats et la fiche de l'établissement."*
  //
  // Deliberately **not** a new top-level section. `Services et tarifs` is already the configuration
  // surface for what a facility offers and what it charges, and a second entry over the same
  // subject would be the two-doors-into-one-room problem this file refuses elsewhere.
  //
  // The two halves of the screen are gated independently *inside* it, exactly as `Facturation`'s
  // till and price halves already are: a `facility.configure` holder with no
  // `billing.configure_prices` sees the services they may configure and not the prices they may
  // not, and the reverse also holds. Holding both shows both.]**
  if (
    can(capabilities, Capability.BILLING_CONFIGURE_PRICES) ||
    can(capabilities, Capability.FACILITY_CONFIGURE)
  ) {
    ids.push('prices')
  }

  // DHEALTH-FACILITY-PROFILE-001: `facility.configure` **alone**, deliberately not the
  // `facility.configure || billing.configure_prices` disjunction `prices` above uses. Somebody who
  // may set a price has no business in the facility's own record, and reaching a section is not
  // authority over everything in it — the distinction `Facturation` already draws between its till
  // and price halves.
  if (can(capabilities, Capability.FACILITY_CONFIGURE)) {
    ids.push('facility')
  }

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
