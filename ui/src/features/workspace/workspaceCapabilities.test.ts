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

  // DHEALTH-NATIVE-ACCESS-001 reversed these two. They previously asserted that an
  // ORG_ADMIN-only membership was *not* usable, on the stated grounds that
  // "administration is a later ticket's surface, and listing such an organization
  // here would offer a workspace with nothing in it". The second half is no longer
  // true: Équipe et accès is a real screen. Rewritten rather than deleted, because
  // the boundary they guard still matters -- what makes an ORG_ADMIN usable is that
  // there is now somewhere for them to go, not that administration became clinical
  // authority.
  it('accepts a provider membership holding only ORG_ADMIN, which now has a screen', () => {
    expect(isUsableMembership(membership({ roles: ['ORG_ADMIN'] }))).toBe(true)
  })

  it('accepts a laboratory membership holding only ORG_ADMIN', () => {
    expect(
      isUsableMembership(membership({ organization_type: 'LAB', roles: ['ORG_ADMIN'] })),
    ).toBe(true)
  })

  it('still rejects a membership whose only role unlocks nothing this build renders', () => {
    // The rule is unchanged in substance: a role with no surface behind it does not
    // make an organization usable. Pinned with a value the backend does not define,
    // so the check cannot pass merely because every real role happens to be usable.
    expect(isUsableMembership(membership({ roles: ['RECEPTION'] }))).toBe(false)
    expect(isUsableMembership(membership({ roles: [] }))).toBe(false)
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
      // Was the unusable case here before DHEALTH-NATIVE-ACCESS-001; now kept, and
      // kept *first*, which also pins that server order is preserved rather than
      // reordered by usability.
      membership({ organization_id: 'admin-only', roles: ['ORG_ADMIN'] }),
      kept1,
      membership({ organization_id: 'suspended', organization_status: 'SUSPENDED' }),
      kept2,
    ])

    expect(result.map((m) => m.organization_id)).toEqual(['admin-only', 'prov-1', 'lab-1'])
  })
})

