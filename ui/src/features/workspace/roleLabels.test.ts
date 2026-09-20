/**
 * DH-MVP-POLISH-001 — the role labels, pinned.
 *
 * These exist because the masthead shipped `roles.join(' · ')` and put
 * `LAB_TECH · LAB_RESULT_RELEASER` in front of the user. The point of the module
 * is that the authorization enum stays internal, so the test that matters most is
 * the one asserting no known enum value can reach the screen.
 */

import { describe, expect, it } from 'vitest'
import { formatRoleLabels, roleLabel } from './roleLabels'

const KNOWN_ROLES = ['CLINICIAN', 'LAB_TECH', 'LAB_RESULT_RELEASER', 'ORG_ADMIN'] as const

describe('role labels', () => {
  it('names every role the backend defines', () => {
    // The full enum from the adapter's own `ExchangeRole`. If a fifth role is
    // added there, this list should grow with it and the mapping with it.
    expect(roleLabel('CLINICIAN')).toBe('Clinicien')
    expect(roleLabel('LAB_TECH')).toBe('Analyse')
    expect(roleLabel('LAB_RESULT_RELEASER')).toBe('Libération')
    expect(roleLabel('ORG_ADMIN')).toBe('Administration')
  })

  it('leaks no known enum value to the screen', () => {
    for (const role of KNOWN_ROLES) {
      expect(roleLabel(role)).not.toMatch(/[A-Z]{2,}_|^[A-Z_]+$/)
    }
    expect(formatRoleLabels(KNOWN_ROLES)).not.toMatch(/_/)
  })

  it('keeps entry and release as two separate authorities', () => {
    // ADR-044. A dual-role user holds both; neither implies the other, and the
    // label must not merge them into one "laboratoire" role.
    expect(formatRoleLabels(['LAB_TECH', 'LAB_RESULT_RELEASER'])).toBe('Analyse · Libération')
  })

  it('reads the same regardless of the order the API returned', () => {
    // A stable presentation order, so the identity band does not reshuffle
    // between two responses that carry the same authorities.
    expect(formatRoleLabels(['LAB_RESULT_RELEASER', 'LAB_TECH'])).toBe('Analyse · Libération')
    expect(formatRoleLabels(['ORG_ADMIN', 'CLINICIAN'])).toBe('Clinicien · Administration')
  })

  it('falls back to the raw value for a role it does not know', () => {
    // Deliberate: an unlabelled role showing its code is a visible prompt to name
    // it, whereas dropping it would silently hide an authority the person holds.
    expect(roleLabel('FUTURE_ROLE')).toBe('FUTURE_ROLE')
    expect(formatRoleLabels(['CLINICIAN', 'FUTURE_ROLE'])).toBe('Clinicien · FUTURE_ROLE')
  })

  it('says nothing when there is nothing to say', () => {
    expect(formatRoleLabels([])).toBe('')
  })
})
