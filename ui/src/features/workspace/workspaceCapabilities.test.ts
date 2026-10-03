/**
 * DHEALTH-ACCESS-CAPABILITIES-001 — the frontend authority model, tested directly.
 *
 * ── What these tests are now about ──
 *
 * They used to assert a *mapping*: that `roles: ['CLINICIAN']` produced `canViewPatients: true`,
 * and so on. That mapping is gone, and rightly: it was a second implementation of DHealth's
 * authorization rule, written in TypeScript, kept in step with the Python one by hand.
 *
 * So the subject changed. What matters now is that this module **faithfully reports what the server
 * sent and derives nothing** — because the moment it derives something, a capability granted
 * individually (which no role implies) becomes invisible to the interface, and the screen and the
 * server start disagreeing about who may do what.
 *
 * Per §58, these were tightened rather than deleted: every boundary the old tests guarded still has
 * a test, stated against the actual authority invariant instead of against a role name.
 */

import { describe, expect, it } from 'vitest'
import type { ExchangeMembershipSummary } from '../../api/exchangeTypes'
import {
  Capability,
  NO_CAPABILITIES,
  can,
  canAny,
  capabilitiesFor,
  isUsableMembership,
  usableMemberships,
} from './workspaceCapabilities'

const CLINICIAN_CAPABILITIES = [
  Capability.PATIENT_DIRECTORY_READ,
  Capability.PATIENT_REGISTER,
  Capability.VISIT_READ,
  Capability.VISIT_DOCUMENT,
  Capability.ORDER_MANAGE,
  Capability.RESULT_REVIEW,
]

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
    capabilities: [...CLINICIAN_CAPABILITIES],
    ...overrides,
  }
}

describe('isUsableMembership', () => {
  it('accepts any active membership the server gave authority to', () => {
    expect(isUsableMembership(membership())).toBe(true)
    expect(
      isUsableMembership(
        membership({ roles: ['LAB_TECH'], capabilities: [Capability.LAB_QUEUE_WORK] }),
      ),
    ).toBe(true)
    expect(
      isUsableMembership(
        membership({ roles: ['BILLING_CLERK'], capabilities: [Capability.BILLING_OPERATE] }),
      ),
    ).toBe(true)
  })

  it('accepts a membership whose authority came from a direct grant and no role at all', () => {
    // The case the old role-list version could not express, and the reason it is gone. Somebody
    // granted one capability individually holds real authority the server will honour; a frontend
    // that decided usability from role names would have shown them an empty workspace.
    expect(
      isUsableMembership(membership({ roles: [], capabilities: [Capability.BILLING_OPERATE] })),
    ).toBe(true)
  })

  it('rejects a membership the server gave no authority', () => {
    // Replaces "rejects a role this build does not render". The old rule enumerated roles, so
    // every new role had to be remembered here -- and twice it was not, which is how an ORG_ADMIN
    // (DHEALTH-NATIVE-ACCESS-001) and later a BILLING_CLERK (DHEALTH-BILLING-001) each signed in
    // successfully and were shown nothing. "Has capabilities" cannot go stale that way.
    expect(isUsableMembership(membership({ roles: ['RECEPTION'], capabilities: [] }))).toBe(false)
    expect(isUsableMembership(membership({ roles: [], capabilities: [] }))).toBe(false)
  })

  it('rejects a suspended organization and a non-active membership', () => {
    expect(isUsableMembership(membership({ organization_status: 'SUSPENDED' }))).toBe(false)
    expect(isUsableMembership(membership({ membership_status: 'REVOKED' }))).toBe(false)
    expect(isUsableMembership(membership({ membership_status: 'INVITED' }))).toBe(false)
  })

  it('rejects an inactive membership even when the server sent capabilities', () => {
    // Belt and braces. The server already empties the list for an inactive membership, so this
    // pins the *frontend's* own refusal -- a defence that still holds if a future response shape
    // ever reports configured rather than effective authority.
    expect(
      isUsableMembership(
        membership({ membership_status: 'REVOKED', capabilities: [Capability.BILLING_OPERATE] }),
      ),
    ).toBe(false)
  })
})

describe('usableMemberships', () => {
  it('drops every unusable one and preserves server order', () => {
    const admin = membership({
      organization_id: 'admin-only',
      roles: ['ORG_ADMIN'],
      capabilities: [Capability.STAFF_ACCESS_MANAGE],
    })
    const clinic = membership({ organization_id: 'prov-1' })
    const lab = membership({
      organization_id: 'lab-1',
      organization_type: 'LAB',
      roles: ['LAB_TECH'],
      capabilities: [Capability.LAB_QUEUE_WORK],
    })

    const result = usableMemberships([
      admin,
      clinic,
      membership({ organization_id: 'suspended', organization_status: 'SUSPENDED' }),
      lab,
    ])

    expect(result.map((m) => m.organization_id)).toEqual(['admin-only', 'prov-1', 'lab-1'])
  })
})

