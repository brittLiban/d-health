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

  it('persists and retrieves opaque pending identity', () => {
    setStoredPendingPatient('user-1', 'provider-a', {
      idempotency_key: '123e4567-e89b-12d3-a456-426614174000',
    })

    const stored = getStoredPendingPatient('user-1', 'provider-a')
    expect(stored).not.toBeNull()
    expect(stored?.idempotency_key).toBe('123e4567-e89b-12d3-a456-426614174000')
    expect(typeof stored?.created_at).toBe('number')
  })

  it('persists confirmed_patient_id and confirmed_patient_reference_id once set', () => {
    setStoredPendingPatient('user-1', 'provider-a', {
      idempotency_key: 'key-confirmed',
      confirmed_patient_id: 'patient-1',
      confirmed_patient_reference_id: 'reference-1',
    })

    const stored = getStoredPendingPatient('user-1', 'provider-a')
    expect(stored?.confirmed_patient_id).toBe('patient-1')
    expect(stored?.confirmed_patient_reference_id).toBe('reference-1')
  })

  it('never persists any demographic field -- no name, DOB, or contact', () => {
    setStoredPendingPatient('user-1', 'provider-a', { idempotency_key: 'key-no-phi' })
    const raw = localStorage.getItem('dhealth_clinic_pending_patient_v2:user-1:provider-a')
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string) as Record<string, unknown>
    expect(parsed).not.toHaveProperty('display_name')
    expect(parsed).not.toHaveProperty('date_of_birth')
    expect(parsed).not.toHaveProperty('contact_channel_type')
    expect(parsed).not.toHaveProperty('contact_value')
  })

  it('enforces strict tenant and user isolation', () => {
    setStoredPendingPatient('user-1', 'provider-a', { idempotency_key: 'uuid-user1-prova' })

    // Different provider cannot access user-1's provider-a token
    expect(getStoredPendingPatient('user-1', 'provider-b')).toBeNull()

    // Different user cannot access user-1's provider-a token
    expect(getStoredPendingPatient('user-2', 'provider-a')).toBeNull()

    // User-1 accessing provider-a still gets their token
    expect(getStoredPendingPatient('user-1', 'provider-a')?.idempotency_key).toBe('uuid-user1-prova')
  })

  it('retains an unresolved identity beyond the former 30-minute TTL', () => {
    const now = 1000000000000
    vi.spyOn(Date, 'now').mockReturnValue(now)

    setStoredPendingPatient('user-1', 'provider-a', {
      idempotency_key: 'uuid-ttl-test',
      created_at: now,
    })

    expect(getStoredPendingPatient('user-1', 'provider-a')).not.toBeNull()

    // Advance 31 minutes
    vi.spyOn(Date, 'now').mockReturnValue(now + 31 * 60 * 1000)

    expect(getStoredPendingPatient('user-1', 'provider-a')?.idempotency_key).toBe('uuid-ttl-test')
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v2:user-1:provider-a')).not.toBeNull()
  })

  it('clears individual scoped entries', () => {
    setStoredPendingPatient('user-1', 'provider-a', { idempotency_key: 'uuid-1' })
    setStoredPendingPatient('user-1', 'provider-b', { idempotency_key: 'uuid-2' })

    clearStoredPendingPatient('user-1', 'provider-a')
    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()
    expect(getStoredPendingPatient('user-1', 'provider-b')).not.toBeNull()
  })

  it('purges all pending entries on clearAllStoredPendingPatients (logout)', () => {
    setStoredPendingPatient('user-1', 'provider-a', { idempotency_key: 'uuid-1' })
    setStoredPendingPatient('user-2', 'provider-b', { idempotency_key: 'uuid-2' })
    localStorage.setItem('other_app_key', 'keep_me')

    clearAllStoredPendingPatients()

    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()
    expect(getStoredPendingPatient('user-2', 'provider-b')).toBeNull()
    expect(localStorage.getItem('other_app_key')).toBe('keep_me')
  })

  it('returns null when storage key is truly absent', () => {
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v2:user-1:provider-a')).toBeNull()
    expect(getStoredPendingPatient('user-1', 'provider-a')).toBeNull()
  })

  it('fails closed on malformed JSON and leaves raw entry intact', () => {
    const rawMalformed = '{not valid json at all'
    localStorage.setItem('dhealth_clinic_pending_patient_v2:user-1:provider-a', rawMalformed)

    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    // Raw record must NOT be deleted
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v2:user-1:provider-a')).toBe(rawMalformed)
  })

  it('fails closed on wrong required field types and leaves raw entry intact', () => {
    const rawCorruptTypes = JSON.stringify({
      idempotency_key: 12345, // invalid type
      created_at: '2026-09-15',
    })
    localStorage.setItem('dhealth_clinic_pending_patient_v2:user-1:provider-a', rawCorruptTypes)

    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    // Raw record must NOT be deleted
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v2:user-1:provider-a')).toBe(rawCorruptTypes)
  })

  it('fails closed on missing required idempotency key and leaves raw entry intact', () => {
    const rawMissingIdentity = JSON.stringify({
      // idempotency_key is missing
      created_at: Date.now(),
    })
    localStorage.setItem('dhealth_clinic_pending_patient_v2:user-1:provider-a', rawMissingIdentity)

    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    // Raw record must NOT be deleted
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v2:user-1:provider-a')).toBe(rawMissingIdentity)
  })

  it('never deletes a malformed record across repeated reads', () => {
    const rawCorrupt = JSON.stringify({ arbitrary_broken_data: true })
    localStorage.setItem('dhealth_clinic_pending_patient_v2:user-1:provider-a', rawCorrupt)

    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    expect(() => getStoredPendingPatient('user-1', 'provider-a')).toThrow(PendingPatientStorageReadError)
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v2:user-1:provider-a')).toBe(rawCorrupt)
  })
})
