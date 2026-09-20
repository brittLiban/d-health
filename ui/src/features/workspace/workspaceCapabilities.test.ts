/**
 * DH-MVP-POLISH-001 — the eligibility and capability derivations, tested directly.
 *
 * The US suite covers these through the DOM, which proves the product behaves.
 * These state the rules themselves, because they are where a future regression
 * lands and because several of the cases are awkward to stage through a rendered
 * shell: an unknown organization type, or roles held at a *different*
 * organization than the active one.
 */

import { describe, expect, it } from 'vitest'
import type { ExchangeMembershipSummary } from '../../api/exchangeTypes'
import {
  NO_CAPABILITIES,
  capabilitiesFor,
  isUsableMembership,
  usableMemberships,
} from './workspaceCapabilities'

function membership(
  overrides: Partial<ExchangeMembershipSummary> = {},
): ExchangeMembershipSummary {
  return {
    organization_id: 'org-1',
    organization_name: 'Clinique de la Paix',
    organization_type: 'PROVIDER',
    organization_status: 'ACTIVE',
    membership_status: 'ACTIVE',
    roles: ['CLINICIAN'],
    ...overrides,
  }
}

describe('isUsableMembership', () => {
  it('accepts an active clinician membership at an active provider', () => {
    expect(isUsableMembership(membership())).toBe(true)
  })

  it('accepts a laboratory membership holding either laboratory role', () => {
    const lab = { organization_type: 'LAB' }
    expect(isUsableMembership(membership({ ...lab, roles: ['LAB_TECH'] }))).toBe(true)
    expect(isUsableMembership(membership({ ...lab, roles: ['LAB_RESULT_RELEASER'] }))).toBe(true)
    expect(
      isUsableMembership(membership({ ...lab, roles: ['LAB_TECH', 'LAB_RESULT_RELEASER'] })),
    ).toBe(true)
  })

  it('rejects a provider membership that holds no clinical role', () => {
    // An ORG_ADMIN who is not also a clinician carries no authority the server
    // would honour for patients, orders or review. Listing the organization would
    // offer a workspace with nothing in it.
    expect(isUsableMembership(membership({ roles: ['ORG_ADMIN'] }))).toBe(false)
  })

  it('rejects a laboratory membership that holds only ORG_ADMIN', () => {
    expect(
      isUsableMembership(membership({ organization_type: 'LAB', roles: ['ORG_ADMIN'] })),
    ).toBe(false)
  })

  it('rejects a suspended organization and a non-active membership', () => {
    expect(isUsableMembership(membership({ organization_status: 'SUSPENDED' }))).toBe(false)
    expect(isUsableMembership(membership({ membership_status: 'REVOKED' }))).toBe(false)
    expect(isUsableMembership(membership({ membership_status: 'INVITED' }))).toBe(false)
  })

  it('rejects an organization type this build cannot render, without guessing', () => {
    // Not an error and not something to infer a workflow for: it simply offers
    // nothing here. A future type must be added deliberately.
    expect(isUsableMembership(membership({ organization_type: 'PHARMACY' }))).toBe(false)
  })
})

describe('usableMemberships', () => {
  it('keeps both organization types and drops every unusable one, preserving order', () => {
    const kept1 = membership({ organization_id: 'prov-1' })
    const kept2 = membership({
      organization_id: 'lab-1',
      organization_type: 'LAB',
      roles: ['LAB_TECH'],
    })

    const result = usableMemberships([
      membership({ organization_id: 'admin-only', roles: ['ORG_ADMIN'] }),
      kept1,
      membership({ organization_id: 'suspended', organization_status: 'SUSPENDED' }),
      kept2,
    ])

    expect(result.map((m) => m.organization_id)).toEqual(['prov-1', 'lab-1'])
  })
})

describe('capabilitiesFor', () => {
  it('grants a provider clinician the three provider capabilities and no laboratory work', () => {
    expect(capabilitiesFor(membership())).toEqual({
      canViewPatients: true,
      canCreateOrders: true,
      canReviewResults: true,
      canWorkLabOrders: false,
      canReleaseResults: false,
    })
  })

  it('keeps result entry and result release as separate authorities (ADR-044)', () => {
    const tech = capabilitiesFor(
      membership({ organization_type: 'LAB', roles: ['LAB_TECH'] }),
    )
    expect(tech.canWorkLabOrders).toBe(true)
    expect(tech.canReleaseResults).toBe(false)

    const releaser = capabilitiesFor(
      membership({ organization_type: 'LAB', roles: ['LAB_RESULT_RELEASER'] }),
    )
    expect(releaser.canWorkLabOrders).toBe(false)
    expect(releaser.canReleaseResults).toBe(true)
  })

  it('grants a laboratory membership no provider capability', () => {
    // A laboratory works from orders. The patient directory, order creation and
    // review are all `_require_clinician` at a provider.
    const lab = capabilitiesFor(
      membership({ organization_type: 'LAB', roles: ['LAB_TECH', 'LAB_RESULT_RELEASER'] }),
    )
    expect(lab.canViewPatients).toBe(false)
    expect(lab.canCreateOrders).toBe(false)
    expect(lab.canReviewResults).toBe(false)
  })

  it('derives nothing from roles held at a different organization', () => {
    // The whole point of deriving from the *active* membership. A clinician at
    // Clinique A who is also a technician at Laboratoire B must not be offered
    // laboratory work while Clinique A is active — that would misrepresent whose
    // authority the next request carries.
    const activeProvider = membership({
      organization_id: 'prov-1',
      roles: ['CLINICIAN'],
    })
    expect(capabilitiesFor(activeProvider).canWorkLabOrders).toBe(false)
    expect(capabilitiesFor(activeProvider).canReleaseResults).toBe(false)
  })

  it('grants nothing when there is no active organization', () => {
    expect(capabilitiesFor(null)).toEqual(NO_CAPABILITIES)
  })

  it('grants nothing for a membership that is not usable in the first place', () => {
    // Belt and braces: a caller that skipped the eligibility filter must not be
    // able to obtain capabilities from a revoked membership.
    expect(capabilitiesFor(membership({ membership_status: 'REVOKED' }))).toEqual(NO_CAPABILITIES)
    expect(capabilitiesFor(membership({ organization_status: 'SUSPENDED' }))).toEqual(
      NO_CAPABILITIES,
    )
  })
})