describe('capabilitiesFor', () => {
  it('reports exactly what the server sent, and adds nothing', () => {
    // The central property. Not "a clinician gets these six" -- that is the *server's* rule and
    // testing it here would recreate the mapping this module exists without. What this asserts is
    // that the list survives the trip unchanged.
    const capabilities = capabilitiesFor(membership())
    expect([...capabilities].sort()).toEqual([...CLINICIAN_CAPABILITIES].sort())
  })

  it('honours a capability no role of theirs implies', () => {
    // CAP-1's frontend half. A clinician granted `billing.operate` directly holds it; the old
    // role-derived version returned false here, because no clinician role implies the till.
    const capabilities = capabilitiesFor(
      membership({ capabilities: [...CLINICIAN_CAPABILITIES, Capability.BILLING_OPERATE] }),
    )
    expect(can(capabilities, Capability.BILLING_OPERATE)).toBe(true)
    expect(can(capabilities, Capability.VISIT_DOCUMENT)).toBe(true)
  })

  it('honours a capability individually denied despite the role', () => {
    // CAP-2's frontend half. The server resolved the deny and simply did not send the capability;
    // the role is still `BILLING_CLERK` and the interface must follow the authority, not the title.
    const capabilities = capabilitiesFor(
      membership({ roles: ['BILLING_CLERK'], capabilities: [] }),
    )
    expect(can(capabilities, Capability.BILLING_OPERATE)).toBe(false)
    expect(isUsableMembership(membership({ roles: ['BILLING_CLERK'], capabilities: [] }))).toBe(
      false,
    )
  })

  it('gives nothing for no membership, an unusable one, or a suspended organization', () => {
    // The failure direction is always "shows nothing", never "shows everything".
    expect(capabilitiesFor(null)).toBe(NO_CAPABILITIES)
    expect(capabilitiesFor(membership({ membership_status: 'REVOKED' }))).toBe(NO_CAPABILITIES)
    expect(capabilitiesFor(membership({ organization_status: 'SUSPENDED' }))).toBe(NO_CAPABILITIES)
  })

  it('reads the active membership only, never authority held elsewhere', () => {
    // §6/§35: a cashier at Clinique B must not see the till while Clinique A is active. The
    // function takes one membership precisely so there is nothing else it *could* read.
    const active = membership({ organization_id: 'prov-1' })
    const capabilities = capabilitiesFor(active)
    expect(can(capabilities, Capability.BILLING_OPERATE)).toBe(false)
  })
})

describe('can and canAny', () => {
  it('answer one capability and any-of', () => {
    const capabilities = capabilitiesFor(
      membership({ capabilities: [Capability.VISIT_READ, Capability.BILLING_OPERATE] }),
    )
    expect(can(capabilities, Capability.VISIT_READ)).toBe(true)
    expect(can(capabilities, Capability.STAFF_ACCESS_MANAGE)).toBe(false)
    expect(canAny(capabilities, Capability.STAFF_ACCESS_MANAGE, Capability.BILLING_OPERATE)).toBe(
      true,
    )
    expect(canAny(capabilities, Capability.STAFF_ACCESS_MANAGE, Capability.LAB_QUEUE_WORK)).toBe(
      false,
    )
  })

  it('answers false for everything when nothing is held', () => {
    for (const capability of Object.values(Capability)) {
      expect(can(NO_CAPABILITIES, capability)).toBe(false)
    }
  })
})

describe('the catalog mirrors the server', () => {
  it('names every capability the adapter defines, with the adapter values', () => {
    // A typo here would be a permission check that is silently always false -- the worst kind,
    // because the screen would simply hide something and nobody would see an error. The values are
    // the contract with `adapter/src/daher_adapter/exchange/capabilities.py`; the adapter's own
    // `test_the_frontend_catalog_matches_the_backend_catalog` asserts the other direction.
    expect(Object.values(Capability).sort()).toEqual(
      [
        // DHEALTH-APPOINTMENTS-001/ADR-073. In no role template: a clinician who should book is
        // granted it explicitly, so this entry existing is not authority anybody holds by default.
        'appointment.manage',
        'billing.configure_prices',
        'billing.operate',
        'facility.configure',
        'lab.queue.work',
        'lab.result.release',
        'order.manage',
        'patient.directory.read',
        'patient.register',
        'result.review',
        'staff.access.manage',
        // DHEALTH-FRONT-DESK-001/ADR-069 split the arrival out of `visit.document`, so a person may
        // register arrivals without any authority over the consultation that follows.
        'visit.check_in',
        'visit.document',
        'visit.read',
      ].sort(),
    )
  })
})
