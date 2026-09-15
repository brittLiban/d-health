/**
 * Non-PHI storage for unresolved native Clinic patient creation (DH-CLINIC-002A).
 *
 * Requirements:
 * - Persists ONLY technical opaque identity tuple across reload/restart.
 * - ZERO patient PHI: no name, no phone, no contact, no demographics.
 * - Bound strictly to authenticated user identity + active provider scope.
 * - Provider A cannot access Provider B's pending token.
 * - User A cannot access User B's pending token.
 * - Never expires an unresolved identity: expiry would make a potentially committed
 *   create unrecoverable and permit a duplicate.
 * - Cleared only once the server has authoritatively resolved the attempt.
 */

export interface OpaquePendingPatientIdentity {
  source_system: string
  source_identity_system: string
  source_identity_value: string
  created_at: number
  /** Set only after the exact bind POST has returned successfully; still non-PHI. */
  confirmed_patient_reference_id?: string
}

export class PendingPatientStorageReadError extends Error {}

const STORAGE_PREFIX = 'dhealth_clinic_pending_patient_v1:'

function makeKey(userId: string, providerId: string): string {
  return `${STORAGE_PREFIX}${userId}:${providerId}`
}

function getStorage(): Storage | null {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage
    }
  } catch {
    // Storage access may be restricted
  }
  return null
}

/** A read failure means we cannot prove that this scoped browser has no pending UUID. */
export function canReadPendingPatientStorage(): boolean {
  const storage = getStorage()
  if (!storage) return false
  try {
    storage.getItem(STORAGE_PREFIX)
    return true
  } catch {
    return false
  }
}

export function getStoredPendingPatient(
  userId: string,
  providerId: string,
): OpaquePendingPatientIdentity | null {
  const storage = getStorage()
  if (!storage || !userId || !providerId) return null

  let raw: string | null
  try {
    raw = storage.getItem(makeKey(userId, providerId))
  } catch {
    throw new PendingPatientStorageReadError()
  }
  try {
    if (!raw) return null

    const parsed = JSON.parse(raw) as Partial<OpaquePendingPatientIdentity>
    if (
      typeof parsed.source_system === 'string' &&
      typeof parsed.source_identity_system === 'string' &&
      typeof parsed.source_identity_value === 'string' &&
      typeof parsed.created_at === 'number'
    ) {
      return {
        source_system: parsed.source_system,
        source_identity_system: parsed.source_identity_system,
        source_identity_value: parsed.source_identity_value,
        created_at: parsed.created_at,
        ...(typeof parsed.confirmed_patient_reference_id === 'string'
          ? { confirmed_patient_reference_id: parsed.confirmed_patient_reference_id }
          : {}),
      }
    }
    // Malformed entry
    clearStoredPendingPatient(userId, providerId)
    return null
  } catch (error) {
    if (error instanceof PendingPatientStorageReadError) throw error
    return null
  }
}

export function setStoredPendingPatient(
  userId: string,
  providerId: string,
  pending: Omit<OpaquePendingPatientIdentity, 'created_at'> & { created_at?: number },
): boolean {
  const storage = getStorage()
  if (!storage || !userId || !providerId) return false

  const record: OpaquePendingPatientIdentity = {
    source_system: pending.source_system,
    source_identity_system: pending.source_identity_system,
    source_identity_value: pending.source_identity_value,
    created_at: pending.created_at ?? Date.now(),
    ...(typeof pending.confirmed_patient_reference_id === 'string'
      ? { confirmed_patient_reference_id: pending.confirmed_patient_reference_id }
      : {}),
  }

  try {
    storage.setItem(makeKey(userId, providerId), JSON.stringify(record))
    return storage.getItem(makeKey(userId, providerId)) === JSON.stringify(record)
  } catch {
    return false
  }
}

export function clearStoredPendingPatient(userId: string, providerId: string): void {
  const storage = getStorage()
  if (!storage || !userId || !providerId) return

  try {
    storage.removeItem(makeKey(userId, providerId))
  } catch {
    // Ignore storage errors
  }
}

export function clearAllStoredPendingPatientsForUser(userId: string): void {
  const storage = getStorage()
  if (!storage || !userId) return

  const userPrefix = `${STORAGE_PREFIX}${userId}:`
  try {
    const keysToRemove: string[] = []
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (key && key.startsWith(userPrefix)) {
        keysToRemove.push(key)
      }
    }
    for (const key of keysToRemove) {
      storage.removeItem(key)
    }
  } catch {
    // Ignore errors
  }
}

export function clearAllStoredPendingPatients(): void {
  const storage = getStorage()
  if (!storage) return

  try {
    const keysToRemove: string[] = []
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (key && key.startsWith(STORAGE_PREFIX)) {
        keysToRemove.push(key)
      }
    }
    for (const key of keysToRemove) {
      storage.removeItem(key)
    }
  } catch {
    // Ignore errors
  }
}
