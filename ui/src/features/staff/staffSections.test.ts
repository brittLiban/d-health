/**
 * DH-MVP-POLISH-001 — navigation derivation, tested directly.
 *
 * The rule this protects: a section is rendered only if the active membership can
 * actually use it, and nothing is listed-and-disabled or marked "Bientôt".
 */

import { describe, expect, it } from 'vitest'
import type { ExchangeMembershipSummary } from '../../api/exchangeTypes'
import { capabilitiesFor, NO_CAPABILITIES } from '../workspace/workspaceCapabilities'
import { availableSections, resolveSection, sectionLabel } from './staffSections'
import { capabilitiesForRoles } from '../../test/renderWithWorkspace'

function provider(roles: string[] = ['CLINICIAN']): ExchangeMembershipSummary {
  return {
    organization_id: 'prov-1',
    organization_name: 'Clinique de la Paix',
    organization_type: 'PROVIDER',
    organization_status: 'ACTIVE',
    membership_status: 'ACTIVE',
    roles,
    capabilities: capabilitiesForRoles(roles),
  }
}

function lab(roles: string[]): ExchangeMembershipSummary {
  return { ...provider(roles), organization_id: 'lab-1', organization_type: 'LAB' }
}

function idsFor(membership: ExchangeMembershipSummary) {
  return availableSections(membership, capabilitiesFor(membership)).map((s) => s.id)
}

