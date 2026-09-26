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

  // ── DHEALTH-NATIVE-ACCESS-001: Équipe et accès ─────────────────────────────

  it('offers an ORG_ADMIN-only membership the team section and nothing else', () => {
    // Not `['today', 'team']`. `Aujourd'hui` is the clinical/laboratory work relay
    // and would render empty for somebody who holds no clinical role -- the dead
    // navigation entry this module's own rule forbids, reached from the other side.
    expect(idsFor(provider(['ORG_ADMIN']))).toEqual(['team'])
    expect(idsFor(lab(['ORG_ADMIN']))).toEqual(['team'])
  })

  it('lands an administrator with no clinical role directly on the team section', () => {
    const sections = availableSections(
      provider(['ORG_ADMIN']),
      capabilitiesFor(provider(['ORG_ADMIN'])),
    )
    expect(resolveSection(null, sections)).toBe('team')
  })

  it('puts the team section last for an administrator who also works clinically', () => {
    // Administration is not what they open the application for.
    expect(idsFor(provider(['CLINICIAN', 'ORG_ADMIN']))).toEqual([
      'today',
      'patients',
      'orders',
      'results',
      'team',
    ])
    expect(idsFor(lab(['LAB_TECH', 'ORG_ADMIN']))).toEqual(['today', 'orders', 'team'])
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
