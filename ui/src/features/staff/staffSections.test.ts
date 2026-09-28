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

function provider(roles: string[] = ['CLINICIAN']): ExchangeMembershipSummary {
  return {
    organization_id: 'prov-1',
    organization_name: 'Clinique de la Paix',
    organization_type: 'PROVIDER',
    organization_status: 'ACTIVE',
    membership_status: 'ACTIVE',
    roles,
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
    // DHEALTH-BILLING-001 added `billing` here, and it is a real screen rather than a
    // widening: `BILLING_CONFIGURE_PRICES` is ORG_ADMIN's own capability (ADR-066 §2),
    // so an administrator opening `Facturation` finds the price list -- what this
    // facility charges -- and no till. The reverse holds for a cashier, and
    // `never offers the till to an administrator` below pins that half.
    expect(idsFor(provider(['ORG_ADMIN']))).toEqual(['billing', 'team'])
    expect(idsFor(lab(['ORG_ADMIN']))).toEqual(['billing', 'team'])
  })

  it('lands an administrator with no clinical role on the first section they can use', () => {
    const sections = availableSections(
      provider(['ORG_ADMIN']),
      capabilitiesFor(provider(['ORG_ADMIN'])),
    )
    expect(resolveSection(null, sections)).toBe('billing')
    // ...and `Équipe et accès` is still reachable, which is what the landing change
    // must not cost them.
    expect(sections.map((section) => section.id)).toContain('team')
  })

  it('puts the team section last for an administrator who also works clinically', () => {
    // Administration is not what they open the application for. `Facturation` sits
    // between the clinical sections and administration: money follows the work that
    // produced it, and precedes administering the establishment.
    expect(idsFor(provider(['CLINICIAN', 'ORG_ADMIN']))).toEqual([
      'today',
      'visits',
      'patients',
      'orders',
      'results',
      'billing',
      'team',
    ])
    expect(idsFor(lab(['LAB_TECH', 'ORG_ADMIN']))).toEqual([
      'today',
      'orders',
      'billing',
      'team',
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
