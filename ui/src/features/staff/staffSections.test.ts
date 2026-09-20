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
  it('offers a provider clinician all four sections, in navigation order', () => {
    expect(idsFor(provider())).toEqual(['today', 'patients', 'orders', 'results'])
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
