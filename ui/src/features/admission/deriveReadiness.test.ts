import { describe, expect, it } from 'vitest'
import { deriveReadiness } from './deriveReadiness'
import type { OpenMrsSession } from '../../api/types'

describe('deriveReadiness', () => {
  it('READY — authenticated, holds Daher Clinician Admission, and currentProvider is set', () => {
    const session: OpenMrsSession = {
      authenticated: true,
      user: { uuid: 'u1', roles: [{ name: 'Daher Clinician Admission' }, { name: 'Provider' }] },
      currentProvider: { uuid: 'provider-uuid' },
    }
    expect(deriveReadiness(session)).toEqual({ status: 'READY' })
  })

  it('PROVIDER_MISSING — holds the admission role but currentProvider is null (ADR-017E2 fail-closed default)', () => {
    const session: OpenMrsSession = {
      authenticated: true,
      user: { uuid: 'u1', roles: [{ name: 'Daher Clinician Admission' }] },
      currentProvider: null,
    }
    expect(deriveReadiness(session)).toEqual({ status: 'PROVIDER_MISSING' })
  })

  it('NOT_ELIGIBLE — Daher Reception does not hold the admission role', () => {
    const session: OpenMrsSession = {
      authenticated: true,
      user: { uuid: 'u1', roles: [{ name: 'Daher Reception' }] },
      currentProvider: null,
    }
    expect(deriveReadiness(session)).toEqual({ status: 'NOT_ELIGIBLE' })
  })

  it('NOT_ELIGIBLE — an unauthenticated session, even with a stale currentProvider value', () => {
    const session: OpenMrsSession = {
      authenticated: false,
      currentProvider: { uuid: 'stale-uuid' },
    }
    expect(deriveReadiness(session)).toEqual({ status: 'NOT_ELIGIBLE' })
  })

  it('NOT_ELIGIBLE — no roles array at all', () => {
    const session: OpenMrsSession = { authenticated: true }
    expect(deriveReadiness(session)).toEqual({ status: 'NOT_ELIGIBLE' })
  })

  it('never treats broad privilege (Organizational: Nurse) alone as the admission role', () => {
    const session: OpenMrsSession = {
      authenticated: true,
      user: { uuid: 'u1', roles: [{ name: 'Organizational: Nurse' }, { name: 'Provider' }] },
      currentProvider: { uuid: 'provider-uuid' },
    }
    expect(deriveReadiness(session)).toEqual({ status: 'NOT_ELIGIBLE' })
  })
})