describe('capabilitiesFor', () => {
  it('grants a provider clinician the four provider capabilities and no laboratory work', () => {
    expect(capabilitiesFor(membership())).toEqual({
      canViewPatients: true,
      canCreateOrders: true,
      canReviewResults: true,
      // DHEALTH-CLINIC-VISIT-001: the visit lifecycle resolves from the same CLINICIAN grant.
      canRunVisits: true,
      canWorkLabOrders: false,
      canReleaseResults: false,
      // DHEALTH-NATIVE-ACCESS-001: a clinician who is not also an ORG_ADMIN gets no
      // administration surface. Pinned in the exhaustive shape on purpose -- this is
      // the test that would catch a capability being granted by default.
      canAdministerTeam: false,
      // DHEALTH-BILLING-001/ADR-066 §2: clinical authority does not imply the till,
      // and it does not imply setting prices either. A clinician at a one-person
      // clinic who is genuinely also the cashier is granted BILLING_CLERK; they do
      // not get it for free by being a clinician.
      canOperateBilling: false,
      canConfigurePrices: false,
    })
  })

  it('separates running the till from setting prices (ADR-066 §2)', () => {
    // The oldest problem in facility billing is somebody who can quietly change a
    // price *and* take the payment, so the two are different capabilities held by
    // different roles. Mirrors `CAPABILITY_ROLE_GRANTS` in the adapter, which is the
    // source of truth; this test is what catches the mirror drifting from it.
    const clerk = capabilitiesFor(membership({ roles: ['BILLING_CLERK'] }))
    expect(clerk.canOperateBilling).toBe(true)
    expect(clerk.canConfigurePrices).toBe(false)

    const admin = capabilitiesFor(membership({ roles: ['ORG_ADMIN'] }))
    expect(admin.canConfigurePrices).toBe(true)
    expect(admin.canOperateBilling).toBe(false)
  })

  it('gives a cashier no clinical authority whatsoever', () => {
    const clerk = capabilitiesFor(membership({ roles: ['BILLING_CLERK'] }))
    expect(clerk.canViewPatients).toBe(false)
    expect(clerk.canCreateOrders).toBe(false)
    expect(clerk.canReviewResults).toBe(false)
    expect(clerk.canRunVisits).toBe(false)
    expect(clerk.canWorkLabOrders).toBe(false)
    expect(clerk.canReleaseResults).toBe(false)
    expect(clerk.canAdministerTeam).toBe(false)
  })

  it('grants the till at a laboratory as well as at a provider', () => {
    // `ALLOWED_ROLES_BY_ORG_TYPE` lists BILLING_CLERK for both, and a bill is the
    // same kind of record either way -- unlike the clinical capabilities above,
    // which are deliberately gated on organization type.
    const labClerk = capabilitiesFor(
      membership({ organization_type: 'LAB', roles: ['BILLING_CLERK'] }),
    )
    expect(labClerk.canOperateBilling).toBe(true)
    expect(labClerk.canWorkLabOrders).toBe(false)
    expect(labClerk.canReleaseResults).toBe(false)
  })

  it('treats a cashier-only membership as usable, so they are not shown an empty workspace', () => {
    // The defect this pins is the one DHEALTH-NATIVE-ACCESS-001 found for ORG_ADMIN:
    // a membership whose only role the frontend did not recognise signed in
    // successfully and landed on nothing at all.
    expect(isUsableMembership(membership({ roles: ['BILLING_CLERK'] }))).toBe(true)
    expect(
      isUsableMembership(membership({ organization_type: 'LAB', roles: ['BILLING_CLERK'] })),
    ).toBe(true)
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
    expect(lab.canRunVisits).toBe(false)
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

  // ── DHEALTH-NATIVE-ACCESS-001 ────────────────────────────────────────────────

  it('grants canAdministerTeam to an ORG_ADMIN at either organization type', () => {
    expect(capabilitiesFor(membership({ roles: ['ORG_ADMIN'] })).canAdministerTeam).toBe(true)
    expect(
      capabilitiesFor(membership({ organization_type: 'LAB', roles: ['ORG_ADMIN'] }))
        .canAdministerTeam,
    ).toBe(true)
  })

  it('keeps administration separate from every clinical authority', () => {
    // Both directions. An administrator is not a clinician: ADR-044 and
    // `require_clinician`/`require_lab_result_releaser` refuse an ORG_ADMIN who holds
    // only that, and this derivation must not offer what the server refuses.
    const admin = capabilitiesFor(membership({ roles: ['ORG_ADMIN'] }))
    expect(admin.canViewPatients).toBe(false)
    expect(admin.canCreateOrders).toBe(false)
    expect(admin.canReviewResults).toBe(false)
    expect(admin.canWorkLabOrders).toBe(false)
    expect(admin.canReleaseResults).toBe(false)

    const clinician = capabilitiesFor(membership({ roles: ['CLINICIAN'] }))
    expect(clinician.canAdministerTeam).toBe(false)
    const releaser = capabilitiesFor(
      membership({ organization_type: 'LAB', roles: ['LAB_TECH', 'LAB_RESULT_RELEASER'] }),
    )
    expect(releaser.canAdministerTeam).toBe(false)
  })

  it('grants both when one person holds both, without merging them', () => {
    const both = capabilitiesFor(membership({ roles: ['CLINICIAN', 'ORG_ADMIN'] }))
    expect(both.canAdministerTeam).toBe(true)
    expect(both.canCreateOrders).toBe(true)
  })

  it('grants no administration for a membership that is not usable in the first place', () => {
    expect(
      capabilitiesFor(membership({ roles: ['ORG_ADMIN'], membership_status: 'REVOKED' }))
        .canAdministerTeam,
    ).toBe(false)
    expect(
      capabilitiesFor(membership({ roles: ['ORG_ADMIN'], organization_status: 'SUSPENDED' }))
        .canAdministerTeam,
    ).toBe(false)
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