describe('availableSections', () => {
  it('offers a provider clinician every clinical section, in navigation order', () => {
    // DHEALTH-CLINIC-VISIT-001 added `visits`, second: it is the clinic's live state (who is here,
    // who is being seen), which a clinician arriving in the morning wants before the directory.
    expect(idsFor(provider())).toEqual(['today', 'visits', 'patients', 'orders', 'results'])
  })

  it('never offers a laboratory a Visites section', () => {
    // The visit lifecycle is gated on CLINICIAN, which `ALLOWED_ROLES_BY_ORG_TYPE` never grants at a
    // laboratory. A laboratory has no outpatient consultation, and offering the section would be a
    // promise the server refuses to keep -- the same reasoning as `patients` below.
    expect(idsFor(lab(['LAB_TECH', 'LAB_RESULT_RELEASER']))).not.toContain('visits')
    expect(idsFor(lab(['ORG_ADMIN']))).not.toContain('visits')
  })

  it('never offers Visites to an administrator who holds no clinical role', () => {
    // Registering an arrival is clinical workflow authority today. *Whether it should stay that way*
    // is D-1 in `docs/NATIVE-OPERATING-PRODUCT.md` §28 -- an open product question, not something
    // this navigation may answer by widening what an ORG_ADMIN is offered.
    expect(idsFor(provider(['ORG_ADMIN']))).not.toContain('visits')
  })

  it('offers a laboratory technician the queue but not release', () => {
    expect(idsFor(lab(['LAB_TECH']))).toEqual(['today', 'orders'])
  })

  it('offers a laboratory releaser release but not the queue', () => {
    expect(idsFor(lab(['LAB_RESULT_RELEASER']))).toEqual(['today', 'results'])
  })

  it('offers a dual-role laboratory user both', () => {
    expect(idsFor(lab(['LAB_TECH', 'LAB_RESULT_RELEASER']))).toEqual([
      'today',
      'orders',
      'results',
    ])
  })

  it('never offers a laboratory a Patients section', () => {
    // A laboratory works from orders; the patient directory is a provider's
    // authority. Offering it would be a promise the server refuses to keep.
    expect(idsFor(lab(['LAB_TECH', 'LAB_RESULT_RELEASER']))).not.toContain('patients')
  })

  // ── DHEALTH-NATIVE-ACCESS-001: Équipe et accès ─────────────────────────────

  it('offers an ORG_ADMIN-only membership administration and prices, and nothing clinical', () => {
    // Not `['today', 'team']`. `Aujourd'hui` is the clinical/laboratory work relay
    // and would render empty for somebody who holds no clinical role -- the dead
    // navigation entry this module's own rule forbids, reached from the other side.
    //
    // **DHEALTH-ACCESS-CAPABILITIES-001 §27 corrected this.** DHEALTH-BILLING-001 gave an
    // administrator `Facturation`, because that section opened for *either* billing capability
    // and `billing.configure_prices` is ORG_ADMIN's. The effect was an administrator landing on
    // the operational till -- invoices, payments, receipts -- where every control would refuse
    // them, since they hold no `billing.operate`.
    //
    // They now get `Services et tarifs`: the price list, which is what they actually may do, and
    // which §26 keeps out of both `Facturation` (the till) and `Équipe et accès` (people).
    //
    // `team` first, and the order matters: `resolveSection` lands on the first available section,
    // and DHEALTH-NATIVE-ACCESS-001 decided an administrator opens the application on
    // `Équipe et accès`. The test below is what holds that.
    expect(idsFor(provider(['ORG_ADMIN']))).toEqual(['team', 'prices'])
    expect(idsFor(lab(['ORG_ADMIN']))).toEqual(['team', 'prices'])
  })

  it('never offers the operational till to somebody who cannot take a payment', () => {
    // §27's correction stated as its own invariant rather than left implicit in the list above.
    // `Facturation` is `billing.operate`; nothing else opens it.
    expect(idsFor(provider(['ORG_ADMIN']))).not.toContain('billing')
    expect(idsFor(provider(['CLINICIAN']))).not.toContain('billing')
    expect(idsFor(provider(['BILLING_CLERK']))).toContain('billing')
  })

  it('offers the till to somebody granted it directly, holding no cashier role', () => {
    // **CAP-1's frontend half**, and the test NC-CAP-7 has to redden.
    //
    // Every other case in this file happens to have capabilities that match its roles, so a
    // navigation rule written as `roles.includes('BILLING_CLERK')` would pass all of them. This is
    // the one that tells them apart: an administrator granted `billing.operate` individually holds
    // no billing role at all, and the section must still appear -- because the server will serve
    // them, and a navigation that hid it would be hiding authority the product actually granted.
    const granted: ExchangeMembershipSummary = {
      ...provider(['ORG_ADMIN']),
      capabilities: [...capabilitiesForRoles(['ORG_ADMIN']), 'billing.operate'],
    }
    expect(idsFor(granted)).toContain('billing')
  })

  it('hides the till from a cashier whose capability was individually denied', () => {
    // The mirror, and CAP-2's frontend half. The role is still `BILLING_CLERK`; the server resolved
    // the deny and sent no `billing.operate`, so the section is gone. A role-derived navigation
    // would show it and every control on it would be refused.
    const denied: ExchangeMembershipSummary = {
      ...provider(['BILLING_CLERK']),
      capabilities: [],
    }
    expect(idsFor(denied)).not.toContain('billing')
  })

  it('offers both surfaces to somebody who genuinely holds both jobs', () => {
    // The composition case, which is the whole point of capabilities: a person who both runs the
    // till and sets the prices sees both, and that is the honest rendering of holding both.
    expect(idsFor(provider(['ORG_ADMIN', 'BILLING_CLERK']))).toEqual([
      'team',
      'billing',
      'prices',
    ])
  })

  it('still lands an administrator with no clinical role directly on the team section', () => {
    // DHEALTH-NATIVE-ACCESS-001's decision, re-pinned because DHEALTH-BILLING-001 nearly
    // moved it by accident: `Facturation` was ordered before `Équipe et accès` at first,
    // which silently made the till an administrator's landing page. `07-access-lifecycle`
    // asserts this same thing in a real browser immediately after sign-in.
    const sections = availableSections(
      provider(['ORG_ADMIN']),
      capabilitiesFor(provider(['ORG_ADMIN'])),
    )
    expect(resolveSection(null, sections)).toBe('team')
    expect(sections.map((section) => section.id)).toContain('prices')
  })

  it('puts the team section last for an administrator who also works clinically', () => {
    // Administration is not what they open the application for, and neither is the
    // price list, so `Facturation` follows it.
    expect(idsFor(provider(['CLINICIAN', 'ORG_ADMIN']))).toEqual([
      'today',
      'visits',
      'patients',
      'orders',
      'results',
      'team',
      'prices',
    ])
    expect(idsFor(lab(['LAB_TECH', 'ORG_ADMIN']))).toEqual([
      'today',
      'orders',
      'team',
      'prices',
    ])
  })

  it('offers a cashier the till and nothing else, in either kind of facility', () => {
    // DHEALTH-BILLING-001/ADR-066 §2, as navigation. A BILLING_CLERK holds that role
    // and nothing else by design, so this is also the membership that would have been
    // signed in and shown no workspace at all had `isUsableMembership` not learned
    // about the role -- the same defect DHEALTH-NATIVE-ACCESS-001 fixed for ORG_ADMIN.
    expect(idsFor(provider(['BILLING_CLERK']))).toEqual(['billing'])
    expect(idsFor(lab(['BILLING_CLERK']))).toEqual(['billing'])
  })

  it('never offers a cashier a clinical section, in either direction', () => {
    const clerk = idsFor(provider(['BILLING_CLERK']))
    for (const forbidden of ['today', 'visits', 'patients', 'orders', 'results', 'team']) {
      expect(clerk).not.toContain(forbidden)
    }
    // And the clinician gets no till: billing authority is not implied by clinical
    // authority any more than the reverse.
    expect(idsFor(provider(['CLINICIAN']))).not.toContain('billing')
    expect(idsFor(lab(['LAB_TECH', 'LAB_RESULT_RELEASER']))).not.toContain('billing')
  })

  it('never offers the team section to somebody who is not an ORG_ADMIN', () => {
    expect(idsFor(provider(['CLINICIAN']))).not.toContain('team')
    expect(idsFor(lab(['LAB_TECH', 'LAB_RESULT_RELEASER']))).not.toContain('team')
  })

  it('drops the team section when ORG_ADMIN is removed, so a stale id cannot survive', () => {
    // The same mechanism that stops a Patients page rendering against a laboratory:
    // an administrator whose ORG_ADMIN was just revoked, standing in `team`, must be
    // moved off it rather than left on a screen the server now refuses.
    const afterRevocation = availableSections(
      provider(['CLINICIAN']),
      capabilitiesFor(provider(['CLINICIAN'])),
    )
    expect(resolveSection('team', afterRevocation)).toBe('today')
  })

  it('offers nothing at all when there is no active organization', () => {
    expect(availableSections(null, NO_CAPABILITIES)).toEqual([])
  })

  it('never includes a Réglages section', () => {
    // Facility setup and administration are DH-MVP-ADMIN-001's surface. Listing
    // the item before it does anything would ship exactly the dead navigation
    // entry this product forbids.
    for (const membership of [provider(), lab(['LAB_TECH', 'LAB_RESULT_RELEASER'])]) {
      const labels = availableSections(membership, capabilitiesFor(membership)).map((s) => s.label)
      expect(labels).not.toContain('Réglages')
      expect(labels).not.toContain('Paramètres')
    }
  })

  it('labels sections in French, with the laboratory qualifier dropped', () => {
    expect(sectionLabel('today')).toBe("Aujourd'hui")
    expect(sectionLabel('orders')).toBe('Ordres')
    expect(sectionLabel('results')).toBe('Résultats')
    expect(sectionLabel('patients')).toBe('Patients')
    // DHEALTH-CLINIC-VISIT-001: "Visites", deliberately not "Consultations" -- a visit exists from
    // the moment somebody walks in, which is before any consultation has happened.
    expect(sectionLabel('visits')).toBe('Visites')
  })
})

