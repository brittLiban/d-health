import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearAllStoredPendingPatients,
  clearStoredPendingPatient,
  getStoredPendingPatient,
  PendingPatientStorageReadError,
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

  it('retains an unresolved identity beyond the former 30-minute TTL', () => {
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

    expect(getStoredPendingPatient('user-1', 'provider-a')?.source_identity_value).toBe('uuid-ttl-test')
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v1:user-1:provider-a')).not.toBeNull()
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

  it('returns null when storage key is truly absent', () => {
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v1:user-1:provider-a')).toBeNull()
    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()
  })

  it('fails closed on malformed JSON and leaves raw entry intact', () => {
    const rawMalformed = '{not valid json at all'
    localStorage.setItem('dhealth_clinic_pending_patient_v1:user-1:provider-a', rawMalformed)

    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    // Raw record must NOT be deleted
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v1:user-1:provider-a')).toBe(rawMalformed)
  })

  it('fails closed on wrong required field types and leaves raw entry intact', () => {
    const rawCorruptTypes = JSON.stringify({
      source_system: 12345, // invalid type
      source_identity_system: true,
      source_identity_value: {},
      created_at: '2026-09-15',
    })
    localStorage.setItem('dhealth_clinic_pending_patient_v1:user-1:provider-a', rawCorruptTypes)

    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    // Raw record must NOT be deleted
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v1:user-1:provider-a')).toBe(rawCorruptTypes)
  })

  it('fails closed on missing required source identity value and leaves raw entry intact', () => {
    const rawMissingIdentity = JSON.stringify({
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      // source_identity_value is missing
      created_at: Date.now(),
    })
    localStorage.setItem('dhealth_clinic_pending_patient_v1:user-1:provider-a', rawMissingIdentity)

    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    // Raw record must NOT be deleted
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v1:user-1:provider-a')).toBe(rawMissingIdentity)
  })

  it('never deletes a malformed record across repeated reads', () => {
    const rawCorrupt = JSON.stringify({ arbitrary_broken_data: true })
    localStorage.setItem('dhealth_clinic_pending_patient_v1:user-1:provider-a', rawCorrupt)

    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v1:user-1:provider-a')).toBe(rawCorrupt)
  })
})
