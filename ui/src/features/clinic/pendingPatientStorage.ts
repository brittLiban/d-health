/**
 * Non-PHI storage for unresolved native Clinic patient creation (DH-CLINIC-002A).
 *
 * Requirements:
 * - Persists ONLY technical opaque identity tuple across reload/restart.
 * - ZERO patient PHI: no name, no phone, no contact, no demographics.
 * - Bound strictly to authenticated user identity + active provider scope.
 * - Provider A cannot access Provider B's pending token.
 * - User A cannot access User B's pending token.
 * - 30-minute TTL to prevent stale orphaned tokens from lingering indefinitely.
 * - Cleared on successful reconciliation, explicit abandonment, or logout.
 */

export interface OpaquePendingPatientIdentity {
  source_system: string
  source_identity_system: string
  source_identity_value: string
  created_at: number
}

const STORAGE_PREFIX = 'dhealth_clinic_pending_patient_v1:'
const DEFAULT_TTL_MS = 30 * 60 * 1000 // 30 minutes

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

export function getStoredPendingPatient(
  userId: string,
  providerId: string,
): OpaquePendingPatientIdentity | null {
  const storage = getStorage()
  if (!storage || !userId || !providerId) return null

  try {
    const raw = storage.getItem(makeKey(userId, providerId))
    if (!raw) return null

    const parsed = JSON.parse(raw) as Partial<OpaquePendingPatientIdentity>
    if (
      typeof parsed.source_system === 'string' &&
      typeof parsed.source_identity_system === 'string' &&
      typeof parsed.source_identity_value === 'string' &&
      typeof parsed.created_at === 'number'
    ) {
      // Check TTL
      if (Date.now() - parsed.created_at > DEFAULT_TTL_MS) {
        clearStoredPendingPatient(userId, providerId)
        return null
      }
      return {
        source_system: parsed.source_system,
        source_identity_system: parsed.source_identity_system,
        source_identity_value: parsed.source_identity_value,
        created_at: parsed.created_at,
      }
    }
    // Malformed entry
    clearStoredPendingPatient(userId, providerId)
    return null
  } catch {
    return null
  }
}

export function setStoredPendingPatient(
  userId: string,
  providerId: string,
  pending: Omit<OpaquePendingPatientIdentity, 'created_at'> & { created_at?: number },
): void {
  const storage = getStorage()
  if (!storage || !userId || !providerId) return

  const record: OpaquePendingPatientIdentity = {
    source_system: pending.source_system,
    source_identity_system: pending.source_identity_system,
    source_identity_value: pending.source_identity_value,
    created_at: pending.created_at ?? Date.now(),
  }

  try {
    storage.setItem(makeKey(userId, providerId), JSON.stringify(record))
  } catch {
    // Ignore storage quota / access errors
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
