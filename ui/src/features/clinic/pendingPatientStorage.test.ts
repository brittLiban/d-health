import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearAllStoredPendingPatients,
  clearStoredPendingPatient,
  getStoredPendingPatient,
  setStoredPendingPatient,
} from './pendingPatientStorage'

describe('pendingPatientStorage', () => {
  beforeEach(() => {
    localStorage.clear()
    clearAllStoredPendingPatients()
    vi.restoreAllMocks()
  })

  it('persists and retrieves opaque pending identity tuple', () => {
    setStoredPendingPatient('user-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: '123e4567-e89b-12d3-a456-426614174000',
    })

    const stored = getStoredPendingPatient('user-1', 'provider-a')
    expect(stored).not.toBeNull()
    expect(stored?.source_system).toBe('dhealth-clinic')
    expect(stored?.source_identity_system).toBe('urn:dhealth:clinic:patient-reference-id')
    expect(stored?.source_identity_value).toBe('123e4567-e89b-12d3-a456-426614174000')
    expect(typeof stored?.created_at).toBe('number')
  })

  it('enforces strict tenant and user isolation', () => {
    setStoredPendingPatient('user-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: 'uuid-user1-prova',
    })

    // Different provider cannot access user-1's provider-a token
    expect(getStoredPendingPatient('user-1', 'provider-b')).toBeNull()

    // Different user cannot access user-1's provider-a token
    expect(getStoredPendingPatient('user-2', 'provider-a')).toBeNull()

    // User-1 accessing provider-a still gets their token
    expect(getStoredPendingPatient('user-1', 'provider-a')?.source_identity_value).toBe('uuid-user1-prova')
  })

  it('expires pending identity after TTL (30 minutes)', () => {
    const now = 1000000000000
    vi.spyOn(Date, 'now').mockReturnValue(now)

    setStoredPendingPatient('user-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: 'uuid-ttl-test',
      created_at: now,
    })

    expect(getStoredPendingPatient('user-1', 'provider-a')).not.toBeNull()

    // Advance 31 minutes
    vi.spyOn(Date, 'now').mockReturnValue(now + 31 * 60 * 1000)

    // Should return null and clean up expired entry
    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v1:user-1:provider-a')).toBeNull()
  })

  it('clears individual scoped entries', () => {
    setStoredPendingPatient('user-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: 'uuid-1',
    })
    setStoredPendingPatient('user-1', 'provider-b', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: 'uuid-2',
    })

    clearStoredPendingPatient('user-1', 'provider-a')
    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()
    expect(getStoredPendingPatient('user-1', 'provider-b')).not.toBeNull()
  })

  it('purges all pending entries on clearAllStoredPendingPatients (logout)', () => {
    setStoredPendingPatient('user-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: 'uuid-1',
    })
    setStoredPendingPatient('user-2', 'provider-b', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: 'uuid-2',
    })
    localStorage.setItem('other_app_key', 'keep_me')

    clearAllStoredPendingPatients()

    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()
    expect(getStoredPendingPatient('user-2', 'provider-b')).toBeNull()
    expect(localStorage.getItem('other_app_key')).toBe('keep_me')
  })

  it('handles malformed JSON or corrupted storage gracefully', () => {
    localStorage.setItem('dhealth_clinic_pending_patient_v1:user-1:provider-a', '{invalid json}')
    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()

    localStorage.setItem(
      'dhealth_clinic_pending_patient_v1:user-1:provider-a',
      JSON.stringify({ source_system: 123 }), // corrupted types
    )
    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()
  })
})