describe('resolveSection', () => {
  const providerSections = availableSections(provider(), capabilitiesFor(provider()))
  const techSections = availableSections(lab(['LAB_TECH']), capabilitiesFor(lab(['LAB_TECH'])))

  it('keeps a section that still exists', () => {
    expect(resolveSection('orders', providerSections)).toBe('orders')
  })

  it('falls back to the first section when the requested one is gone', () => {
    // The organization-switch case that matters: a clinician standing in Patients
    // who switches to a laboratory has no Patients section. Leaving the old id
    // active would render a provider page against a laboratory organization.
    expect(resolveSection('patients', techSections)).toBe('today')
  })

  it('falls back when nothing was requested yet', () => {
    expect(resolveSection(null, providerSections)).toBe('today')
  })

  it('resolves to nothing when no section is available', () => {
    expect(resolveSection('orders', [])).toBeNull()
  })
})

describe('DHEALTH-LAB-OFFERINGS-001 — who reaches Services et tarifs', () => {
  /**
   * The section is `Services et tarifs`, and a laboratory's *services* are `facility.configure`'s
   * subject rather than the price-setter's. So the entry is reachable through either capability.
   *
   * Pinned in both directions deliberately: before this ticket, a laboratory administrator holding
   * only `facility.configure` reached no configuration surface at all, and nothing would have
   * noticed if a later refactor took it away again.
   */
  it('reaches it through facility.configure alone', () => {
    const membership = { ...lab([]), capabilities: ['facility.configure'] }
    expect(idsFor(membership)).toEqual(['prices'])
  })

  it('still reaches it through billing.configure_prices alone', () => {
    const membership = { ...lab([]), capabilities: ['billing.configure_prices'] }
    expect(idsFor(membership)).toEqual(['prices'])
  })

  it('shows it exactly once to somebody holding both', () => {
    // Two capabilities, one section. An `ids.push` per capability without the guard would have
    // produced a duplicated navigation entry, which is the obvious way to get this wrong.
    const membership = {
      ...lab([]),
      capabilities: ['facility.configure', 'billing.configure_prices'],
    }
    expect(idsFor(membership)).toEqual(['prices'])
  })

  it('does not reach it on a laboratory authority that is neither', () => {
    // `lab.queue.work` works the queue and `lab.result.release` publishes results. Neither
    // configures the facility, and neither should be offered a configuration surface the server
    // would refuse.
    expect(idsFor({ ...lab([]), capabilities: ['lab.queue.work'] })).not.toContain('prices')
    expect(idsFor({ ...lab([]), capabilities: ['lab.result.release'] })).not.toContain('prices')
    expect(idsFor({ ...lab([]), capabilities: ['billing.operate'] })).not.toContain('prices')
  })
})
